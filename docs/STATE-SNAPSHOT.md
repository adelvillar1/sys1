# State Snapshot

> Dated entity counts and health signals for the project. **Replace** this file when refreshing — do not append.

Last updated: 2026-09-28

| Signal | Value | Notes |
|--------|-------|-------|
| Library version | 0.2.0 | Branch: `feature/provider-flags-and-dashboard` |
| Registered providers | 11 | 4 core + 7 candidate |
| Pre-registered tasks | 8 | `change`, `commit_audit`, `deps_risk`, `docs_drift`, `api_drift`, `pr_gate`, `issue_triage`, `safety` (recipes ship under `examples/`, not the registry) |
| Test count | 188 | pytest (was 126; +31 gating/cascade, +17 recipe-pack) |
| Coverage | 82% | `cli.py` 0%, `tasks.py` 84%, `providers.py` 87%, `gating.py` 100% |
| Open ruff errors | 16 | Pre-existing: E741, F841, SIM115, SIM117, B007, B905, RUF022, RUF046 |
| Confirmed defects | 0 | `docs/plans/2026-09-28-sys1-correctness-fixes.md` landed (`9e13e16`) |
| Active plans | 1 | `2026-09-28-noul-gating-and-recipe-pack` (active; noul gating, per-head floors, recipe pack) |
| Example recipes | 5 | `examples/` — guardrail screen, citation check, lead scoring, re-rank, taxonomy walk; stub-tested in `tests/test_recipe_pack.py` |

Gating semantics changed 2026-09-28: noul answers gate on derived certainty `max(p, 1 − p)`; `Head.floor` slots above the task floor in the precedence ladder. See `docs/features/gating-and-recipes.md`.
