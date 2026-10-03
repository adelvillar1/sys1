# Session recap — 2026-10-02 (2): plan-surface, probes, README reconciliation

**Plan link**: this session ran ahead of plans (spitball-validated builds); the glide plan (`docs/plans/2026-10-01-glide-provider-and-router.md`) is complete and unrelated except for the config it contributed.

## What shipped

- **`src/sys1/plansurface.py`** — layered precompute for plan decomposition: `parse_criteria`, `inventory_surface`, `build_map_task` (criterion→module choice + existence noul per criterion), `build_dep_tasks` (one choice head per unordered pair, chunked at 14), `assemble` (thresholded DAG, uncertain band, weakest-edge cycle breaks, risk flags), `reconcile` (Kendall tau + threshold sweep vs commit sequence), `grade_and_record` (shared per-head feedback writer). Probe-informed defaults: map layer needs jev (drex/glide return one weak top-1 pick), `DEP_THRESHOLD = 0.8` (direction precision 1.0 at ≥0.8 vs 0.5-0.67 at 0.65 on the control-tower corpus), `run_*` default `provider="auto"` through routing.
- **Probe scripts**: `scripts/mutate-module.py` (stdlib AST mutation harness: cmp/bool/const/neg/drop ops, per-test kill attribution), `scripts/probe-plan-replay.py` (retroactive decomposition replay), `scripts/probe-test-surface.py` + `scripts/probe_lib.py` (test-surface probe), `scripts/verify-dev-decisions-parity.py` (pre-existing).
- **Test-surface probe**: three blind sys1 layers over a suite, graded by mutation kill matrix. First run on plansurface pure functions: 92/120 killed (0.767), hand-check 3/3, 56 feedback rows. Layer verdicts: behavior inventory deployment-ready; tautology confidence-orders but over-flags (needs fitted floors); redundancy re-scoped — kill-set SUBSUMPTION is the defensible ground truth, and the layer still can't see it from source text (needs the matrix as state).
- **README reconciliation**: plansurface + probe scripts + routing.py + scripts/ added to What's-inside/Project-layout, plan-surface section + Quick-start snippet, stale "233 tests" removed (321 collected). Parallel-session content (julia-1, glide pricing) preserved.

## Numbers

- Suite: 321 tests green (16 plansurface wiring tests + 9 mutation-harness tests added today; 28 mutation survivors triaged in `docs/mutant-triage-plansurface.md` — ~8 equivalent, ~20 gaps in 4 clusters, ~10 targeted tests lift score 0.77→~0.95).
- uc-gate probe rerun against the fixed fan-out: citation precision 12/12 on the control-tower corpus; quadrant 0/6 documented as a retroactive artifact (post-hoc FS reads the plan's own output as existing) — see the dev-decisions and second-brain recaps; valid uc-gate mode is design-time.

## Ruff debt settled

The julia session's recap flagged 8 ruff errors owned by this session's files: all 16 (grown during the day) fixed — unused loop vars, RUF005 unpacking, B905 strict zips, N806 renames, `itertools.pairwise` in reconcile. `ruff check` is clean for everything this branch touched (2 remaining repo-wide errors live in the other session's untracked `.agents/` files).

## Gotchas (recorded for the next session)

- deepseek-flash rejects full-page PNGs above ~1.5MB — `capture.fold()` viewport fallback exists in the ux-capture kit; deepseek key lives in `~/.zshrc`, TYPESAFE in `~/.config/dev-decisions/env`.
- `sys1.classify` skips JSONL logging when every provider fails — empty answers mean resolution failure, check telemetry.
- Retroactive uc-gate quadrants are invalid against post-hoc FS inventories; design-time runs are the valid mode.

## Open

- Merge `feature/plan-surface` to main (user approval pending; main lags by the whole day's work).
- Mutant-gap tests (~10 targeted tests per the triage doc) when convenient.
- Forward uc-gate run on the next real plan.
