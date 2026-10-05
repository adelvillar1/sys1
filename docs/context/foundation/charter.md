---
title: sys1 — charter (what it is, the contracts it keeps)
status: foundational
sources:
  - README.md
  - CLAUDE.md
  - src/sys1/__init__.py
  - pyproject.toml
related:
  - architecture/providers-and-flags.md
  - interface/api.md
---

# sys1 — charter

sys1 is a reusable System One decision-model abstraction: one Python API over several small
"decision" providers (Jev, GLiNER-2.5-Decide, local GLiNER, ModernBERT, Drex, GLiDE, plus wired
candidates), one normalized answer shape, and JSONL calibration telemetry. `sys1.classify(...)`
(exported from `src/sys1/__init__.py`) is the one function most callers need.

## The contracts that shape everything

- **Stdlib-only at runtime.** `dependencies = []` in `pyproject.toml` is a contract. FastAPI/uvicorn
  are the `[service]` extra; the GLiNER/torch stack lives in a uv-managed venv that `src/sys1`
  shells out to via `subprocess`, never imports.
- **The JSONL log keys are a public contract.** dev-decisions' dashboard and the calibration tooling
  consume `events.jsonl`. Adding keys is fine; renaming or removing is breaking. See
  [telemetry-and-calibration.md](../architecture/telemetry-and-calibration.md).
- **Fail open.** A broken provider must not break the gate: `classify` catches all provider
  exceptions per provider, and `telemetry.log_record` swallows every write error.
- **Never surface API key values.** `provider_config_view` exposes whether a key env var is *set*,
  never its value.
- **Confidence floors are per-provider.** Jev confidence is a distribution-shape statistic;
  Decide/local/ModernBERT use max-probability. Numbers never cross providers — see
  [gating.md](../architecture/gating.md).
- **Consumer compatibility.** The config dict, answer wire shapes, and exit codes mirror
  dev-decisions 1:1 so existing config files, dashboards, and git hooks keep working unchanged.

## Repository layout

`src/sys1/` is the library (11 modules); `service/` is a thin FastAPI wrapper around the same
`classify()`; `examples/` are runnable TOML task packs and scripts; `tests/` mirror the modules.
`docs/plans/` and `docs/recaps/` are session contracts — they are NOT part of the fragment set.

## Provider policy (as of 2026-10)

Hosted fastino (Decide, GLiDE) and TypeSafe Jev are the active providers; Drex is core with a key.
The `local` GLiNER and `modernbert` paths are optional and not relied on. Candidates (CLM-8B,
openjev, Kev, Tev1, Laya, decide_1b, decide_multi) are wired but disabled until listed in
`providers.enabled`.
