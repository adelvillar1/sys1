# Session recap — 2026-09-30

**Plan link**: `docs/plans/2026-09-30-drex-provider.md` (completed)

## What shipped

- **`drex` — new core provider** (Nace, https://drex.nace.ai): Jev-class decision model on the `/v1/systemone` wire with a 131,072-token state window (`drex-v1.5`). Added to the roster alongside decide/jev/local/modernbert, enabled by default (`providers.enabled = "core"`).
- Wire deltas vs Jev, all encoded + tested: plain-string `state` bounded by `drex_max_chars` (400k, not the diff truncation), noul criteria under `true`/`false` keys (`Head.to_drex_head`), score shifted from Drex's 0-indexed expected level to sys1's `score_min`-based scale + legend array→dict (`parse_drex`), usage `input_tokens`/`output_tokens` mapped onto the contract telemetry keys (+ `evaluation_time_ms`, `request_id` extras).
- `heads_for(task, "drex")` inherits the `jev` head set wherever a task defines one — the Jev-replacement property.
- Floor registered: `gating._DEFAULT_FLOORS["drex"] = 0.7` (Jev-family semantics).
- Retry policy matches Drex docs: 429/529/500 retryable, 401/402/422 fail fast (via existing `http_post_json`).

## Credentials (housekeeping rule 5)

Updated `CLAUDE.local.md`: added `DREX_API_KEY` row to the provider-key table. The key itself lives in **`<repo>/.env`** (new file, gitignored via a new `.gitignore` entry — this is sys1's first `.env`; user-directed). `DREX_BASE_URL=https://drex.nace.ai` also in `.env`. Key was verified 2026-09-30 (noul 0.931, 8.8ms) before wiring.

## Criteria status

All 7 acceptance criteria checked — evidence in the plan's Outcome section. 249 tests passing (12 new), ruff clean on all new code (16 pre-existing errors unchanged). Live end-to-end through `sys1.classify('drex', 'change', ...)`: 4 heads parsed, verdict `escalate` (risk_tier 0.32 < 0.70 floor), 237 prompt tokens, 303ms.

## Notable

- `jev` itself currently reports "missing API key: $TYPESAFE_API_KEY" in this environment — **drex is the only configured Jev-class provider right now**, so the replacement is already the working option.
- Docs updated: TECHNICAL-DOCUMENTATION (wire table + new "Drex deltas" section), README (provider table row).

## Doc-update follow-ups

None outstanding. Uncommitted on `feature/provider-flags-and-dashboard`: `.gitignore` (+`.env`), the drex provider work, plan, recap. Commit when ready.

## Next-session preview

Jev-parity measurement (the option the user deferred): run dev-decisions tasks through drex vs jev side-by-side once a TYPESAFE_API_KEY is available. dev-decisions-side adoption (pointing a gate at drex) is a separate plan.
