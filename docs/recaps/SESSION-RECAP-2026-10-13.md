# Session Recap — 2026-10-13

## Summary

Added **ScaleDown `classify-1`** as sys1's ninth core provider — the documented
Jev-mirror Decisions API from scaledown.ai, wired end-to-end and proven live on
a real diff. The provider was admitted on the strength of a three-way probe
(d1 vs GLiDE vs classify-1) on blend1's own tasks: route-task verdicts agree
across all three, so classify-1 takes a route seat; prose-rating nouls compress
toward the middle, so it stays out of validation gates. Also fixed a stale
safety-routing test that had the committed tree red (`c1f7383` added glide to
the safety allow-list without updating the routing test), and committed the
in-flight MCP pass-through work that was already in the tree so the committed
state matches the tested state.

## Plans worked on

- No new plan (a provider adapter is config + one register call per the
  registry's design note; the probe artifacts are the acceptance evidence).
- The probe record: `/tmp/three_way_probe.py` output — route task: direct
  0.002/0.008/0.187, synth 0.978/0.994/0.843 (d1/glide/sd-1), identical shape
  picks; validate task: GOOD atq 0.940/0.846/0.660, contradicts 0.002/0.015/0.483;
  DRIFT atq 0.905/0.251/0.413 — GLiDE the only stable critic, d1 noisy on prose
  (0.622→0.905 across wordings).

## Commits

| Hash | Message |
|------|---------|
| `701b44a` | feat: ScaleDown classify-1 provider (the Jev mirror) + MCP provider pass-through; stale safety-routing test fixed |

## What was added

- `_scaledown_wire` in `providers.py` — POST `/v1/scaledown` `{model, state,
  questions}`; plain-string state bounded by `scaledown_max_chars` (1M-token
  context); Bearer auth; usage maps input/output tokens **plus `cost_usd`**
  (ScaleDown bills input only — output free).
- `Head.to_scaledown_head()` in `types.py` — the two wire deltas: noul takes
  the question as `instructions` (NOT true/false criteria like Drex/GLiDE);
  score criteria is an **ordered array** (a map 422s — discovered by hitting it).
- `parse_scaledown` in `parsers.py` — the documented Jev mirror: `choice`→
  `label` via `_jev_entry`; noul p-only (gating derives `max(p,1-p)`, family-
  consistent); 0-indexed score shifted by `score_min` with dict-keyed
  legend/probabilities.
- Config defaults: `scaledown_api_url` (`https://api.scaledown.xyz/v1/scaledown`),
  `scaledown_model` (`classify-1`), `scaledown_api_key_env`, `scaledown_max_chars`.
- `heads_for`: scaledown inherits the jev head set (Jev-class, like drex/glide/
  julia/liquid). Core roster 8→9; README table + counts updated.
- Tests: `TestScaledownProvider` — key gate, wire+usage+cost_usd, state
  truncation, 422 envelope, head inheritance — all offline via mocked urlopen.
- Live proof: `sys1.classify('scaledown', 'change', <auth diff>)` → risk_tier
  high (0.68), personal_data 0.65, 4.2s, $0.0000614, HTTP 200.

## What was fixed

- **Stale test, red tree**: `test_task_allowlist_hard_filter` expected the
  pre-`c1f7383` safety allow-list. Root cause via `git log -S`: glide was added
  to `_SAFETY.providers` deliberately in `c1f7383` and the routing test was
  never updated — the committed tree had been failing. Test corrected to the
  real allow-list (drex filtered, glide/jev route).
- The in-flight MCP work already in the working tree (api.py provider
  pass-through, tasks.py's `route` task, mcp_server.py, the env-plan doc) was
  committed together with the provider so the committed tree matches the tested
  state — flagged to the user in case that work was meant to stay uncommitted.

## Files changed

- `src/sys1/providers.py` — `_scaledown_wire` + register + `_requires_key` entry
- `src/sys1/types.py` — `Head.to_scaledown_head()`
- `src/sys1/parsers.py` — `parse_scaledown`
- `src/sys1/config.py` — scaledown defaults
- `src/sys1/tasks.py` — heads_for inheritance (plus the pre-existing route task)
- `tests/test_providers.py`, `tests/test_provider_flags.py` (CORE roster),
  `tests/test_routing.py` (stale-test fix)
- `README.md` — provider row + roster counts

## Doc updates applied

- README provider table + counts, this recap.

## Open questions / next steps

1. **Calibration**: classify-1 is a route seat on one probe. Fit its floors
   from the JSONL stream before admitting it to any default chain or gate.
2. **The verify-stage experiment** (the user's dev-process thesis): today's
   blend1 validation-gate work as ground truth, acceptance criteria as noul
   heads, providers rating the same artifact — now with a three-way comparison
   available.
3. `feature/plan-surface` carries both this commit and the MCP work; main
   merge still needs the user's explicit approval per CLAUDE.md.
4. Untracked in the tree (not touched, not mine): `.agents/skills/sys1-context/*`,
   `.v2c/`, `.video_agent/`, `.zcodeignore`.
