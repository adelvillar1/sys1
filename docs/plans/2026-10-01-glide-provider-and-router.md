---
status: completed
created: 2026-10-01
updated: 2026-10-01
slug: glide-provider-and-router
---

# GLiDE provider + route() — a cheap hosted Jev-class default, then a size-aware routing policy on the cascade

## Context

Fastino's GLiDE (`fastino/glide`, https://docs.fastino.ai/concepts/decision-models) is a Jev/Drex-class decision model on the same `POST /v1/systemone` wire: one `state`, typed questions (`noul`/`choice`/`score`), calibrated probabilities. Evaluated 2026-10-01 with six live probes (all HTTP 200, including the user's own curl snippet). Pricing is $0.00 input / $0.04 per 1M output tokens — effectively free — with a **40,000-token rendered-prompt limit** and ≤255 options.

Two problems it solves, and one it clarifies:

1. **No cheap hosted default.** The hosted Jev-class roster (jev, drex) is pay-per-call; the local GLiNER venv is unreliable in this setup and is excluded from routing. GLiDE at ~free pricing becomes the natural cascade head.
2. **No size-aware selection.** `cascade_classify` takes a static chain; a single default either pays Drex for everything or truncates long inputs. GLiDE's 40K bound vs Drex's 131,072 makes "glide unless long, then drex" a real, deterministic routing signal — expressible only by a router.
3. **It removes the cost argument for local.** With a ~free hosted Jev-class provider, the unreliable local venv no longer needs to be a routing candidate at all (it stays registered, manually invocable, never auto-routed).

Sequencing is deliberate: **phase 1 wires and calibrates glide; phase 2 adds `route()`** — the cascade's contract says the first provider must be well-calibrated for the task, so the router's ordering premise must exist before the router lands.

Live-verified contract (2026-10-01 probes — the evidence base for every delta below):

- **Auth**: both `Authorization: Bearer` and `X-API-Key` accepted on /v1/systemone → `http_post_json` unmodified.
- **state**: plain string, Jev-style `{"text": ...}`, and object forms all accepted. sys1 sends a plain string (Drex style).
- **Strict validation** (Drex-strict, not Jev-lenient): an extra `id` field per question → 422 `extra_forbidden`; noul criteria keys must be `true`/`false` (`yes`/`no` → 422). **`Head.to_drex_head()` is a drop-in question mapper.**
- **Score is 0-indexed** like Drex: obvious-lowest → `score: 0`, obvious-highest → `score: 4` on a 5-level scale; `expected_level` = Σ index × p; `probabilities` and `legend` keyed `"0".."N-1"` (legend is a **dict**, where Drex sends an array).
- **Confidence on every answer kind**: noul `|2p−1|`, choice/score `top1−top2` margin — a different scale from the Jev-family derived `max(p, 1−p)` (p=0.85 → 0.70 explicit).
- **Envelope**: `answers` keyed by question name; `usage.input_tokens/output_tokens` + top-level `token_usage` int; echoes `model: "glide"`.
- **Latency**: 1.1–1.7s warm, ~7.8s on several calls — fine under `request_timeout_seconds` = 30.

Key handling: `FASTINO_API_KEY` currently lives in `~/Projects/TTESS/.env` (NOT in sys1/.env or the shell). Copy it into `<repo>/.env` (gitignored) exactly like `DREX_API_KEY`.

## Approach

### Phase 1 — glide provider (the Drex pattern)

A dedicated `_glide_wire` (house pattern: one wire factory per vendor, deltas in its docstring):

1. **`state` is a plain string** bounded by new `providers.glide_max_chars` (default 100,000 ≈ 2.5 chars/token under the 40,000-token rendered-prompt limit) — NOT `scan.max_diff_chars`; 40K tokens is the feature, 128K-scale inputs are not.
2. **Questions via `to_drex_head()`** — verified live: no `id` field, noul criteria under `true`/`false`. No new head mapper in types.py.
3. **`parse_glide`** (parse_drex skeleton, two deltas):
   - Score: same `score_min` shift as parse_drex, but legend arrives as an index-keyed **dict** → shift the keys (`"0"` → `"1"` on a 1..5 scale) instead of converting an array. Keep `expected_level` as a ride-along extra on the Answer (calibration dashboards may want it).
   - Confidence policy: **keep** GLiDE's explicit confidence on choice/score (documented top1−top2 margin — same flavor as the Jev family per gating.py's docstring); **strip** noul confidence (`{"type": "noul", "noul": p}`) so gating derives `max(p,1−p)` family-consistently — otherwise the `|2p−1|` scale makes the shared 0.7 floor stricter than jev/drex for the same answer.
4. **Registration**: tier `core`, `wire="jev"`, `classify=_glide_wire("glide")`, `_hosted_available`; add `"glide"` to `_requires_key`'s core tuple; `gating._DEFAULT_FLOORS["glide"] = 0.7` (Jev-family semantics via the strip decision); extend the `heads_for` jev-inheritance line in tasks.py to `provider in ("drex", "glide")`.
5. **Telemetry**: `usage.input_tokens/output_tokens` → `token_prompt/completion/total` (same mapping as `_drex_wire`); no `evaluation_time_ms`/`request_id` on this wire.
6. **Calibration gate (closes phase 1)**: run the three-way label comparison glide vs jev vs drex on the standard corpus (parity-script corpus); record agreement + confidence deltas. The strip-noul-confidence default stands unless the corpus shows margin semantics calibrate better — in which case parse_glide keeps the explicit field and glide gets its own floor. The decision and evidence land in this plan's Outcome section.

Config: `glide_api_url` (https://api.fastino.ai/v1/systemone), `glide_model` (pinned `fastino/glide`), `glide_api_key_env` (FASTINO_API_KEY), `glide_max_chars` (100_000).

### Phase 2 — route() (deterministic policy, not an adaptive router)

New `src/sys1/routing.py`: `route(task, text, cfg) -> list[str]` — a pure function whose output feeds the existing cascade. No quota tracking, no adaptive scoring: decision calls are low-volume and near-free at the head of the chain; routing quality lives in the floors and the parity harness, not in model selection (the zcode-router-kit regime — quota-aware, high-volume — does not transfer).

1. **Chain source**: `providers.route.by_task[task.id]` if set, else `providers.route.default_chain` — default `"glide,drex,jev"`, hosted-only by design (local/modernbert deliberately absent; see Context #3).
2. **Size gate**: `len(text) > providers.route.long_input_chars` (default = `glide_max_chars`) → reorder so providers whose documented bound can't take the input go last (glide behind drex). Derives from the provider's own bound — no new magic number.
3. **Availability filter**: drop providers where `providers.is_available(pid, cfg)` is false (existing machinery). An unavailable provider never silently enters a chain.
4. **Task allow-list wins**: `Task.providers` (e.g. `_SAFETY`) remains a hard filter, preserving chain order.
5. **Integration**: one line in `cascade_classify` — `chain = provider_chain or routing.route(resolved_task, text, cfg)`. Explicit chains, `classify()`, and direct `call_provider` usage bypass routing entirely.
6. **Auditability**: the route decision rides into telemetry as **added keys only** — `route_chain`, `route_reason` (`default` | `task` | `long-input` | `availability-filtered`) — so JSONL consumers never migrate.
7. **Escape hatch**: `providers.route.enabled = false` restores the legacy `["decide", "jev"]` default.

## Acceptance criteria

- [ ] `_glide_wire` sends `state` as a plain string bounded by `glide_max_chars`, model/url/key from config, and questions via `to_drex_head()` containing no `id` field and `true`/`false` noul criteria (body asserted with urlopen mocked)
- [ ] `parse_glide` maps choice→`label`/`confidence`/`probabilities`, noul→`{"type": "noul", "noul": p}` with no confidence field, score→`score_min`-shifted float with legend dict keys shifted and `expected_level` preserved
- [ ] Missing answers become `declined`; GLiDE's 422 error envelope surfaces as a RuntimeError via `http_post_json`
- [ ] Telemetry keys `http_status`, `latency_ms`, `retries`, `structured_ok`, `token_prompt/completion/total` are populated from `usage.input_tokens`/`output_tokens` (mock 152/1)
- [ ] `glide` is registered tier="core", wire="jev"; `providers --all`, doctor, and the dashboard show it when `FASTINO_API_KEY` is set; `_requires_key` covers glide
- [ ] `heads_for(task, "glide")` returns the jev head set wherever a task defines one
- [ ] `gating._DEFAULT_FLOORS["glide"]` exists with Jev-family semantics documented
- [ ] `FASTINO_API_KEY` copied into repo `.env` (gitignored); a live `sys1.classify('glide', 'change', ...)` matches the raw-curl probe results (label agreement)
- [ ] Three-way calibration (glide/jev/drex) recorded in this plan's Outcome with label agreement + confidence deltas; the noul confidence-policy decision (strip vs keep) is confirmed or flipped with evidence
- [ ] `route()` unit-tested for: default chain on short input, long-input reorder, availability filtering, `Task.providers` hard filter, `by_task` override, and `route.enabled = false` fallback
- [ ] `cascade_classify` uses `route()` only when `provider_chain` is None; explicit chains bypass it
- [ ] Route decisions appear in telemetry as added keys only (`route_chain`, `route_reason`)
- [ ] The default chain is hosted-only (`glide,drex,jev`) — `local`/`modernbert` absent from auto-chains
- [ ] Full suite green; ruff clean for all new code

## Files to be touched

- `src/sys1/providers.py` — `_glide_wire` + register + `_requires_key`
- `src/sys1/parsers.py` — `parse_glide`
- `src/sys1/config.py` — `glide_*` and `route.*` defaults
- `src/sys1/gating.py` — floor entry
- `src/sys1/tasks.py` — `heads_for` glide→jev inheritance (one line)
- `src/sys1/routing.py` — new (phase 2)
- `src/sys1/api.py` — `cascade_classify` default chain (phase 2, one line)
- `tests/test_providers.py`, `tests/test_parsers.py`, `tests/test_routing.py` (new)
- `TECHNICAL-DOCUMENTATION.md` — provider registry table, telemetry keys, route section
- `README.md` — provider table, if it lists one
- `.env` — `FASTINO_API_KEY` copy (gitignored, never committed)

## Out of scope

- Quota tracking, adaptive scoring, delegation suggestions (zcode-router-kit's regime — wrong economics for decision calls)
- Routing second-brain scripts through sys1 (they call Drex directly with their own client; a separate decision)
- Fixing local venv reliability — `local` stays registered and manually invocable, never auto-routed
- `state` as structured objects on the glide wire (acceptance verified by probes; sys1 sends text)
- Multi-label primitives; dev-decisions-side changes
- Making glide the default for any consumer gate beyond the route default (adoption per task remains task-level config)

## Verification

- `.venv/bin/python -m pytest -q` — full suite green
- `.venv/bin/ruff check .` — clean for new code
- `SYS1_PROVIDERS__ENABLED=core .venv/bin/python -m sys1.cli providers --all | grep glide` — `[on] glide jev configured → available`
- Live single-provider: `set -a && . ./.env && set +a && .venv/bin/python -c "import sys1; r = sys1.classify('glide', 'change', 'Help! My payouts have been failing for 3 days.'); print(r.answers)"` — parses, verdict computed
- Live cross-check vs probe: the rollback/hotfix/wait state from the user's curl snippet through `cascade_classify` → `rollback` wins
- Phase 2: `route('change', short_text)` → glide-first; `route('change', 'x' * 150_000)` → drex-first; `cascade_classify(task='change', text=short)` telemetry shows `route_chain`/`route_reason`
- `scripts/verify-dev-decisions-parity.py` — still exit 0 (glide/route additions leave the decide/jev path untouched)

## Linked artifacts

- `TECHNICAL-DOCUMENTATION.md` — provider registry table + telemetry keys + routing section
- `docs/recaps/` — session recap on implementation (housekeeping rule 5)
- Evaluation evidence: 2026-10-01 session — six live probes, contract quoted in Context; probe payloads reusable as test fixtures

## Outcome (2026-10-01) — per-criterion evidence

1. **_glide_wire** (`providers.py`) — plain-string state bounded by `glide_max_chars` ("abcd" at 4, `TestGlideProvider::test_state_truncated_by_glide_max_chars`); model/url/key from config; questions via `to_drex_head()` with no `id` field and `true`/`false` noul criteria (asserted in `test_call_glide_wire_and_usage`); 422 envelope → RuntimeError (`test_422_error_envelope_raises`); no `evaluation_time_ms` on this wire.
2. **parse_glide** (`parsers.py`) — choice keeps label/confidence/probabilities; noul confidence stripped (0.9977/0.9954 probe shape → no confidence field); score 0-indexed → `score_min` shift (0 → 1.0 on a 1..2 scale) with legend dict keys shifted ("0"/"1" → "1"/"2"); `expected_level` preserved; missing → declined; the 2026-10-01 live-probe response shapes are captured verbatim in `TestParseGlide::test_live_probe_shapes`.
3. **Telemetry** — `usage.input_tokens/output_tokens` mapped to `token_prompt/completion/total` (mock 152/1/153 asserted; live classify: 1528/465/1993).
4. **Roster** — tier `core`, `wire="jev"`, `_requires_key` core tuple includes glide; `providers --all` shows `[on ] glide  jev  configured → available` (key sourced from repo `.env`); `provider_config_view` url-kind with `FASTINO_API_KEY`.
5. **Floor** — `gating._DEFAULT_FLOORS["glide"] = 0.7` with Jev-family semantics documented.
6. **heads_for** — `heads_for(task, "glide")` returns the jev change-head set (4 heads incl. both nouls), asserted equal to jev's id list (`test_heads_for_inherits_jev_set`).
7. **Key** — `FASTINO_API_KEY` copied from `~/Projects/TTESS/.env` into repo `.env` (gitignored).
8. **Live single-provider** — `sys1.classify('glide', 'change', 'Help! My payouts have been failing for 3 days.')`: 4 heads parsed, diff_type=fix 0.72, risk_tier=high 0.67, noul 0.20/0.29 (no confidence fields), 1993 tokens, 6.1s, structured_ok. Cross-check vs the raw curl probe: the rollback scenario through the sys1 wire → `rollback` p=0.99854 vs raw curl p=0.99857 — label agreement.
9. **Calibration (6-text change corpus, log=False; /tmp/glide_calibration.json)** — glide-vs-jev label agreement **6/6 diff_type, 5/6 risk_tier**; glide-vs-drex 3/6 and 4/6 (drex is the outlier on this corpus); noul p tracks jev within ~0.05 (0.14/0.15, 0.74/0.84, 0.81/0.79); glide's choice/score margins run systematically lower than Jev's concentration statistic (e.g. 0.63 vs 0.96 on t1). **Noul confidence-policy decision CONFIRMED: strip stands** — noul semantics are family-consistent; choice/score margins are kept as returned, and consumers wanting margin-scale floors have the per-provider `glide_confidence_floor` config key. Latency: glide 1.4–17.5s vs jev 0.17–0.35s / drex 0.25–0.37s — glide is the cheap default, not the latency pick.
10. **route()** (`routing.py`) — 9 policy tests (default chain, size gate, `long_input_chars` override, by_task override, task allow-list hard filter via _SAFETY, availability filter, disabled legacy, unknown-id filtering, chain-only wrapper) + 4 cascade integration tests (routed default, explicit bypass, disabled legacy chain, `route_chain`/`route_reason` in `events.jsonl`).
11. **Live routing** — short input routes `[glide, drex]` (jev dropped: no TYPESAFE key in that shell); a ~540K-char input routes `[drex, glide]`; live cascade on a short input walked `glide → escalate → drex won` (`cascade[glide,drex]`).
12. **Parity** — `scripts/verify-dev-decisions-parity.py --repo ~/Projects/dev-decisions --provider local` → "✓ parity confirmed", exit 0.
13. **Tests** — 276 passed (261 before the routing tests landed), ruff clean for all new code; the 16 long-standing pre-existing errors were cleared later in the same session (see the session recap) — `ruff check .` is now at 0.

Incidental fix: `load_config` now deep-copies `DEFAULTS` — the nested `providers.route` dict exposed that nested sections were shared by reference across `load_config()` calls, so a caller mutating `cfg["providers"]["route"]` would have leaked into every later load (caught by test isolation).
