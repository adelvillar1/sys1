---
status: completed
created: 2026-09-28
updated: 2026-09-28
slug: jev-cookbook-adoption
---

# Plan: Jev cookbook adoption — skills, self-consistency, semantic find, wire rules

## Context

The 2026-09-28 pass over the TypeSafe cookbooks (systemonemodels.org/examples/cookbooks → docs.typesafe.ai) surfaced a layer we have not absorbed: the vendor's official **model-limitation** material ("Jev 1.13 jaggedness") and four patterns beyond this morning's recipe pack — self-consistency, semantic find, function calling via closed sets, and explanation-noul alignment. Two external references matter: **dbreunig/building-with-jev-skill** (142★), the best existing task-design-craft skill for Jev, and the official confidence page, which frames confidence as a convenience statistic ("never locked into our definition") — vendor confirmation of our per-provider floor design.

The load-bearing findings:

- **Jev 1.13 documented jagged edges** (with vendor workarounds): literal reading of instructions; cannot count; weak numerics — score levels are weakly calibrated as numbers; reads dates as text; multi-hop indirection costs accuracy; large irrelevant state is a distractor ("context rot"); **does not treat state as hostile** (no injection defense — our guardrail recipe exists because of this); criteria must point the same direction as instructions; **no structural invariants** (P(x) vs 1−P(not x) is not guaranteed; a noul and a choice can disagree). The invariants point is vendor confirmation of two of our design rules: gate noul on its own probability (`answer_confidence`'s derived certainty) and never port thresholds across primitives/providers.
- **Self-consistency** (noul + choice cookbooks): repeat independent draws; TypeSafe's per-question probability stdev was an order of magnitude tighter than LLM judges'; a 0.30–0.70 uncertainty band routes to human review — explicitly *illustrative, not calibrated*, to be fit from labeled data (our JSONL stream exists for exactly this).
- **Semantic find**: rank lines with a Choice over line-IDs (`L001…`, up to 255 options/request), and in the *same request* ask an existence Noul — ranking tells you where to look, the noul tells you whether it answers (their example: top line 0.86 relevance, 0.14 existence → correctly flagged absent). Thresholds FOUND=0.7 / ABSENT=0.35 as tuneable constants.
- **Function calling / entity alignment / skill suggestion**: closed-set dispatch with per-argument `stated` nouls and min-composition confidence; ordered Score over outcomes + explanation nouls for the human queue; skim-everything-then-scrutinize-top-3 with either step allowed to come back empty. These are design vocabulary for skills and future work, not library code.
- **Confidence page**: three-range pattern (act / proceed-cautiously / human); thresholds scale with consequence — already implemented as head floors + guardrail tiers. Nothing to build; worth citing.

## Approach

Three workstreams, ordered by leverage per unit of work.

**A. typesafe-jev skill upgrade** (the skill lives at `~/.agents/skills/typesafe-jev/SKILL.md` — outside any git repo; changes there have no version control, so the recap is the only trace). Extend the skill with three sections and an index refresh — absorbing dbreunig's craft and the vendor's jaggedness page rather than installing a second skill:

1. *Jagged edges (official, jev-1.13)* — all nine limitations with the vendor workaround for each, written as rules for task authors, plus the explicit "no structural invariants" warning and what it justifies in our gating.
2. *Task design* — one property per question; instructions-object keys (`question`/`focus`/`inspect`/`compare`/`field`); contrastive criteria (`what`/`not_for`/`examples`); score levels as standalone situations with no numerals; add `other` to open Choice sets; a noul of 0.5 means unsure, not medium; crisp noul conditions; keep decision policy in code; minimal structured state with named paths (`ticket.messages[0].text`); compute all arithmetic, counting, and date math in code.
3. *Diagnosis* — the symptom→cause→fix table and the revision rules (change one or two questions per revision; judge on labeled data, not confidence; keep the answer space stable once code depends on it).
4. *Cookbook index* — add the new paths (`/model-jaggedness/jev-1.13`, `/confidence`, `/cookbooks/consistency_noul_cookbook`, `/cookbooks/consistency_choice_cookbook`, `/cookbooks/semantic_find`, `/cookbooks/date_extraction_cookbook`, `/cookbooks/pre_parsed_value_extraction_cookbook`, `/cookbooks/function_calling`, `/cookbooks/entity_alignment`, `/cookbooks/skill_suggestion`) and a pointer to dbreunig's skill as complementary reading.

**B. sys1: self-consistency helper + semantic-find recipe.**

1. `sys1.consistency_classify(provider, task, text, *, n=5, cfg=None, log=True, low=0.30, high=0.70)` in `api.py`, exported from `__init__`. Runs `n` independent `classify()` draws (provider-agnostic; each draw is a normal call, so JSONL records keep the existing shape — no new log contract). Returns a plain dict per the library's style: `{"task", "provider", "n", "heads": {head_id: {"samples": [...], "mean", "stdev", "band"}}}` where `band` maps the per-head signal — a noul's probability, or a choice winner's max probability — to `"no" / "uncertain" / "yes"` at the inclusive band edges. Constants documented as illustrative starting points; the docstring points at `record_feedback`/`calibration_curve` for fitting them.
2. `examples/semantic-find.py` — the cookbook pattern over `sys1.Task` objects: tag lines `L001…`, one `make_choice` head over line IDs + one `make_noul` existence head, a single `classify()` per query, ranked lines above a relevance floor, verdict from `FOUND=0.7`/`ABSENT=0.35` constants. Provider-swappable (jev, decide, local Decide wire — the same recipe exercises our new prescribed local path). Smoke-tested in `tests/test_recipe_pack.py` with the stub provider, per the recipe-pack idiom.
3. 429 retry: `http_post_json` currently retries 5xx + warming errors only. Jev documents 429 rate-limiting (1,200 req/min) — add 429 to the retryable statuses with the same backoff. Telemetry keys unchanged; guardrails still fail closed on exhaustion.
4. Docs: TECHNICAL-DOCUMENTATION §5 gains the jaggedness wire rules (including the score-numerics caveat: threshold/rank/round scores, never reconstruct a magnitude — and the note that derived noul certainty is our routing convention, not a model-verified invariant); FUNCTIONAL-SPECIFICATIONS §2 gains a short "Consistency classification" subsection; README mentions `consistency_classify`; CHANGELOG entry; STATE-SNAPSHOT + CLAUDE.md test-count refresh at close.

## Acceptance criteria

These are the contract. Each is a verifiable assertion.

- [x] `typesafe-jev/SKILL.md` has a jaggedness section covering all nine documented edges with vendor workarounds, including the structural-invariants warning and what it justifies in our gating.
- [x] The skill has task-design guidance (instructions-object keys, contrastive criteria, standalone score levels without numerals, `other` option, noul-0.5 semantics, state minimalism with named paths) and the symptom→cause→fix diagnosis table with the revision rules.
- [x] The skill's cookbook index lists the new cookbook paths and the confidence page, plus a pointer to dbreunig's skill as complementary.
- [x] `sys1.consistency_classify(...)` returns per-head `samples`/`mean`/`stdev`/`band`, routes the inclusive 0.30–0.70 band to `"uncertain"`, works for noul and choice heads, and — with `log=True` — emits ordinary per-draw classify records (no new JSONL shape).
- [x] A choice head's banding uses the winner's max probability; a fixed-probability stub yields a deterministic band; a spread stub yields non-zero `stdev`; `n=1` degrades to a single draw without error.
- [x] `examples/semantic-find.py` loads its task via `Task` objects, runs one `classify()` per query, ranks lines, and returns `"answered" / "partial" / "absent"` from the existence noul at the documented constants; it is smoke-tested in `tests/test_recipe_pack.py` under the stub provider.
- [x] `http_post_json` retries a 429 (then succeeds), telemetry still records each attempt's `http_status`, and a final 429 raises with the error surfaced — guardrail semantics unchanged.
- [x] TECHNICAL-DOCUMENTATION §5 carries the jaggedness wire rules (score-numerics caveat + invariants note); FUNCTIONAL-SPECIFICATIONS §2 documents the consistency helper; README + CHANGELOG updated; full sys1 suite green with no test-count regression and no new ruff errors on touched files.
- [x] Live verification (manual, documented in the recap): semantic-find against a real document through the hosted Fastino API (`decide` provider); `consistency_classify` n≥3 on a noul task through the same. *(Direction 2026-09-28: the local endpoint is no longer a trusted path — live runs showed its wire drops head instructions entirely; user standard is the hosted Fastino API.)*

## Files to be touched

**Skills (outside git — recap is the only trace):**
- `~/.agents/skills/typesafe-jev/SKILL.md` — the three new sections + cookbook index refresh

**sys1:**
- `src/sys1/api.py` — `consistency_classify()`
- `src/sys1/__init__.py` — export
- `src/sys1/providers.py` — 429 in `http_post_json`'s retryable statuses
- `examples/semantic-find.py` (new) — cookbook recipe, provider-swappable
- `tests/test_consistency.py` (new) — band math, spread stats, log passthrough, n=1
- `tests/test_recipe_pack.py` — semantic-find smoke tests
- `tests/test_providers.py` (or the http-retry test home) — 429 retry test
- `TECHNICAL-DOCUMENTATION.md` §5, `FUNCTIONAL-SPECIFICATIONS.md` §2, `README.md`, `CHANGELOG.md`, `docs/STATE-SNAPSHOT.md`, `CLAUDE.md` "Today's state" (count refresh at close), `docs/recaps/SESSION-RECAP-2026-09-28.md`

## Out of scope

- **zcode-router `stated` nouls** ("did the user mention a workflow at all?") — a real judge-behavior change to the typesafe/fastino question sets; follow-up plan of its own.
- **Routing the router's typesafe judge through sys1** (one JSONL calibration stream across both backends) — defensible, but the JS SDK path is proven; revisit later.
- **dev-decisions jaggedness audit of its 8 tasks** — small doc-only pass; follow-up.
- **Structured state support in `classify()`** (`state=` dicts with named paths per task) — the wire supports it and the cookbooks lean on it, but it changes the `classify()` signature and task model; deserves its own plan.
- **Two-stage narrowing for >255-line documents** — documented in the recipe as a note, not implemented.
- **Tuning the 0.30–0.70 band** — constants ship as illustrative; fitting is the calibration stream's job.

## Verification

**Skill (criteria 1–3):** read `~/.agents/skills/typesafe-jev/SKILL.md` — sections present, index rows resolve against `docs.typesafe.ai/llms.txt`.

**Consistency helper (criteria 4–5):**
```bash
.venv/bin/python -m pytest tests/test_consistency.py -q
.venv/bin/python - <<'EOF'
import sys1
from sys1.types import Task, make_noul
t = Task(id="band_demo", heads=[make_noul("Does the diff remove an auth check?", id="auth_removal")])
r = sys1.consistency_classify("decide", t, "diff ...", n=3, cfg=sys1.load_config(), log=False)
print(r["heads"]["auth_removal"])   # samples, mean, stdev, band
EOF
```
Expected: three samples through the hosted Fastino API, band computed. (KEY: source `FASTINO_API_KEY` from `~/.zcode/router/.env`.)

**Semantic find (criterion 6):**
```bash
.venv/bin/python -m pytest tests/test_recipe_pack.py -q
.venv/bin/python examples/semantic-find.py --provider decide --query "termination clause" doc.txt
```
Expected: ranked line IDs + verdict; one classify call per query in the telemetry. (Live verification runs against the hosted Fastino API per the 2026-09-28 direction; the recipe stays provider-swappable.)

**429 (criterion 7):** targeted unit test — first call returns 429, second succeeds; and a maxed-out 429 raises.

**Docs + suite (criterion 8):**
```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check src/sys1 tests examples --output-format=concise
```

## Risks

- **Band constants look more authoritative than they are.** The cookbook calls its own bounds illustrative; our docstrings and README must say the same, or callers will treat 0.30/0.70 as vendor guidance. Mitigation: docstring language + the calibration pointer, mirroring the CMU non-transfer finding.
- **Skill file has no version control.** `~/.agents/skills/` is outside git. Mitigation: the recap records the change; the skill's content is also summarized in sys1's docs.
- **255-option ceiling on the local wire.** The cookbook's 255-option limit is documented for TypeSafe's API; the gliner2 SDK's ceiling is unverified. Mitigation: the live verification step runs semantic-find with a >100-line doc through the local provider; if the SDK rejects large option sets, the recipe documents the two-stage narrowing as required rather than optional.
- **429 retry changes failure timing** for callers that assumed fail-fast on rate limits. Mitigation: bounded by existing `max_retries` (default 2) and backoff; guardrails still fail closed on exhaustion; telemetry records every attempt.
- **Consistency multiplies cost n×.** Document that n=5 multiplies request count (output tokens are free, state tokens are not); the docstring states the trade.

## Dependencies

- `eba8d48` (noul gating — `answer_confidence` is what the consistency band reads for noul heads) and `4916a2f` (prescribed local wire + classifier server — the fast local provider the live verification rides on).
- The recipe-pack idiom (`tests/test_recipe_pack.py` stub provider) for the semantic-find smoke tests.

## Notes

- **Provider policy 2026-09-28 (user): the hosted Fastino API and the TypeSafe Jev API are the only ACTIVE providers.** If additional hosted APIs become available, explore whether to include them. Local GLiNER stays supported by design but is **not encouraged** — and per the same direction, no longer a trusted/verification path. Live verification exposed that the local wire never delivers head instructions to the model (`classification_schema_spec` carries only kind+labels; the SDK's `schema.task(..., instruction=...)` was unused) — query-dependent tasks like semantic find are blind on local. User standard: **the hosted Fastino API is the reliable path**; local stays registered as a provider (stdlib library — consumers may enable it) but is not the verification or default-trust path. Corrections made under this policy: dev-decisions default provider back to `decide` (`98cf8d1`), router judge default provider `decide` (`f99fbd7`). Open question for sys1 itself: whether `providers.enabled` default should drop from `core` to `decide,jev` (consumer-visible default change — decide at wrap-up). Also measured on hosted: big flat Choices may come back JSON null (declined — sys1 gates that as escalate, fail-safe), and nouls arrive bool+confidence rather than probabilistic (recipes need the Decide-shaped branch).


- **Why a library helper for self-consistency and not the other patterns:** consistency is mechanism (repeat, aggregate, band) — provider-agnostic, testable, and it feeds the calibration stream. Function-calling dispatch, entity alignment, and skill suggestion are application shapes — they belong in skills and future recipes, not the library.
- **dbreunig skill: absorb, don't install.** Its craft merges into `typesafe-jev` so there is one opinionated Jev skill; a pointer credits it. karanb192/jev-architect (deciding what to build with Jev) is reference-only.
- **The confidence page validates existing design** — per-provider floors, head-floor ladder, risk-scaled guardrail tiers. Cite it in the skill rather than changing anything.
- Cookbook sources for this plan: `docs.typesafe.ai/model-jaggedness/jev-1.13`, `/confidence`, `/cookbooks/consistency_noul_cookbook`, `/cookbooks/consistency_choice_cookbook`, `/cookbooks/semantic_find`, `/cookbooks/date_extraction_cookbook`, `/cookbooks/pre_parsed_value_extraction_cookbook`, `/cookbooks/function_calling`, `/cookbooks/entity_alignment`, `/cookbooks/skill_suggestion`; plus `github.com/dbreunig/building-with-jev-skill`.

Completed 2026-09-28. Evidence per criterion:

1. Skill jaggedness section: all nine edges with vendor workarounds + the invariants warning tied to `answer_confidence` and per-provider floors (`~/.agents/skills/typesafe-jev/SKILL.md`, "Jagged edges (official, jev-1.13)").
2. Task-design + diagnosis sections present (instructions-object keys, contrastive criteria, standalone score levels, `other` option, noul-0.5 semantics, state minimalism; 13-row symptom table + revision rules).
3. Cookbook index carries the new paths (`semantic_find`, `date_extraction`, `pre_parsed_value_extraction`, `function_calling`, `entity_alignment`, `skill_suggestion`, `/confidence`, `/model-jaggedness/jev-1.13`) + dbreunig/jev-architect pointers.
4. `consistency_classify` returns `samples`/`mean`/`stdev`/`band`; inclusive 0.30–0.70 → `uncertain`; works on probabilistic nouls, Decide-shaped boolean nouls (P(true) = conf when true, 1−conf when false), and choice/score winner probabilities; per-draw ordinary JSONL records verified (`test_log_true_emits_ordinary_records_per_draw`).
5. Choice banding on winner max-prob (0.6 vs the 0.55 shape-statistic, asserted); fixed stub → deterministic band; scripted spread → stdev 0.2; n=1 → stdev None without error; n<1 raises.
6. `examples/semantic-find.py` builds its Task inline, one classify per query, ranked lines + verdict; 6 smoke tests in `test_recipe_pack.py` (query in BOTH instructions, answered/absent/partial, Decide-shaped bool, 255-line guard).
7. `test_429_retries_then_succeeds` + `test_429_exhausted_raises`; telemetry records every attempt; guardrail fail-closed semantics unchanged.
8. TECHNICAL §5 "Jev jagged edges (task-authoring rules)" + score-numerics caveat; FUNCTIONAL §2 "Consistency classification"; README quick-start + recipe row; CHANGELOG entry; suite 233 passed; ruff 16 pre-existing / 0 new.
9. Live, against the hosted Fastino API and Jev (keys sourced from `~/.zcode/router/.env`):
   - semantic-find, answerable query: decide exists=true@0.52 → answered; jev exists=0.98 with L008 (the cancellation clause) ranked 1.00 — the cookbook's full pattern.
   - semantic-find, unanswerable query: decide exists=false@0.82 → absent; jev exists=0.01 → absent. The query-embedded noul discriminates (the pre-fix query-blind version returned 0.61/0.61 for both).
   - consistency_classify n=3 via decide on the auth-removal noul: samples [0.9998 ×3], band "yes".
   - Known hosted behavior: decide declines large flat ranking choices (JSON null) even at 8 lines — ranking engages on jev; the recipe documents this and returns the existence verdict either way.

Commits: sys1 (this branch), dev-decisions `98cf8d1`, zcode-router-kit `f99fbd7` (provider-policy corrections). None pushed; sys1 merge to `main` requires explicit approval.