---
status: active
created: 2026-10-04
updated: 2026-10-04
slug: canonical-provider-env
---

# Plan: sys1 self-loads the provider env file

## Context

sys1 is the canonical provider layer for every sys1-shaped call — that is why
it was built — but its provider availability silently depends on the
*caller's shell*: `config.py` only reads `os.environ`, so hosts whose keys
live in an env file (`~/.config/dev-decisions/env`) register no hosted
providers unless the caller remembered to source it first. On 2026-10-03/04
this misread as "all hosted providers down" across three sessions
(`no provider answered (chain: jev)`, doctor showing only
`local, modernbert`) — implausible for decide (fastino.ai), jev
(typesafe.ai), and drex (nace.ai), which are separate hosted environments.
The layer must self-configure.

## Approach

`config.py` gains `load_provider_env()`: parse `KEY=VALUE` lines from a
well-known env file and `setdefault` them into `os.environ` — the caller's
exported values always win. Path resolution mirrors the TOML discovery that
already treats dev-decisions as a compat source: `$SYS1_ENV_FILE` override →
`~/.config/sys1/env` (canonical) → `~/.config/dev-decisions/env` (legacy
fallback, no key migration required). `load_config()` calls it first, before
the TOML merge, so every consumer path (api entry points, provider registry,
doctor, the dev-decisions CLI) gets the full pool with zero caller-side
changes. Fail-open per house rules: a broken file skips silently, names are
returned but values never are.

Decision-model check: deterministic file parse + setdefault; no judgment.

High-level steps:

1. `env_file_path()` + `load_provider_env()` in `config.py`; export both
   from `__init__`; call at the top of `load_config()`.
2. `tests/test_config_env.py`: setdefault semantics (exported value wins),
   canonical-over-legacy resolution, `SYS1_ENV_FILE` override, comment/
   export-prefix/malformed-line tolerance, missing file no-op, values never
   appear in the return.
3. Contract docs: TECHNICAL-DOCUMENTATION (config discovery), FUNCTIONAL-
   SPECIFICATIONS (provider availability behavior).

## Use cases

- [ ] A: a consumer (dev-decisions gate, a script, cron) calls sys1 from a shell that never sourced any env file — hosted providers must still answer

## Acceptance criteria

These are the contract. Each one is a verifiable assertion. The work is "done" when every box is checked. **Order is identity** — criteria are C0, C1, … in checkbox order.

- [x] With no key variables exported, `sys1.load_config()` on this machine registers the hosted providers (doctor shows decide, jev, drex, glide alongside local/modernbert)
- [x] A key exported in the caller's environment always wins over the env file's value (setdefault semantics)
- [x] `~/.config/sys1/env` takes precedence over `~/.config/dev-decisions/env`, and `SYS1_ENV_FILE` overrides both
- [x] Comment lines, `export ` prefixes, blank lines, and malformed entries are skipped without error; a missing or unreadable file is a no-op
- [x] `load_provider_env()`'s return exposes file path and key NAMES only — no values
- [x] `python3 -m pytest tests/test_config_env.py` passes; the existing suite stays green

## Files to be touched

**Backend / API:**
- `src/sys1/config.py` — `env_file_path()`, `load_provider_env()`, hook in `load_config()`
- `src/sys1/__init__.py` — exports

**Tests:**
- `tests/test_config_env.py` — new

**Documentation (see Linked artifacts below):**
- `TECHNICAL-DOCUMENTATION.md` — config discovery section
- `FUNCTIONAL-SPECIFICATIONS.md` — provider availability behavior

## Out of scope

- Migrating keys out of `~/.config/dev-decisions/env` (the fallback reads it in place; move is the user's call)
- dev-decisions-side loading or any caller changes (none needed — that is the point)
- The `service/` container path beyond what `SYS1_ENV_FILE` already enables
- knowledge_index.py's direct Drex calls (second-brain repo; flagged separately)

## Verification

**Criterion: pool self-configures (live, this machine)**
```bash
env -u FASTINO_API_KEY -u TYPESAFE_API_KEY python3 -c "import sys1; sys1.load_config(); print(len(sys1.available_providers()))"
```
Expected: hosted providers present without any sourced shell.

**Criterion: caller env wins**
In tests via monkeypatch; live: sourced key keeps its value after load_config().

**Criterion: suite**
```bash
python3 -m pytest tests/ -q
```
Expected: green.

## Linked artifacts

- `TECHNICAL-DOCUMENTATION.md` — config discovery order gains the env file step
- `FUNCTIONAL-SPECIFICATIONS.md` — provider availability: registered from the env file without caller-side sourcing

## Risks

- Loading keys into `os.environ` makes them visible to child processes sys1 spawns (the local GLiNER venv subprocess) — same exposure as a sourced shell; accepted, no values are ever logged or returned.

## Dependencies

- None — config-layer only.
