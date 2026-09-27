# Changelog

All notable changes to `sys1` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
