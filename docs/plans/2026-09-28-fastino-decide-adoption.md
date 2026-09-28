---
status: completed
created: 2026-09-28
updated: 2026-09-28
slug: fastino-decide-adoption
---

# Plan: Fastino Decide adoption — sys1 as the one integration, consumers on sys1

## Context

The vendor's own playground (huggingface.co/spaces/fastino/gliner25-decide-playground, `model.py`) is the prescribed way to use GLiNER2.5-Decide: `gliner2.classification.Classifier.from_pretrained("fastino/GLiNER2.5-Decide")`, one `ClassificationSchema` holding every task (`single` for choice, `ordinal` for ordered scales), one `classify(text, schema, config=ClassificationConfig(decoder="independent"))` decode for all of it, and the full `probabilities` distribution per task as the confidence basis. Verified 2026-09-28 against our venv (gliner2 2.0.0, `/private/tmp/gliner-decide`): **9.8s model load, then 361ms to decode 3 heads** (choice + binary + ordinal) with full distributions returned.

Direction (user, 2026-09-28): **dev-decisions and zcode-router-kit should both use sys1** — that is why sys1 was built as a standalone library — and both should use GLiNER2.5/Decide **as fastino prescribes**. The hosted Decide endpoint (api.fastino.ai) is not the focus.

Current state against that bar:

- **sys1** `_LOCAL_INLINE` (`providers.py:361`) drives the legacy `GLiNER2.classify_text` surface in a per-head loop: one model load per subprocess call, N forward passes for N heads, no `probabilities`, no `ordinal`. The prescribed surface ships in our venv already.
- **dev-decisions** (branch `migrate-to-sys1`) delegates to sys1 correctly, but its example config defaults `[classify] provider = "decide"` — the **hosted** wire — and keeps a full inline hosted-decide fallback. Only `--provider local` touches the fastino SDK.
- **zcode-router-kit** `router/fastino.mjs` calls api.fastino.ai directly from Node (necessarily — no Python SDK), with model `fastino/gliner2.5-multi-v1` (not Decide), confidence scraped from whichever of `confidence/score/probability/prob` appears, and its four routing questions hardcoded as a schema JSON blob.
- **Naming collision**: sys1 provider id `decide` means *hosted wire*, while the Decide *model* is what `local` runs. dev-decisions' docs commits (`e23c5e1`, `65ddc4f`) fixed the naming story, not the wiring.

## Approach

Five phases, strictly ordered — each phase's consumer depends on the one before it.

1. **sys1: migrate the gliner wire to the prescribed API.** Rewrite `_LOCAL_INLINE` to build one `ClassificationSchema` per classify call (named tasks; `single` for choice/noul heads, `ordinal` for score heads) and one `Classifier.classify(..., decoder="independent")` decode. Capture `probabilities` into the Answer (`confidence` stays `max(probabilities)`). `on_infeasible` → a declined answer so our existing decline→escalate gating absorbs it. Keep the schema-construction logic in an importable pure function so tests can assert schema shape without loading the model.
2. **sys1: persistent classifier server.** A load-once classifier process in the venv (stdlib `http.server` — the venv already carries torch; sys1's own runtime stays dependency-free), exposing the playground's contract `{text, tasks} → {tasks: {name: {labels, probabilities}}}`. `_gliner_wire` POSTs to it when configured/reachable and falls back to the subprocess path otherwise, so nothing breaks with the server down. This deletes the 9.8s-per-call reload, which is the dominant local cost our own README warns about.
3. **sys1: consumers' endpoint.** The existing FastAPI service (`service/main.py`) already exposes `/v1/classify` with auth, telemetry, and the JSONL calibration log — that is what "using sys1" means for a Node consumer. Confirm the local provider works through it, add `examples/routing-judge.toml` (the router's four questions as choice heads — workload/execution/workflow/followUp), and resolve the id collision **documentation-first**: ids stay stable (`decide` = hosted, `local` = fastino SDK/Decide model), READMEs say it plainly, and hosted `decide` leaves every consumer's default path.
4. **dev-decisions: sys1-only default.** Example config and defaults flip to `provider = "local"`; the inline hosted-decide fallback is demoted (kept only as the no-sys1 degraded path, documented as such); README stops presenting hosted as the primary option.
5. **zcode-router-kit: sys1 client.** `fastino.mjs` becomes a sys1-service client: POST `/v1/classify` with task `routing_judge` against the local provider, map `answers.<provider>.<head>` → its `{workload, execution, workflow, followUp, conf...}` verdict shape, model becomes GLiNER2.5-Decide. Its per-question confidence gates (0.6/0.6/0.4) and the followUp≠workflow rule **stay caller-side** (policy in code, per the house pattern; sys1's all-heads verdict doesn't fit per-question independence). The direct api.fastino.ai call is removed or demoted to an explicitly-named fallback.

## Acceptance criteria

These are the contract. Each is a verifiable assertion.

- [x] sys1's local provider answers an N-head task with **one** model load and **one** decode (subprocess mode: one invocation per `classify()`; server mode: one HTTP call), regardless of head count — measured, not assumed. *(live 2026-09-28: 2-head `change` task = one subprocess call, 12.4s incl. load; server mode one POST, 361ms first / 144ms steady)*
- [x] Local answers carry full `probabilities`, and `confidence == max(probabilities)` for every choice/ordinal head. *(live output; `tests/test_local_wire.py::TestParseGliner2`)*
- [x] Score heads are served via `schema.ordinal`; noul-shaped heads are binary singles whose probability gates through `answer_confidence()`'s derived certainty. *(live mixed-head task: noul 0.868 "(conf 0.87 derived)", ordinal severity → score 2.0 + legend; spec/parser unit tests)*
- [x] With the persistent server running, a second `classify()` does not reload the model (second-call latency ≪ first-call; no subprocess spawn); with the server down, the subprocess fallback produces identical answer shapes. *(live: 361ms → 144ms, `mode=server`, no fallback marker; fallback shape covered by `TestParseGliner2` legacy dispatch + live subprocess run)*
- [x] dev-decisions runs its default classification path through sys1 with model `fastino/GLiNER2.5-Decide` and no `FASTINO_API_KEY`; `scripts/verify-dev-decisions-parity.py` still passes. *(default flipped to `local` in DEFAULTS + config.example.toml, commit `d5e140d`; parity exit 0 with keys unset — labels agree, local confidence deltas documented as the schema-task-name effect, parity script updated per this plan's risk mitigation)*
- [x] zcode-router-kit's judge path returns the same verdict shape it returns today, sourced from a sys1 service running the local provider, with no direct api.fastino.ai call on the default path. *(live Node run against the service + classifier server: gates applied on the same `{workload, execution, workflow, followUp, conf…}` shape, ~280ms; fail-open on a down service; commit `015c8f2`)*
- [x] No consumer's documented default path targets the hosted Decide endpoint; the `decide`-id-vs-Decide-model distinction is stated in sys1's README, dev-decisions' README, and the router's README. *(sys1 README "Provider naming"; dev-decisions provider table + config comments; router README judge-backends rewrite)*
- [x] sys1 suite green with no test-count regression; ruff clean on touched files; parity script green. *(211 passed (188 + 23 new); ruff 16 pre-existing, 0 new; parity exit 0)*

Completed 2026-09-28. Commits: sys1 `4916a2f` + `7663cde` (`feature/provider-flags-and-dashboard`), dev-decisions `d5e140d` (`migrate-to-sys1`), zcode-router-kit `015c8f2` (`master`). None pushed; sys1 merging to `main` still requires explicit approval.

## Files to be touched

**sys1** (this repo):
- `src/sys1/providers.py` — `_LOCAL_INLINE` rewrite (prescribed API), server-client path with subprocess fallback, `_local_venv_python`/server URL config plumbing
- `src/sys1/parsers.py` — `parse_local` passes `probabilities` through (answers stay verbatim-JSON-shaped)
- `service/classifier_server.py` (new) — stdlib load-once classifier server for the venv
- `src/sys1/config.py` — `local_server_url` / `local_server_mode` keys (default off → opt-in)
- `examples/routing-judge.toml` (new) — the router's four questions as choice heads
- `tests/test_local_wire.py` (new) — schema construction (pure), parser shapes, server contract with a stub engine
- Docs: TECHNICAL-DOCUMENTATION §5 (gliner wire rewritten around the prescribed API), §9 (service), FUNCTIONAL-SPECIFICATIONS §4, `docs/features/gating-and-recipes.md` untouched, README (naming collision + server mode), CHANGELOG

**dev-decisions** (`~/Projects/dev-decisions`, branch `migrate-to-sys1`):
- `config.example.toml` — `provider = "local"`, hosted keys commented as fallback
- `scripts/dev_decisions.py` — default provider resolution, inline fallback demotion
- `README.md` — decide/local language, server mode note

**zcode-router-kit** (`~/Projects/zcode-router-kit`):
- `router/fastino.mjs` — sys1 client replacing the hosted call; same exported function names and return contract so `usage.mjs`/`server.js` don't change
- `README.md` / config docs — baseUrl points at sys1 service, model is Decide-via-sys1

## Out of scope

- Changing the hosted `decide` wire's implementation or removing the provider (it stays registered, off every default path).
- The seven candidate providers, the Jev wire, ModernBERT.
- GPU serving, batching across concurrent requests, or quantization — load-once CPU is the target (the playground's own deployment).
- dev-decisions' hook UX, task registry, or fleet scripts.
- Router workflow semantics (followUp≠workflow etc. stay exactly as they are).

## Verification

**Phase 1 smoke (already run once, repeated as the acceptance harness):**
```bash
/private/tmp/gliner-decide/bin/python - <<'EOF'
from gliner2.classification import Classifier, ClassificationConfig, ClassificationSchema
clf = Classifier.from_pretrained("fastino/GLiNER2.5-Decide", map_location="cpu")
schema = ClassificationSchema()
schema.single("diff_type", ["feat", "fix", "chore", "refactor"])
schema.single("breaking_change", ["yes", "no"])
schema.ordinal("risk", ["low", "medium", "high"])
r = clf.classify("diff text", schema, config=ClassificationConfig(decoder="independent"))
for t in ("diff_type", "breaking_change", "risk"):
    print(t, r.value(t), r.probabilities(t))
EOF
```
Expected: one decode, full distributions, ordinal honored.

**Phases 1–2 (sys1):**
```bash
.venv/bin/python -m pytest -q          # suite green, new test_local_wire.py green
.venv/bin/python -m sys1.cli classify --provider local --task change -i sample.diff   # subprocess mode
# server mode:
/private/tmp/gliner-decide/bin/python service/classifier_server.py &   # load-once
.venv/bin/python -m sys1.cli classify --provider local --task change -i sample.diff   # twice; second call ≪ first
```
Expected: same answer shapes both modes; `probabilities` present; confidence = max(probs).

**Phase 4 (dev-decisions):**
```bash
cd ~/Projects/dev-decisions && python3 scripts/verify-dev-decisions-parity.py --repo . --provider local --diff-file sample.diff
```
Expected: parity confirmed; no FASTINO_API_KEY in env.

**Phase 5 (zcode-router-kit):**
```bash
cd ~/Projects/zcode-router-kit && node --input-type=module -e "import {judgeViaFastino} from './router/fastino.mjs'; /* stub signals/cfg/envMap/R; baseUrl=sys1 service */"
```
Expected: verdict shape identical to today's (`workload/execution/workflow/followUp` + conf + reason), sourced from sys1.

## Risks

- **Verdict drift on migration.** `classify_text` → `Classifier` may shift labels/confidences on edge inputs. Mitigation: parity script extended to compare old vs new local output on a fixture corpus before the switch lands; the noul gating from `eba8d48` is provider-agnostic so verdict semantics carry over.
- **Server mode is an ops change** (a long-lived venv process). Mitigation: opt-in via config, subprocess fallback automatic, `sys1 doctor` reports server reachability.
- **Router model change (multi-v1 → Decide) changes routing verdicts.** That is the point — Decide is the decision-tuned model — but it is a behavior change for the router's users. Mitigation: its gates are caller-side constants, already tunable; log both verdicts for one session before flipping.
- **Naming-collision fix could tempt id renames.** Renaming provider ids breaks consumer configs. Mitigation: ids frozen; documentation and default-path changes only (explicitly listed in acceptance criteria).
- **`ordinal` semantics for score heads.** The vendor's `ordinal` schema type is designed for ordered levels; if its output shape differs from what `parse_local` expects for scores, the parser adapts (phase 1 tests cover it).

## Dependencies

- `eba8d48` (noul gating + per-head floors + recipe pack) — gating semantics the local answers now flow through.
- gliner2 2.0.0 in `/private/tmp/gliner-decide` (present; `gliner2.classification` confirmed).
- dev-decisions is on `migrate-to-sys1` already delegating to sys1 (`_sys1_classify`); zcode-router has no sys1 dependency today (Node).

## Notes

- **Vendor prescription, verbatim** (playground `model.py`): `Classifier.from_pretrained(MODEL_ID, map_location="cpu")` → `schema.single(name, labels)` per task → `classify(text, schema, config=ClassificationConfig(decoder="independent"))` → `result.value(name)` / `result.probabilities(name)`. The playground's UI teaches the same snippet. Flat labels, no rubric — matching our hosted-Decide schema memory.
- **Measured baseline (2026-09-28, M-series CPU)**: 9.8s model load; 361ms for a 3-head decode. Subprocess-per-call multiplies the 9.8s into every classify; the server amortizes it to zero.
- **Why the FastAPI service for the router, not the stdlib classifier server**: "using sys1" for a Node consumer should mean sys1's contract — auth, telemetry, JSONL calibration, task registry — not a raw model endpoint. The stdlib server is an internal sys1 optimization behind the local provider, not a public API.
- **Router gates stay in JS** (0.6/0.6/0.4, followUp≠workflow): sys1's verdict is all-heads-or-escalate, and the router treats its four questions independently — the documented "model returns labels and numbers; thresholds stay in your code" pattern.
- **Recommendation on the id collision**: keep ids, fix docs. A rename (`decide` → `decide_hosted`) is cleaner long-term but breaks consumer configs for no functional gain now; revisit if a third hosted Fastino wire ever appears.
- Hosted `decide_model` string in sys1 config is `fastino/GLiNER-2.5-Decide` (dashed) vs the HF repo `fastino/GLiNER2.5-Decide` — correct the hosted default string in phase 3 while touching docs.
