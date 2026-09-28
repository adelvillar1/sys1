# State Snapshot

> Dated entity counts and health signals for the project. **Replace** this file when refreshing — do not append.

Last updated: 2026-09-28

| Signal | Value | Notes |
|--------|-------|-------|
| Library version | 0.2.0 | Branch: `feature/provider-flags-and-dashboard` |
| Registered providers | 11 | 4 core + 7 candidate |
| Pre-registered tasks | 8 | `change`, `commit_audit`, `deps_risk`, `docs_drift`, `api_drift`, `pr_gate`, `issue_triage`, `safety` (recipes ship under `examples/`, not the registry) |
| Test count | 233 | pytest (was 126; +31 gating/cascade, +17 recipe-pack, +23 local fastino wire, +22 consistency/semantic-find/429, +14 misc) |
| Coverage | 82% | `cli.py` 0%, `tasks.py` 84%, `providers.py` 87%, `gating.py` 100% |
| Open ruff errors | 16 | Pre-existing: E741, F841, SIM115, SIM117, B007, B905, RUF022, RUF046 |
| Confirmed defects | 0 | `docs/plans/2026-09-28-sys1-correctness-fixes.md` landed (`9e13e16`) |
| Active plans | 1 | `2026-09-28-jev-cookbook-adoption` (self-consistency helper, semantic-find recipe, jagged-edge wire rules, skill upgrade) |
| Example recipes | 7 | `examples/` — guardrail screen, citation check, lead scoring, re-rank, taxonomy walk, routing judge, semantic find; stub-tested |

Provider policy (2026-09-28): the hosted Fastino API (`decide`) and the TypeSafe Jev API (`jev`) are the **only active providers**; additional hosted APIs are explored before inclusion. Local GLiNER stays supported by design but is not encouraged (its wire drops head instructions — query-dependent tasks are blind there) and nothing critical relies on it. Gating semantics: noul answers gate on derived certainty `max(p, 1 − p)`; `Head.floor` sits above the task floor; hosted-decide boolean nouls estimate P(true) as conf/1−conf in the consistency helper. See `docs/features/gating-and-recipes.md` and the plans.
