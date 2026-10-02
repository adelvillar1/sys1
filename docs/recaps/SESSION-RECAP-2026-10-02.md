# Session recap — 2026-10-02

**Plan link**: `docs/plans/2026-10-02-julia-provider.md` (completed)

## What shipped

- **`julia` — new core provider** (Supersoniclabs, `julia-1`): BERT-based decision model on the `/v1/systemone` wire, $0.025 per 1M input tokens / $0 output, 8,192-token window, fan-out native (6 heads in one request, ~0.5s), single-call latency 0.5–1.2s. Contract verified with 8 live probes before wiring.
- **Wire deltas encoded + tested** (all probe-backed, quoted in the plan Context):
  - `Idempotency-Key` header REQUIRED (missing → 400 `idempotency_key_required`) — one fresh uuid per logical call, reused across retries.
  - `User-Agent` header REQUIRED: Cloudflare 403s (error 1010) Python-urllib's default signature — the exact UA `http_post_json` sends. Scoped to `_julia_wire` via `extra_headers`; other vendors' wires untouched.
  - 8,192-token window with NO structured overflow error (~9.7K tokens → raw HTTP 502) — client-side `julia_max_chars` (12,000) is the only guard.
  - Score takes `criteria` as an ordered ARRAY (map → 422; Jev's `levels` → 422); `to_drex_head()` is again a drop-in.
  - Score 0-indexed like Drex/GliDE but **`legend` arrives as `null`** — `parse_julia` synthesizes it from `head._score_criteria()` (the array the wire sent).
  - **Confidence = top1 probability** on choice/score (byte-equals `probabilities[top1]`) — kept as returned, directly thresholdable at the 0.7 floor (unlike GliDE's margins). noul has no confidence field on the wire; gating derives `max(p, 1−p)` with nothing to strip.
- **Calibration gate FAILED for chain admission — julia is NOT in the default chain** (`glide,drex,jev` unchanged). Four-way six-text corpus: julia agreed with glide AND jev only **1/6 on diff_type and 1/6 on risk_tier** (refactors → docs/test; a risky big-bang deploy → risk=low 0.85; breaking changes under-detected at 0.21–0.25 vs family 0.9+; overconfident on disagreeing labels). Position per the house rule (route on capacity+shape+calibration, never cost): cheap fast triage head for bulk classification, or per-task via `providers.route.by_task` with fitted floors. `julia` IS in `_SIZE_EXCLUDED` so custom chains respect the size gate. Evidence: `/tmp/julia_calibration_2026-10-02.json`.
- Incidental: GLiDE's "~free" billing claim (user-corrected) is now fixed in README's provider row and TECHNICAL-DOCUMENTATION's GLiDE section too — the earlier fix had landed only in config.py's comment.

## Credentials (housekeeping rule 5)

`SUPERSONIC_API_KEY` stored in repo `.env` (gitignored) at probe time — sys1/.env stays the canonical home for all sys1 decision-model keys (user directive, 2026-10-02). `CLAUDE.local.md` key table gained the SUPERSONIC row (name only, never values). Note: the key was pasted into chat by the user during the session — rotation is cheap if that ever matters.

## Criteria status

All 10 acceptance criteria checked — evidence in the plan's Outcome section. **321 tests passing** (13 new julia tests); ruff clean for all of `src/` + `tests/`. Live: `sys1.classify('julia', 'change', ...)` → 4 heads parsed in 1.4s; routing verified unchanged for short/long inputs; roster shows `[on ] julia  jev  configured → available`.

## Notable

- First-connection TLS stall to api.supersoniclabs.ai observed exactly once (curl error 35), never recurred across ~10 calls; `http_post_json`'s network-error retry covers it.
- Repo-root `ruff check .` currently reports 8 errors — all inside the concurrent plan-surface session's untracked files (`src/sys1/plansurface.py`, `scripts/probe-plan-replay.py`, `tests/test_plansurface.py`, `.agents/skills/sys1-context/scripts/build-map.py`). Not this plan's scope; that session owns them.
- This session also re-ran the full glide live battery (raw curl probe, cascade, routing, three-way calibration) after the user questioned yesterday's "failed tests" — everything passed with numbers identical to 2026-10-01; the hosted API was never broken (the 422s were `options`-shaped choice probes, client-side). See the glide plan's 2026-10-01 Outcome + the memory note.
- Keys canonicalized per user directive: `TYPESAFE_API_KEY` copied from `~/Projects/TTESS/.env` into repo `.env`; the routed cascade walked `glide → drex → jev` live for the first time (reason `default`).

## Doc updates applied

- `TECHNICAL-DOCUMENTATION.md` — new "Julia-1 deltas (probed live 2026-10-02)" section; GLiDE section billing line corrected.
- `README.md` — provider table gains `julia`; glide row billing corrected.
- `CLAUDE.local.md` — SUPERSONIC key row + canonical-keys note.
- `docs/plans/2026-10-02-julia-provider.md` — status `completed`, full per-criterion Outcome.

No deferred doc updates.

## CLAUDE.local.md changes

- Provider-key table: `SUPERSONIC_API_KEY` row added (set, repo `.env`); `TYPESAFE_API_KEY` row updated to "set"; `.env` note extended (all hosted keys canonical in repo `.env`; candidate-tier keys don't exist on this machine yet).

## Doc-update follow-ups

None outstanding.

## Next-session preview

- If a real bulk-triage workload appears, fit `julia` per-task floors from the JSONL stream and consider a `providers.route.by_task` entry — cost/latency make it attractive where the change taxonomy isn't the contract.
- The plan-surface session's files (`plansurface.py` et al.) are uncommitted in the working tree — that session owns their commit; after it lands, repo-root `ruff check .` should return to 0.
- dev-decisions-side adoption (routing a real gate through the cascade) remains a separate plan.
