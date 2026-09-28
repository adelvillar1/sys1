# Session Recap — 2026-09-28

> Plans: [`docs/plans/2026-09-28-noul-gating-and-recipe-pack.md`](../plans/2026-09-28-noul-gating-and-recipe-pack.md) → completed; [`docs/plans/2026-09-28-fastino-decide-adoption.md`](../plans/2026-09-28-fastino-decide-adoption.md) → completed

## Second half: fastino Decide adoption (sys1 as the one integration)

The vendor's own playground (fastino/gliner25-decide-playground) settled how
GLiNER2.5-Decide is meant to be used: `gliner2.classification.Classifier` +
one `ClassificationSchema` for all heads (`single`/`ordinal`),
`ClassificationConfig(decoder="independent")`, full `probabilities` per
answer. Verified in our venv: 9.8s model load, 361ms one-decode 3-head
classify on CPU. Per the user's directive, sys1 is THE fastino integration
and dev-decisions + zcode-router consume it; hosted api.fastino.ai is off
every default path.

Shipped (sys1 `4916a2f` + `7663cde`):

- **Prescribed local wire** — `_LOCAL_INLINE` migrated off the legacy
  `GLiNER2.classify_text` per-head loop; `classification_schema_spec()`
  (pure) feeds both the inline script and the new server; `parse_gliner2`
  emits `{label, confidence=max(probs), probabilities}` for choice, the
  docs-accurate noul shape (no confidence; gating derives `max(p, 1−p)`),
  and `score = score_min + ordinal_level_index` with a legend.
- **Classifier server** (`service/classifier_server.py`, stdlib, runs in the
  venv) — load-once, one inference at a time, `GET /ready` + `POST /infer`
  with the playground contract. Opt in via `providers.local_server_url`
  (per-model `{pid}_server_url`); automatic subprocess fallback; an
  answering server counts as available in `sys1 doctor`. Measured: 12.4s
  per subprocess classify → 361ms first / 144ms steady-state via the server.
- **Inline task specs** — `sys1.task_from_spec()` + `POST /v1/classify`
  accepts `task_spec`, so consumers with per-install heads (the router's
  workflow names) classify without registering a task.
- **Naming collision documented** — `decide` id = hosted wire; the Decide
  model is what `local` runs. Hosted `decide_model` string corrected to
  `fastino/GLiNER2.5-Decide`.
- `examples/routing-judge.toml` — the router's four questions as a sys1 task.

Consumers: dev-decisions `d5e140d` (default provider flips to `local`, its
degraded inline path migrated to the same prescribed surface, README/config
updated) and zcode-router-kit `015c8f2` (`fastino.mjs` is a sys1-service
client — inline `task_spec` from the install's own workflow names, same
exports/return contract, gates stay caller-side, multi-v1 → Decide, no
direct hosted call on the live path).

**Finding worth keeping:** the classification schema's task NAME is part of
the model input — naming the same schema by head id vs task text moves
confidences (0.3949 vs 0.3884) while labels stay stable. That is why the
parity script now gates on labels/shapes/noul-consistency and reports local
confidence deltas as informational: sys1 names tasks by head id, dev-
decisions' degraded path by task text. Any future task-name change is a
behavior change.

Verification: sys1 suite 188 → 211 (new `tests/test_local_wire.py`, all
stubbed — no model in tests); live end-to-end runs in both serving modes;
parity script exit 0 with API keys unset; Node judge path exercised against
the live service. Ruff: 16 pre-existing, 0 new.

## First half: noul gating, per-head floors, recipe pack

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
