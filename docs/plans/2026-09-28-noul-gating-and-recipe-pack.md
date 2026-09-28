---
status: completed
created: 2026-09-28
updated: 2026-09-28
slug: noul-gating-and-recipe-pack
---

# Plan: Noul gating, per-head floors, and the recipe pack

## Context

The 2026-09-28 review of the vendor recipe pack (systemonemodels.org/recipes) against sys1 found three gaps. First and worst: `interpret()` (and cascade's `conf_ok`) floors a head only when its `confidence` field is present, and per the TypeSafe docs a Jev **noul answer carries no `confidence` field** — the 0–1 answer *is* the certainty. So noul-gated tasks (citation support, injection screen, confidence-gated action) currently return `pass` on Jev no matter what the noul says, while Decide-served nouls (which arrive as `{label, confidence}`) gate normally — same head, opposite behavior per provider. Second: floors exist at global / per-provider / per-task granularity only, so the recipes' risk-scaled per-action thresholds ("reads act at 0.6, refunds need 0.85" in one task) can't be expressed. Third: `examples/` is entirely git-repo-shaped; none of the eight vendor recipes are packaged. This plan closes all three.

Builds on the same-day Jev score-wire fix on `feature/provider-flags-and-dashboard` (uncommitted): score heads correctly emit the ordered-array criteria form, so the Score-based recipes are expressible.

## Approach

One gating change, one precedence change, one pack — in that order, because the recipes depend on both gating changes.

For noul, derive a floor-comparable certainty when no explicit confidence exists: `certainty = max(p, 1 − p)` where `p` is the noul probability. This matches the binary case of the CMU cascade paper (gate on max probability) and the rule already in the `typesafe-jev` skill ("gate on the probability for Noul"), and it makes Jev noul answers gate at all. An explicit `confidence` on the answer still wins — Decide-shaped nouls keep today's behavior exactly. For per-head floors, add an optional scalar `floor` to `Head`; `interpret()` and `cascade_classify` resolve per-head floors through one shared helper so the two paths can't drift. The precedence ladder stays explicit-config-wins at the top (per the current documented behavior): per-provider `{pid}_confidence_floor` config key > `head.floor` > `task.confidence_floor` > global > `_DEFAULT_FLOORS`. The recipes ship as example task files + short scripts (the `examples/custom-task.toml` idiom), not as new built-in tasks — the registry stays at 8 and consumers adopt what they need.

High-level steps:

1. **Gating core** (`gating.py`): add `answer_confidence(entry)` helper (explicit confidence, else noul-derived certainty, else None) and `floor_for_head(head, prov_floors)`; rewire `interpret()` to use both.
2. **Cascade parity** (`api.py`): replace cascade's inline `conf_ok` computation with the same helper so noul/head-floor semantics match `interpret()`.
3. **Loader** (`tasks.py`): parse a per-head `floor` key in the TOML/JSON task loader.
4. **Fixture accuracy**: drop the hand-written `confidence` from noul answers in `tests/fixtures/jev_sample.json` (docs say it doesn't exist) and update `test_sdk_shape` accordingly.
5. **Recipe pack**: five examples (guardrail screen, citation check, lead scoring, passage re-rank, taxonomy walk) with sourced task files, a stub-provider smoke test, and `examples/README.md` documenting each recipe, its vendor source URL, and provider prerequisites.
6. **Contract docs**: gating/noul semantics into TECHNICAL-DOCUMENTATION.md, the `<add-when-implemented>` noul/score-interpretation items into FUNCTIONAL-SPECIFICATIONS.md §1, recipe pack into §6 of the same file, plus STATE-SNAPSHOT and CLAUDE.md "Today's state" refresh.

## Acceptance criteria

These are the contract. Each one is a verifiable assertion. The work is "done" when every box is checked.

- [x] A Jev-shaped noul answer (float probability, no `confidence`) with p=0.95 yields `verdict: "pass"` at floor 0.7, and p=0.55 yields `"escalate"` — noul is gated on `max(p, 1 − p)`. *(verified by running this plan's snippet: `pass` / `escalate`; `tests/test_gating.py::TestNoulGating`)*
- [x] A noul answer carrying an explicit `confidence` is still gated on that confidence (precedence: explicit confidence beats derived certainty), so Decide-shaped nouls are unchanged. *(plan snippet `noul=.95, confidence=.60` → `escalate`; `TestAnswerConfidence`)*
- [x] A head with `floor=0.85` and confidence 0.80 escalates while a sibling head at the default floor (0.7) passes within the same `classify()` call. *(plan snippet: `escalate`, summary names `risky`; `TestHeadFloors`)*
- [x] An explicit per-provider config key (`jev_confidence_floor`) still overrides `head.floor` and `task.confidence_floor` — the top of the precedence ladder is unchanged. *(plan snippet: config 0.9 beats head 0.5 → `escalate`; `TestEffectiveFloor`)*
- [x] `cascade_classify` over a noul-only task stops at the first provider whose derived certainties clear the floor, and returns its answers as the winner. *(plan snippet: `providers_used == ['pA']`, `pass`; `tests/test_api.py::TestCascadeNoulAndHeadFloors`)*
- [x] `load_tasks_file` parses a per-head `floor` from TOML/JSON and `classify()` honors it end to end. *(`tests/test_recipe_pack.py::TestLoaderPerHeadFloor::test_toml_floor_parses_and_gates`)*
- [x] `tests/fixtures/jev_sample.json` contains no `confidence` on noul answers, and `test_sdk_shape` asserts the docs-accurate shape (noul value, `confidence is None`). *(fixture updated; `tests/test_parsers.py` green)*
- [x] Each shipped recipe task file loads and produces the expected head ids under the stub provider in `tests/test_recipe_pack.py`. *(17 tests green; also `test_shipped_task_files_all_parse` guards every shipped TOML — which uncovered and fixed a pre-existing parse failure in `examples/custom-task.toml`)*
- [x] `examples/README.md` documents every recipe with its vendor source URL and prerequisites, and the full test suite passes with no test-count regression and no new ruff errors on touched files. *(README recipe table with sources; 188 passed (was 126 — all additions); ruff: 16 pre-existing errors, 0 new on touched files)*

Completed 2026-09-28. Recap: [`docs/recaps/SESSION-RECAP-2026-09-28.md`](../recaps/SESSION-RECAP-2026-09-28.md).
Feature doc: [`docs/features/gating-and-recipes.md`](../features/gating-and-recipes.md).
Changes are committed-pending review on `feature/provider-flags-and-dashboard`; merging to `main` still requires explicit approval.

## Files to be touched

**Library core:**
- `src/sys1/gating.py` — add `answer_confidence(entry)` + `floor_for_head(head, floors)`; `interpret()` gates noul on derived certainty and honors `head.floor`; docstring records the precedence ladder and the per-provider-semantics warning (Jev concentration vs Decide max-prob — head floors are expressed in the *serving provider's* semantics; fit per provider, never reuse a number across providers expecting equivalence)
- `src/sys1/api.py` — `cascade_classify`: `conf_ok`/`labels_ok` use the gating helpers
- `src/sys1/types.py` — `Head.floor: float | None = None` field plus a `floor` parameter on the `make_noul`/`make_choice`/`make_score` factories (they are the only constructor path)
- `src/sys1/tasks.py` — `load_tasks_file` parses a per-head `floor`: pass `spec.get("floor")` through `_head_from_spec` into the factories (mirrors how `confidence_floor` is parsed at task level, `tasks.py:527`)

**Tests:**
- `tests/test_gating.py` — noul derivation cases (float p with/without confidence, Decide label-noul, bool noul), per-head floor override + precedence-ladder cases
- `tests/test_api.py` — cascade clears on derived noul certainty; cascade honors head floors
- `tests/test_parsers.py` — update `test_sdk_shape` for the fixture change; assert derived-certainty path end-to-end via `interpret()`
- `tests/fixtures/jev_sample.json` — remove `confidence` from noul answers
- `tests/test_recipe_pack.py` (new) — loads each recipe task file, classifies with a registered stub provider, asserts head ids and expected verdicts

**Examples (new):**
- `examples/guardrail-injection.toml` — 5 hazard nouls (jailbreak, prompt injection, secret exfil, denylist pressure, harmful request) + severity score, per the official llm_guardrails cookbook
- `examples/citation-check.toml` — noul "does the quoted passage support the claim" + optional support-strength score
- `examples/lead-scoring.py` — 4 composite score heads, normalize `(score − min) / (max − min)`, weights as code constants
- `examples/rerank-passages.py` — generated score heads per passage (one fan-out call), sort/cutoff/return-nothing-if-weak in code
- `examples/taxonomy-walk.py` — sequential per-level `classify()` over child labels, probability multiplication with depth normalization, early bail via `head.floor` on weak edges
- `examples/README.md` — per-recipe docs: source pattern/cookbook URL, provider prerequisites, how to run

**Documentation (see Linked artifacts below):**
- `docs/features/gating-and-recipes.md` — create: the noul-certainty rule, the floor precedence ladder, recipe pack usage

## Out of scope

Things people might assume are part of this work but aren't:

- Encoding the guardrail two-tier policy (review ≥ 0.35 / action ≥ 0.70 / severity_block ≥ 2.0) inside `interpret()` — hazard batteries keep thresholds in caller code by design ("the model returns a label and a number; the thresholds stay in your code"); the guardrail recipe demonstrates the pattern.
- Adding the recipes as built-in tasks in the registry — the 8 built-ins stay as they are; recipes ship under `examples/` + the `load_tasks_file` idiom.
- Changes to `dev-decisions` or any other consumer — its `safety` task will see *different verdicts on Jev* after this change (that's the bug being fixed); run the parity script, document the behavior change, don't edit the consumer here.
- `score`-head certainty derivation — score answers already carry `confidence`; nothing to derive.
- Deriving and storing the noul certainty into the logged answers — JSONL answers stay verbatim provider output (public contract); dashboards recompute if they need it.
- Per-head floors as per-provider maps — rejected alternative, see Notes.

## Verification

How to confirm each criterion is met.

**Criterion 1: noul derivation**
```bash
.venv/bin/python -c "
import sys1
from sys1.types import Provider, Task, make_noul
from sys1.gating import interpret
head = make_noul('Is this a refund request?', id='refund')
t = Task(id='t', heads=[head])
p = Provider(id='p', name='p', classify=lambda text,h,c,te: {'refund': {'type':'noul','noul':0.95}}, is_available=lambda c: True)
sys1.register(p)
r = sys1.classify('p', t, 'please refund me', cfg=sys1.load_config(), log=False)
print(r.verdict); assert r.verdict == 'pass'
p2 = Provider(id='p2', name='p2', classify=lambda text,h,c,te: {'refund': {'type':'noul','noul':0.55}}, is_available=lambda c: True)
sys1.register(p2)
r2 = sys1.classify('p2', t, 'maybe a refund', cfg=sys1.load_config(), log=False)
print(r2.verdict); assert r2.verdict == 'escalate'
"
```
Expected: `pass` then `escalate`.

**Criterion 2: explicit confidence wins**
Same harness with `{'type':'noul','noul':0.95,'confidence':0.60}` at floor 0.7 — expected `escalate` (gated on the explicit 0.60, not the derived 0.95).

**Criterion 3: per-head floor**
Two-head task (`floor=0.85` on `risky`, default on `safe`), stub returns confidence 0.80 on both — expected `escalate` with the summary naming `risky`.

**Criterion 4: precedence ladder**
`cfg['classify']['jev_confidence_floor'] = 0.9` with `head.floor=0.5` and a 0.75-confidence answer — expected `escalate` (config key wins).

**Criterion 5: cascade with noul**
Provider chain `['pA','pB']`, noul-only task; `pA` returns p=0.95, `pB` p=0.60 — expected `providers_used == ['pA']`, verdict `pass`.

**Criterion 6: loader floor**
Load a TOML with `{ id = "risky", kind = "choice", task = "...", floor = 0.9 }`, classify a 0.80 answer — expected `escalate`.

**Criterion 7: fixture**
```bash
.venv/bin/python -m pytest tests/test_parsers.py::TestParseJev -q
```
Expected: green; `jev_sample.json` noul answers have no `confidence` key.

**Criterion 8: recipe pack smoke**
```bash
.venv/bin/python -m pytest tests/test_recipe_pack.py -q
```
Expected: every recipe task file loads, expected head ids classify, guardrail task escalates on a stub high-hazard answer.

**Criterion 9: docs + suite + lint**
```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check src/sys1 tests examples --output-format=concise
```
Expected: suite green with count ≥ 140; no ruff errors introduced on touched files.

## Linked artifacts

Documents that need updating when this work is done. The recap workflow will prompt for each.

- `docs/features/gating-and-recipes.md` — create: noul-certainty rule, floor precedence ladder, per-recipe usage
- `TECHNICAL-DOCUMENTATION.md` section 5 "Provider Registry & Wire Shapes" — add the noul gating rule to the Jev primitives table notes; section 6 "Task System" — mention per-head `floor` and the recipe pack
- `FUNCTIONAL-SPECIFICATIONS.md` section 1 "Classification Behavior" — implement the `noul head interpretation` and `score head interpretation` add-when-implemented items; section 2 "Cascade Classification" — cascade clears on derived noul certainty / head floors; section 3 "Gating & Exit Codes" — floor precedence ladder
- `docs/STATE-SNAPSHOT.md` — refresh coverage/test counts
- `CLAUDE.md` "Today's state" — refresh (still shows 126 tests / 34 ruff errors; now 140 / 21 on the branch)
- `CHANGELOG.md` — entry for the behavior change (noul gating now enforced; `Head.floor` added)
- `README.md` — examples section gains the recipe pack; gating note for noul-derived certainty

## Risks

- **This intentionally changes verdicts.** Tasks whose noul heads are served by Jev currently pass on any non-declined answer; after the change they gate on the noul probability. `dev-decisions`' `safety` task is the loudest case. Mitigation: this is the bug being fixed; run `scripts/verify-dev-decisions-parity.py`, document the behavior change in the recap and CHANGELOG, and note that `block_on_classification` stays opt-in so exit codes only change for operators who asked for blocking.
- **Cross-provider floor semantics.** A `head.floor` scalar applies in the serving provider's own confidence semantics (Jev concentration vs Decide max-prob) — reusing one number across providers expecting equivalent meaning is the trap the CLAUDE.md hard rule warns about. Mitigation: docstring + linked feature doc state the rule explicitly; the per-provider `{pid}_confidence_floor` config key remains the cross-provider knob.
- **Fixture change ripples.** `test_sdk_shape` asserts `confidence == 0.92` on a noul answer today; removing it from the fixture changes that assertion. Mitigation: the test is updated to assert the docs-accurate shape in the same patch; no other test reads that key.
- **Non-probability noul values.** Some providers return booleans rather than 0–1. Mitigation: derivation only for real numbers (`bool` excluded), so a bool noul keeps today's behavior; anything undecirable still escalates on `label is None` only if declined.
- **Examples need a live provider to demo.** Mitigation: every recipe is covered by a stub-provider test; `examples/README.md` states the prerequisites (`sys1 doctor`) and shows the local-only path (`--provider local`) where applicable.

## Dependencies

- The 2026-09-28 Jev score-wire fix (uncommitted on `feature/provider-flags-and-dashboard`, in `src/sys1/types.py` `to_jev_head`/`_score_criteria`) — the two Score-based recipes depend on it.
- `docs/plans/2026-09-28-sys1-correctness-fixes.md` — landed (`9e13e16`); its `Task.confidence_floor` wiring is the layer `head.floor` sits above.

## Notes

- **Why certainty, not raw probability.** For a floor/accept-escalate gate, `max(p, 1 − p)` is the right statistic: a confident "no" (p=0.05) should clear the floor, and the CMU paper's cascade gates on exactly this quantity for binary. Raw-probability gating is the *hazard-battery* pattern (act when p(jailbreak) ≥ 0.7) — that stays caller-side policy, not a floor.
- **Rejected: per-head floors as per-provider maps** (`floors={"jev": 0.85, "decide": 0.9}`). Structurally prevents the semantics-mixing trap, but it duplicates what the per-provider config key already does, complicates TOML, and diverges from the recipes' single-number thresholds. Revisit if cross-provider reuse of head floors becomes a real usage.
- **Rejected: encoding guardrail policy in `interpret()`.** Two-tier hazard thresholds + severity-block escalation don't decompose into per-head floors; they need the full answer distribution. The library stays the "returns labels and numbers" half.
- **Precedence ladder (single source of truth):** per-provider config key > `head.floor` > `task.confidence_floor` > global `confidence_floor` > `_DEFAULT_FLOORS[pid]`. The current behavior (per-provider key wins at the top) is preserved; `head.floor` slots in above task level.
- Recipe sources: citation-check, parallel fan-out, support triage, confidence-gated action, passage re-rank, composite scoring, prompt-injection screen, taxonomy cascade — all from systemonemodels.org/recipes/, which credits the TypeSafe cookbooks `/cookbooks/citation_check`, `/cookbooks/llm_guardrails`, `/patterns/fan-out`, `/patterns/confidence-routing`, `/patterns/composite-scoring`, `/cookbooks/rerank_typesafe`, `/cookbooks/classification_using_confidence`, and `/cookbooks/hierarchical_classification`.
- Threshold numbers in the recipes (0.6 floor, 0.85 high-stakes, 0.35/0.70/2.0 battery) are vendor starting points, not tuned values — the JSONL calibration stream is where they get fit, per the CMU non-transfer finding.
