# Session recap — 2026-10-01

**Plan link**: `docs/plans/2026-10-01-glide-provider-and-router.md` (completed)

**Commits**: this session's work lands as one consolidated commit on `feature/provider-flags-and-dashboard` — glide provider + cascade routing + ruff debt cleanup + docs. It also lands the previously uncommitted 2026-09-30 drex-provider work that was still in the working tree (same files, layered work). See `git log` for the hash; plans and recaps for both sessions are included.

## What shipped

- **`glide` — new core provider** (Fastino, `fastino/glide`): Jev/Drex-class decision model on the `/v1/systemone` wire, ~free ($0 input / $0.04 per 1M output tokens), 40,000-token rendered-prompt limit. Contract verified with six live probes before wiring (both `Authorization: Bearer` and `X-API-Key` accepted; strict validation like Drex — no extra `id` field, noul criteria must be `true`/`false`; score 0-indexed).
- Wire deltas encoded + tested: plain-string `state` bounded by `glide_max_chars` (100,000 ≈ headroom under 40K tokens), questions reuse `Head.to_drex_head()` verbatim, `parse_glide` shifts the 0-indexed score and legend **dict** keys (Drex sends an array), `expected_level` rides along, usage `input_tokens`/`output_tokens` mapped onto the contract telemetry keys.
- **Confidence policy**: `parse_glide` strips GLiDE's explicit noul confidence (`|2p−1|` scale) so gating derives `max(p, 1−p)` family-consistently; choice/score margins kept. Calibration (6-text corpus) confirmed the choice — glide agreed with jev **6/6 diff_type, 5/6 risk_tier** (drex was the outlier at 3/6), noul p tracked within ~0.05.
- **`routing.route()` — new module** (`src/sys1/routing.py`): deterministic chain policy for `cascade_classify` when no explicit chain is passed. Per-task override (`providers.route.by_task`) → default `glide,drex,jev` (hosted-only by design; `local`/`modernbert` never auto-routed) → size gate (past `glide_max_chars`, glide moves behind drex) → availability filter → `Task.providers` hard filter. Explicit `provider_chain` args bypass it; `providers.route.enabled = false` restores the legacy `["decide", "jev"]`. Route decisions logged as `route_chain` / `route_reason` (added keys only).
- `heads_for(task, "glide")` inherits the `jev` head set like drex. Floor registered: `gating._DEFAULT_FLOORS["glide"] = 0.7`.
- Incidental fix: `load_config` now deep-copies `DEFAULTS` — the nested `providers.route` dict exposed that nested sections were shared by reference across loads (caller mutations would leak globally).

## Credentials (housekeeping rule 5)

`FASTINO_API_KEY` copied from `~/Projects/TTESS/.env` into `<repo>/.env` (gitignored) and verified live before wiring. `CLAUDE.local.md` provider-key table updated (`glide` added to the FASTINO row; noted `TYPESAFE_API_KEY` is available in `~/Projects/TTESS/.env` if a jev parity run needs it — still not in this repo's `.env`).

## Criteria status

All 14 acceptance criteria checked — evidence in the plan's Outcome section. 276 tests passing (15 new routing + glide tests), ruff clean (`ruff check .` → 0 errors: the 16 long-standing pre-existing errors were also fixed this session — see below). Live: `sys1.classify('glide', 'change', ...)` → 4 heads parsed (noul fields confidence-free as designed); cascade on a short input walked `glide → escalate → drex won`; long input routed drex-first; parity script exit 0.

## Ruff debt cleared (2026-10-01)

The 16 pre-existing errors documented in CLAUDE.md are fixed, none silenced: cli.py file read via context manager (SIM115); tasks.py ambiguous `l` renamed to `label` ×7 (E741) and `detect_task_from_text` now actually uses its `doc_patterns` list — `.rst`/`.txt`/`documentation/` inputs route to docs_drift as the list always implied (F841); telemetry.py iterates values instead of unused keys (B007) and drops a redundant `int()` around `math.floor` (RUF046); types.py `make_score` zips legend with `strict=True` — a legend/level-count mismatch is now a loud authoring error instead of silent truncation (B905); `__all__` sorted isort-style with the stale group comments replaced (RUF022); the three nested `with` blocks in test_providers.py combined (SIM117).

## Notable

- Session-process sweep at wrap-up: registry empty (0 registered processes this session, nothing killed).

- GLiDE latency is bimodal (1.1–1.7s warm, ~7.8s common, 17.5s tail observed) — it's the cheap cascade head, not the latency pick. Jev/Drex sit at 0.17–0.37s.
- Glide's choice/score margins run systematically lower than Jev's concentration statistic (e.g. 0.63 vs 0.96) — the shared 0.7 floor is *stricter* for glide on choice heads. Tune `glide_confidence_floor` per task if a glide-first cascade escalates more than wanted.
- Docs updated: TECHNICAL-DOCUMENTATION (wire table, "GLiDE deltas" + "Routing" sections), README (provider row, component table, provider-naming note).

## Doc updates applied

- `TECHNICAL-DOCUMENTATION.md` — wire table gains `glide`; new "GLiDE deltas (probed live 2026-10-01)" and "Routing (cascade default chains)" sections; telemetry record fields note `route_chain`/`route_reason`.
- `README.md` — provider table gains `glide`; component table gains `route()` and the parse/wire rows are current; provider-naming note now describes the routed cascade default.
- `FUNCTIONAL-SPECIFICATIONS.md` — §2 Cascade Classification documents the routed default chain and the escape hatch.
- `CLAUDE.md` — "Today's state" refreshed: 276 tests, ruff clean, 6-provider core roster, routing, completed plans.
- `docs/plans/2026-10-01-glide-provider-and-router.md` — status `completed`, full per-criterion Outcome section.

No deferred doc updates. `docs/features/` untouched — no feature files cover the provider layer at this granularity (wire deltas live in TECHNICAL-DOCUMENTATION §5, matching the drex precedent).

## CLAUDE.local.md changes

- Provider-key table: `FASTINO_API_KEY` row now covers `decide` + `glide` and reads "set" (lives in repo `.env`, copied from `~/Projects/TTESS/.env`); `TYPESAFE_API_KEY` row notes the key is available in `~/Projects/TTESS/.env` if a jev parity run needs it. `.env` note extended with `FASTINO_API_KEY`. (No secrets pasted.)

## Doc-update follow-ups

None outstanding. Everything listed above is in the same commit as the work that required it.

## Next-session preview

- Decide whether `jev` gets a key in this repo (TYPESAFE_API_KEY sits in TTESS/.env) now that the routed default chain nominally includes it third.
- If glide-first cascades escalate too often on a real workload, fit `glide_confidence_floor` from the JSONL calibration stream instead of sharing 0.7.
- dev-decisions-side adoption (routing a real gate through the cascade) remains a separate plan.
