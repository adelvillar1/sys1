---
title: Config — defaults, TOML discovery, env overrides, runtime flag state
status: shipped
sources:
  - src/sys1/config.py
  - service/Dockerfile
related:
  - architecture/providers-and-flags.md
  - interface/service.md
---

# Config

`config.py` produces the config dict every module reads. The shape mirrors dev-decisions 1:1
(`scan`, `classify`, `providers`, `log`) so existing TOML files and the JSONL log keys stay
compatible.

## Merge order in `load_config` (later wins)

1. `DEFAULTS` (deep-copied — nested sections like `providers.route` must not be shared by reference
   across calls; a caller mutation would leak into every later load)
2. discovered TOML files: `~/.config/sys1/config.toml`, then `~/.config/dev-decisions/config.toml`
   (compat), then caller `extra_paths`, then repo-local `.sys1.toml` / `.dev-decisions.toml`
3. env vars: `SYS1_` prefix, `__` as the section separator (`SYS1_PROVIDERS__JEV_MODEL=...`),
   values coerced by `_coerce` (bool/int/float/str)
4. the `SYS1_LOG_DIR` alias binds to `classify.log_dir` (the generic loop can only produce a
   top-level `log_dir` nothing reads); a non-empty `SYS1_CLASSIFY__LOG_DIR` suppresses the alias
5. the runtime state file (below) — it rewrites `providers.enabled`/`providers.disabled` and wins
   over TOML/env

A broken TOML or state file never breaks the tool: parse failures are swallowed.

## Runtime flag state

`state_file_path` (override: `SYS1_STATE_FILE`; default `~/.config/sys1/state.json`) persists
dashboard toggles. `write_state_flags` writes `{providers: {enabled: [...], disabled: [...]}}`;
`_apply_state_file` folds it back in on the next `load_config`. This is how the control dashboard's
on/off switches survive restarts and outrank config files.

## Where the keys live

Provider endpoints/models/key-env-names are `providers.*` keys in `DEFAULTS` (decide/jev/drex/
glide/julia/liquid urls and `*_max_chars` bounds, candidate endpoints on localhost ports,
`local_venv`, `local_server_url`). API keys themselves are only ever env var *names* in config —
`api_key_for` resolves `providers.{pid}_api_key_env` and reads the environment; legacy
dev-decisions names (`FASTINO_API_KEY`, `TYPESAFE_API_KEY`) are the hardcoded fallback.
`load_provider_env` runs inside `load_config` and setdefaults KEY=VALUE lines from
`$SYS1_ENV_FILE` / `~/.config/sys1/env` (legacy fallback `~/.config/dev-decisions/env`) into
`os.environ`, so hosted providers work from any shell without caller-side sourcing; an
already-exported value always wins. The repo's gitignored `.env` is the local reference copy of
the same keys (user directive, 2026-10-02) — nothing in the code reads it.

## Gotchas

- `SYS1_LOG_DIR` is the documented single-name override (service/Dockerfile sets it); use the
  section-qualified `SYS1_CLASSIFY__LOG_DIR` when you need it to always win.
- `request_timeout_seconds` doubles for ModernBERT subprocess runs (slower on CPU).
