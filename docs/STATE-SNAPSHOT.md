# State Snapshot

> Dated entity counts and health signals for the project. **Replace** this file when refreshing — do not append.

Last updated: 2026-09-28

| Signal | Value | Notes |
|--------|-------|-------|
| Library version | 0.2.0 | Branch: `feature/provider-flags-and-dashboard` |
| Registered providers | 11 | 4 core + 7 candidate |
| Pre-registered tasks | 8 | `change`, `commit_audit`, `deps_risk`, `docs_drift`, `api_drift`, `pr_gate`, `issue_triage`, `safety` (recipes ship under `examples/`, not the registry) |
| Test count | 211 | pytest (was 126; +31 gating/cascade, +17 recipe-pack, +23 local fastino wire, +14 misc) |
| Coverage | 82% | `cli.py` 0%, `tasks.py` 84%, `providers.py` 87%, `gating.py` 100% |
| Open ruff errors | 16 | Pre-existing: E741, F841, SIM115, SIM117, B007, B905, RUF022, RUF046 |
| Confirmed defects | 0 | `docs/plans/2026-09-28-sys1-correctness-fixes.md` landed (`9e13e16`) |
| Active plans | 1 | `2026-09-28-fastino-decide-adoption` (active; fastino-prescribed local wire, classifier server, consumers on sys1) |
| Example recipes | 6 | `examples/` — guardrail screen, citation check, lead scoring, re-rank, taxonomy walk, routing judge; stub-tested |

Gating semantics changed 2026-09-28: noul answers gate on derived certainty `max(p, 1 − p)`; `Head.floor` slots above the task floor in the precedence ladder. The `gliner` wire now follows the fastino-prescribed `gliner2.classification` API (one decode for all heads, full `probabilities`) with a load-once classifier server (`providers.local_server_url`) or subprocess fallback. `decide` id = hosted wire; the Decide model is what `local` runs. See `docs/features/gating-and-recipes.md` and the adoption plan.
