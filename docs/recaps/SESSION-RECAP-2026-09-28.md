# Session Recap — 2026-09-28

> Plan: [`docs/plans/2026-09-28-noul-gating-and-recipe-pack.md`](../plans/2026-09-28-noul-gating-and-recipe-pack.md) → completed this session

## What shipped

**The gating fix.** A Jev noul answer has no `confidence` field — the 0–1
output *is* the probability of "true". `interpret()` and `cascade_classify()`
floored a head only when an explicit `confidence` existed, so noul-gated
tasks served by Jev returned `pass` on any non-declined answer, while
Decide-shaped nouls (which arrive as `{label, confidence}`) gated normally —
same head, opposite behavior per provider. New `gating.answer_confidence()`
derives `max(p, 1 − p)` when no explicit confidence exists; explicit
confidence still wins, so Decide-shaped nouls are unchanged. Bool nouls have
nothing to derive and keep today's behavior. Cascade parity comes from both
paths sharing `answer_confidence` / `effective_floor` / `floors_for`, plus a
new `floor_override` on `interpret()` so cascade's explicit
`confidence_floor` argument reaches the verdict.

**Per-head floors.** `Head.floor: float | None`, a `floor=` parameter on the
`make_choice`/`make_noul`/`make_score` factories, and TOML/JSON loader
support. The precedence ladder is unchanged at the top: per-provider
`{pid}_confidence_floor` config key > `head.floor` > task > global >
`_DEFAULT_FLOORS`.

**The recipe pack.** Five recipes under `examples/`, ported from the
TypeSafe cookbook patterns and documented in `examples/README.md` with
vendor source URLs and prerequisites: guardrail hazard battery (5 nouls +
severity score, review/action/block tiers in `screen_message.py`, fails
closed), citation check (noul `floor = 0.85`), composite lead scoring,
passage re-rank, taxonomy walk (weak-edge bail via `head.floor`). All are
smoke-tested offline against a registered stub provider in
`tests/test_recipe_pack.py`.

**Two latent bugs found and fixed along the way.** `floors_for()` never
applied the effective task/global floor to custom (non-core) providers —
they silently fell through to the global default, which is what let cascade
and `interpret()` disagree for stub ids. And `examples/custom-task.toml` had
never parsed: multi-line TOML inline tables (tomllib forbids them) plus
`provider_heads` written as a dict where the loader takes a list. Fixed, and
`test_shipped_task_files_all_parse` now guards every shipped task file.

## Acceptance criteria — status

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | noul p=0.95 passes at 0.7; p=0.55 escalates | done | plan verification snippet run this session (`pass` / `escalate`); `test_gating.TestNoulGating` |
| 2 | explicit confidence wins; Decide-shaped nouls unchanged | done | plan snippet (`noul=.95, confidence=.60` → escalate); `test_explicit_confidence_beats_derivation` |
| 3 | head `floor=0.85` escalates at 0.80 while sibling passes | done | plan snippet (escalate, summary names `risky`); `TestHeadFloors` |
| 4 | per-provider config key still overrides head + task floors | done | plan snippet (`jev_confidence_floor=0.9` beats `head.floor=0.5`); `TestEffectiveFloor` |
| 5 | cascade stops at first provider clearing on derived certainty | done | plan snippet (`providers_used == ['pA']`, verdict `pass`); `TestCascadeNoulAndHeadFloors` |
| 6 | loader parses per-head `floor`; classify honors it | done | `test_toml_floor_parses_and_gates` |
| 7 | `jev_sample.json` nouls carry no confidence; `test_sdk_shape` asserts it | done | fixture + `tests/test_parsers.py` green |
| 8 | each recipe task file loads and classifies expected head ids under the stub | done | `tests/test_recipe_pack.py` — 17 tests, all green |
| 9 | examples/README documents every recipe; suite green, no new ruff | done | README recipe table with sources/prereqs; 188 passed; ruff 16 errors, all pre-existing (13 src + 3 `tests/test_providers.py`), 0 new on touched files |

Suite: 126 → 188 tests. Coverage 76% → 82% (`gating.py` now 100%).

## Behavior change consumers should know

Tasks whose noul heads are served by Jev used to pass on any non-declined
answer; they now gate on the noul probability. `dev-decisions`' `safety` task
is the loudest case — verdicts will differ there, and that is the bug being
fixed. `block_on_classification` stays opt-in so exit codes change only for
operators who asked for blocking. The parity script
(`scripts/verify-dev-decisions-parity.py --provider local`) was run this
session and confirms the wire shape still agrees; the change is at the
verdict level, not the wire level.

Not done in this session (out of scope): committing. All changes are staged
for review on `feature/provider-flags-and-dashboard`, unmerged to `main`
(merge needs explicit user approval).

## Doc-update follow-ups (done)

- Created `docs/features/gating-and-recipes.md` (noul-certainty rule, floor
  ladder, recipe usage, behavior changes).
- `TECHNICAL-DOCUMENTATION.md` §5 — noul gating subsection + ladder in the
  floors section; §6 — per-head floor, TOML one-line rule, recipe pack. Test
  count 126 → 188.
- `FUNCTIONAL-SPECIFICATIONS.md` §1 — head interpretation (choice/noul/score;
  removed the noul/score `add-when-implemented` items); §2 — cascade clears
  on derived certainty via the shared ladder; §3 — floor precedence ladder +
  cross-provider warning.
- `docs/STATE-SNAPSHOT.md` — replaced (188 tests, 82%, 16 ruff, 5 recipes,
  active plan).
- `CLAUDE.md` — "Today's state" and the pytest command count refreshed.
- `CHANGELOG.md` — noul-gating fix, per-head floors, recipe pack,
  custom-task.toml fix, fixture change; folded into the unreleased 0.2.0
  entry (main is still at v0.1.0).
- `README.md` — noul confidence note in "Confidence semantics", new "Recipe
  pack" section, `floor` in the TOML example, project-layout test count.
