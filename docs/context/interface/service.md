---
title: The service — FastAPI wrapper, classifier server, control dashboard
status: shipped
sources:
  - service/main.py
  - service/dashboard.py
  - service/classifier_server.py
  - tests/test_service.py
  - tests/test_dashboard.py
related:
  - interface/api.md
  - architecture/config-and-flags.md
  - architecture/telemetry-and-calibration.md
---

# The service

`service/` is a thin FastAPI wrapper around the same `sys1.classify()` the library exposes
(`pip install 'sys1[service]'`); the wrapper adds auth, task introspection, and the calibration
read-out. No decision logic lives here.

## `service/main.py` — the HTTP surface

Bearer auth via `require_auth` (`_secure_eq`, constant-time) on the mutating/read endpoints:
`POST /v1/classify`, `POST /v1/cascade` (both accept inline `task_spec` for dynamic heads via
`tasks.task_from_spec`), `POST /v1/feedback` (writes through `telemetry.record_feedback`), and
`GET /v1/calibration` (reads the JSONL log back through `calibration_curve` over a `days` window).
Unauthenticated: `GET /v1/health`, `GET /v1/tasks` + `/v1/tasks/{task_id}` (the registry with head
specs), `GET /` (capability index), `GET /healthz`. Responses reuse `ClassifyResult.as_dict()`.
`SYS1_LOG_DIR` is honored for every log read and write (fixed 2026-09-30).

## `service/classifier_server.py` — the load-once GLiNER server

A stdlib `http.server` exposing `POST /infer` with `{text, tasks, model}`: `load_engine` keeps one
model resident, `build_schema` translates the pure spec (same shape as
`providers.classification_schema_spec`) into a `ClassificationSchema`, `extract` returns labels +
full probability distributions. This is the "server mode" the `local`/`decide_1b`/`decide_multi`
providers prefer; the library falls back to subprocess automatically when it is unreachable — see
[providers-and-flags.md](../architecture/providers-and-flags.md).

## `service/dashboard.py` — the control dashboard

`GET /providers` (`provider_payload` = `providers.provider_report`) and `POST /providers/{pid}/flag`
(`_set_flag` → `config.write_state_flags`, the persisted runtime toggle) plus `POST
/providers/{pid}/probe` (the cheap reachability probe). `GET /recent` reads the JSONL stream for the
calls view. Auth mirrors main.py. **Never expose or log key values** — the config view carried to
the UI is `provider_config_view`, which reports `api_key_set` as a boolean only.

## Gotchas

- The dashboard's flag toggles rewrite `providers.enabled/disabled` in the state file, which
  **outranks TOML/env** on the next `load_config` — a confusing "I set it in config but it's off"
  is the state file winning.
- `build-schema` translation exists in two places (server + `_LOCAL_INLINE`); keep them identical.
