# sys1

> Reusable System One decision-model abstraction: Jev, GLiNER-2.5-Decide, local GLiNER, ModernBERT — one Python API, one wire shape, normalized results, calibration telemetry.
> Repo: https://github.com/adelvillar1/sys1

**Provider API keys and local venv paths are in `CLAUDE.local.md` (gitignored, auto-loaded alongside this file).** Read it before any provider, service, or credential operation.

---

## Hard rules (non-negotiable)

- **Stdlib-only at runtime.** `dependencies = []` in `pyproject.toml` is a contract. FastAPI/uvicorn are `[service]` extras; the GLiNER/torch stack lives in a uv-managed venv that `src/sys1` shells out to via `subprocess`, never imports.
- **JSONL log keys are a public contract.** `events.jsonl` shape (`op`, `task`, `provider`, `providers_used`, `answers`, `telemetry`, `verdict`, `escalated`, `latency_ms`, `input_sha256`) is consumed by dev-decisions' dashboard and the calibration tooling. Adding keys is fine; renaming or removing is a breaking change.
- **Fail open, never raise out of logging.** A broken provider must not break the gate (`api.py` catches all provider exceptions); `telemetry.log_record` swallows every write error. Preserve both invariants.
- **Never print, log, or return API key values.** `provider_config_view` exposes whether a key env var is *set*, never its value. Keep dashboards and error strings on that side of the line.
- **Confidence floors are per-provider, not global.** Jev's confidence is a distribution-shape statistic; Decide/local/ModernBERT use max-probability. Never threshold them with the same number — see `gating._DEFAULT_FLOORS`.
- Default operation target is `main`. Feature branches are normal for work; merging to `main` requires explicit user approval *in the current turn*.

---

## Branch → environment topology

| Branch | Environment | Auto-deploy | Notes |
|--------|-------------|-------------|-------|
| `main` | library release | no | Published library; `pip install sys1` |
| `feature/*` | local dev | no | Current: `feature/provider-flags-and-dashboard` |

(No remote environments — sys1 is a library. The optional HTTP service runs wherever the consumer deploys it; see `service/Dockerfile`.)

---

## Where to find things

**Architecture**
- Design fragments (per-subsystem, source-cited) → `docs/context/README.md`; start there before touching a subsystem. The `sys1-context` skill loads the right fragment for a file or area.
- Tech stack & module map → `docs/architecture/overview.md`

**Features**
<!-- Add per-feature deep dives here as you build them -->

**Reference**
- Business context → `docs/BUSINESS-CONTEXT.md`
- Troubleshooting → `docs/TROUBLESHOOTING.md`
- State snapshot (test counts, provider registry) → `docs/STATE-SNAPSHOT.md`
- Recent sessions → `docs/recaps/`
- Custom task examples → `examples/`

---

## Contracts (the artifacts we treat as commitments)

Three layers of documentation act as contracts and must stay in sync as the codebase evolves. Updating them is part of finishing work, not a separate optional task.

| Contract | Location | Cadence | Purpose |
|----------|----------|---------|---------|
| **Plans** | `docs/plans/YYYY-MM-DD-<slug>.md` | Per feature | Pre-work contract: what we're building, acceptance criteria, linked docs to update |
| **Recaps** | `docs/recaps/SESSION-RECAP-YYYY-MM-DD.md` | Per session | Post-work journal: what shipped, criteria status, doc-update follow-ups |
| **Technical reference** | `TECHNICAL-DOCUMENTATION.md` (top level) | Per feature | Developer-onboarding contract: module map, wire shapes, provider/task registries |
| **Functional reference** | `FUNCTIONAL-SPECIFICATIONS.md` (top level) | Per feature | User-flow contract: gating behavior, CLI commands, service endpoints |

**The cycle is**: draft a plan → implement → write a recap → update the technical and functional contracts. Each phase produces an artifact the next phase consumes.

**Fragment upkeep** runs alongside the cycle: before a push, `bash .agents/skills/sys1-context/scripts/drift.sh` maps changed sources to the `docs/context/` fragments that document them; update the affected fragments and commit docs with the code (same commit). `python3 .agents/skills/sys1-context/scripts/build-map.py` regenerates the index after editing frontmatter. Plans/recaps are never fragments.

For trivial changes (typos, single-line fixes, dependency bumps), the cycle compresses: skip the plan, write a brief recap, no contract-doc updates needed.

---

## Common commands

```bash
.venv/bin/python -m pytest -q          # full test suite (233 tests)
.venv/bin/python -m pytest tests/test_gating.py -q   # one module
.venv/bin/ruff check .                  # lint (config in pyproject.toml)
.venv/bin/python -m sys1.cli doctor     # provider availability snapshot
.venv/bin/python -m sys1.cli providers --all   # registry incl. disabled candidates
.venv/bin/python -m sys1.cli classify --provider decide --task change -i diff.txt
.venv/bin/python -m uvicorn service.main:app --port 8400   # service + dashboard
.venv/bin/python scripts/verify-dev-decisions-parity.py     # dev-decisions compat check
```

---

## Today's state

- v0.2.0 on `feature/plan-surface` (continued from `feature/provider-flags-and-dashboard`: provider flags + control dashboard + noul gating/per-head floors/recipe pack + fastino-prescribed local wire + plan-surface layers), unmerged to `main` (which is at v0.1.0).
- 347 tests passing, `gating.py` at 100% (re-verified 2026-10-05).
- Ruff clean (`ruff check .` → 0 errors, re-verified 2026-10-05; the 2026-10-01 sweep plus this cycle's repairs — I001/F401 in an untracked skill script, RUF022 in `__init__.py`; note `make_score(legend=...)` raises on a legend/level-count mismatch via `zip(strict=True)`).
- Roster (2026-10-05): core = decide, jev, drex, glide, julia, liquid, local, modernbert; `routing.route()` builds the cascade's default chain (`glide,drex,jev`, size-gated, hosted-only; julia + liquid registered but calibrated OUT of the chain — liquid awaits its calibration gate, and its 150K-char bound needs no size-gate exclusion). Plans `2026-09-30-drex-provider`, `2026-10-01-glide-provider-and-router`, `2026-10-02-julia-provider`, and `2026-10-05-liquid-d1-provider` completed. Liquid is the pool's only vision-capable provider: `classify()`/`cascade_classify()` take an optional `images` list (OpenJev extension); the `d1:free` model is never exercised (user constraint 2026-10-05).
- The 6 defects from the 2026-09-28 review are fixed (plan `2026-09-28-sys1-correctness-fixes` landed as `9e13e16`); the noul-gating plan completed the same day.
- Active plan: `2026-09-28-fastino-decide-adoption` — sys1 is the one fastino integration; `local` uses the vendor-prescribed `gliner2.classification` API (one decode, full probabilities) with a load-once classifier server (`local_server_url`) or subprocess fallback; the service accepts inline `task_spec`; consumers (dev-decisions, zcode-router) move onto sys1 per the plan. `decide` id = hosted wire; the Decide model is what `local` runs.

---

## Housekeeping protocol — keeps the docs tree from rotting

**You are responsible for keeping the docs tree current.** The whole point of slimming this file was to push reference material into topical files; that only works if those files stay fresh. After every session that changes something material, do the following before the user wraps up (or when writing the session recap):

1. **Identify which doc(s) the change touched.** Use the "Where to find things" map above. Changes to a feature → the matching `docs/features/*.md`. Connection-string or key-env change → `CLAUDE.local.md`. Wire-shape or module-map change → `docs/architecture/overview.md`.
2. **Update the relevant doc inline.** Don't leave the new fact only in a session recap. Recaps are journal entries; topical docs are the source of truth.
3. **Stat tables go in `docs/STATE-SNAPSHOT.md`, not in topical docs.** When refreshing the snapshot, **replace** it — do not append a new dated section.
4. **CLAUDE.md (this file) only gets updated when:** a hard rule changes; a new top-level docs area is added; the topology changes; or "Today's state" needs a refresh. **Never** add narrative changelogs or feature deep-dives here.
5. **`CLAUDE.local.md` updates require care.** It's gitignored, so changes leave no trace in `git log`. When you rotate a credential or URL, **mention it in the session recap** — that line is the only trace.
5a. **Contract docs (`TECHNICAL-DOCUMENTATION.md`, `FUNCTIONAL-SPECIFICATIONS.md`) must stay in sync with code.** Never declare a feature done while these are stale — track the gap as known debt in the recap if you must defer.
6. **Drift check — run before any session ends with non-trivial doc changes:**
   - `wc -l CLAUDE.md` → should stay ≤ ~300 lines. If growing, move content out.
   - `git status` → must NOT show `CLAUDE.local.md`.
   - For every doc path mentioned in CLAUDE.md, confirm the file exists.
7. **When the user reports "Claude pointed at the wrong provider/env" or "asked me for a key again":** that's a `CLAUDE.local.md` bug. Patch the local file. **Do not just answer the question — fix the source.**
8. **Quarterly hygiene pass:** re-snapshot `docs/STATE-SNAPSHOT.md`; archive recaps older than 90 days into `docs/recaps/archive/`; remove `Updated YYYY-MM-DD` markers from topical docs.

---

## Session protocol

**At the start** of any non-trivial session, run `/warmup`. It loads CLAUDE.md, the latest session recap, all active plans in `docs/plans/`, and the relevant feature/architecture docs before answering anything.

**At the end** of any session that touched anything, run `/wrapup`. It verifies a recap exists, checks "Today's state" freshness, asks for disposition on uncommitted changes, transitions plan status, escalates deferred-update debts, runs drift checks, and drafts a next-session preview.

For trivial sessions (typo fix, one-line change), skip warmup and skip the recap, but **still run `/wrapup`**.

Otherwise:

1. Read this file (auto-loaded).
2. Read `CLAUDE.local.md` if you need any key env var, venv path, or URL (auto-loaded).
3. For task-specific context, read the relevant `docs/` file from the "Where to find things" map.
4. When changes are made, follow the housekeeping protocol above.

**The six-stage cycle**: `warmup → plan → build → recap → wrapup → next session`. Skip the plan for trivial work; skip `/warmup` and `/recap` for trivial sessions; **never skip `/wrapup`** if the session touched anything.
