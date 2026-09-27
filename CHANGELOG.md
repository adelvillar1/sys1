# Changelog

All notable changes to `sys1` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-27

Provider flags + control dashboard: candidate System One models wired and
ready, enable/disable without touching code.

### Added
- **Provider flags** — `providers.enabled` / `providers.disabled` accept
  `core`, `all` / `*`, `optin` / `candidates`, or explicit ids (env:
  `SYS1_PROVIDERS__ENABLED`). `enabled_set()` gates `call_provider`,
  `is_available`, and the API fan-out; a disabled provider fails with the
  exact fix-it hint instead of a bare "unavailable".
- **Seven candidate providers** (registered, off by default, one config
  entry away from live): `clm` (CLM-8B, Jev wire), `openjev` (JevK5, Jev
  wire), `kev` (Apache-2.0, OpenAI wire), `tev1` (Together AI, OpenAI
  wire), `laya` (Convai, OpenAI wire — provisional), and the GLiNER-family
  `decide_1b` / `decide_multi` (same local venv as `local`).
- **Wire factories** — `_openai_wire` / `_jev_wire` / `_gliner_wire`
  generate `classify` + availability from `{id}_api_url`, `{id}_model`,
  `{id}_api_key_env`, `{id}_requires_key`. Adding a model that speaks an
  existing wire is config + one `register()` call.
- **`Provider` metadata** — `tier` (`core`/`candidate`), `wire`,
  `is_configured`, `meta` (setup hints). Custom providers default to
  `core`, so they keep working exactly as before.
- **Reporting API** — `provider_report()` / `availability_reason()` /
  `provider_config_view()` / `probe()` give the dashboard and `sys1 doctor`
  per-provider enabled/configured/available + a human reason.
- **`sys1 providers`** CLI command — core/candidate groups with flag,
  config, and availability state.
- **Control dashboard** (`service/dashboard.py`, mounted at `/dashboard`) —
  provider cards with toggle switches (persist to `~/.config/sys1/state.json`),
  endpoint probes, a live classify console with provider multi-select and
  per-head confidence bars, feedback capture per head (joins the calibration
  log via `input_sha256`), and a recent-JSONL activity table. New endpoints:
  `GET /v1/providers`, `POST /v1/providers/{id}/flag`,
  `POST /v1/providers/{id}/probe`, `GET /v1/recent`.
- **Runtime state file** — `SYS1_STATE_FILE` (default
  `~/.config/sys1/state.json`) written by dashboard toggles, loaded by
  `config.load_config()` and winning over TOML/env; a corrupt file is
  ignored, never fatal.
- **Candidate gating floors** — per-provider confidence-floor defaults for
  all seven candidates in `gating._DEFAULT_FLOORS` (Jev-wire 0.65,
  OpenAI-wire 0.7, Laya 0.6, GLiNER variants 0.65–0.7).
- **Tests** — flag resolution, disabled-gate errors, state-file
  round-trip/priority/corruption, candidate reporting, probes, dashboard
  endpoints, auth boundaries, and the page itself (suite now 126 tests).

## [0.1.0] — 2026-09-27

Initial public release.

### Added
- **Library (`sys1`)** — one-call `classify(provider, task, text, cfg)`
  returning a normalized `{provider → head_id → Answer}` map plus telemetry
  and a pass/escalate/block verdict. Stdlib-only.
- **Four providers** —
  - `decide` (Fastino hosted `GLiNER-2.5-Decide`, OpenAI-compatible)
  - `jev` (TypeSafe `Jev`, `/v1/systemone` with choice / noul / score primitives)
  - `local` (subprocess into a uv-managed venv running `gliner2`)
  - `modernbert` (sentence-encoder eval, no fine-tuning, calibration use)
- **Provider registry** — `sys1.register(Provider(...))` to add your own.
- **Eight pre-registered tasks** — `change`, `commit_audit`, `deps_risk`,
  `docs_drift`, `api_drift`, `pr_gate`, `issue_triage`, `safety`. Heads are
  data-driven; load more from TOML/JSON via `sys1.load_tasks_file(...)`.
- **`cascade_classify()`** — try a chain of providers in order, stop at the
  first that clears the per-head floor. Reconciliation pattern ported from
  `zcode-router-kit`.
- **Gating** — per-provider confidence floors, escalate-vs-block semantics,
  JSONL verdict summary.
- **Calibration telemetry** — JSONL event log, secret redaction, confidence
  histogram, cross-provider agreement ledger, feedback join, calibration
  curve helper.
- **HTTP service** (`service/main.py`) — FastAPI wrapper exposing
  `POST /v1/classify`, `POST /v1/cascade`, `GET /v1/health`,
  `GET /v1/tasks`, `GET /v1/calibration`.
- **CLI** (`sys1 classify`, `sys1 doctor`, `sys1 tasks`, `sys1 version`).
- **Test suite** — 65 tests, recorded provider fixtures, mocked HTTP +
  subprocess for the local venv path.
- **Apache-2.0 LICENSE.**

### Provider facts captured
- Jev confidence is a distribution-shape statistic `(N × max_prob − 1) / (N − 1)`
  for N options; binary collapses to max. Thresholding Jev and Decide at the
  same number is wrong without per-provider floors (gating.py documents this).
- Hosted Decide returns `HTTP 425` while warming — there is no `model_warming`
  request field. `http_post_json` treats a warming `error.message` as retryable.
- `fastino/GLiNER-2.5-Decide` is a `SpanExtractor` despite the "2.5" branding;
  only `multi-Decide` is genuinely boundary-based.

## Unreleased
- Cross-language HTTP client (JS/TS package) wrapping the service.
- Per-question thresholds in `cascade_classify` (zcode-router-kit style).
- Native async provider fan-out (currently sequential).
