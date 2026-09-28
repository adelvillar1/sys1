# Changelog

All notable changes to `sys1` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-27

Provider flags + control dashboard: candidate System One models wired and
ready, enable/disable without touching code. Also (same branch, merged
after the 0.2.0 cut): the noul gating fix, per-head floors, and the
recipe pack below.

### Fixed
- **Noul answers are now gated.** A Jev noul answer carries no
  `confidence` field — the 0–1 output *is* the probability of "true".
  `interpret()` and `cascade_classify()` floor a head only when its
  `confidence` field exists, so noul-gated tasks served by Jev returned
  `pass` on any non-declined answer while Decide-shaped nouls (which
  arrive as `{label, confidence}`) gated normally — same head, opposite
  behavior per provider. `gating.answer_confidence()` derives the
  floor-comparable certainty `max(p, 1 − p)`; an explicit `confidence`
  on an answer still wins, so Decide-shaped nouls are unchanged.
  Bool nouls keep today's behavior (nothing to derive). Note: this
  intentionally changes verdicts for Jev-served noul tasks
  (`dev-decisions`' `safety` task is the loudest case);
  `block_on_classification` stays opt-in.
- **`floors_for()` applies the effective task/global floor to custom
  providers**, which previously fell through to the global default —
  the cause of cascade/`interpret()` disagreement for non-core ids.

### Added
- **Provider flags** — `providers.enabled` / `providers.disabled` accept
  `core`, `all` / `*`, `optin` / `candidates`, or explicit ids (env:
  `SYS1_PROVIDERS__ENABLED`). `enabled_set()` gates `call_provider`,
  `is_available`, and the API fan-out; a disabled provider fails with the
  exact fix-it hint instead of a bare "unavailable".

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

### Added (2026-09-28 — same branch, after the 0.2.0 cut)
- **Self-consistency helper** — `sys1.consistency_classify(provider, task,
  text, *, n=5, low=0.30, high=0.70)` implements the official
  consistency-cookbook pattern: n independent draws, per-head
  `samples`/`mean`/`stdev`, and an uncertainty band routing the inclusive
  middle to review. Noul heads band on the raw probability; choice/score on
  the winner's max probability; ungatable draws count but don't score. Each
  draw logs an ordinary classify record. Bounds are illustrative — fit them
  from the calibration stream.
- **`examples/semantic-find.py`** — the semantic_find cookbook: rank lines
  with a Choice over line-IDs plus an existence Noul in one request
  (provider-swappable, 255-line cap documented).
- **429 is retryable** — `http_post_json` backs off on Jev's documented
  rate-limit status (with 5xx and warming); telemetry records every attempt.
- **Jagged-edge wire rules documented** — TECHNICAL-DOCUMENTATION §5 now
  carries the vendor's jev-1.13 limitations as task-authoring rules (no
  counting/arithmetic/dates; score levels weakly calibrated as numbers;
  no structural invariants — derived noul certainty is a routing
  convention, not a model identity).
- **`typesafe-jev` agent skill extended** — jagged-edges table, task-design
  craft (instructions objects, contrastive criteria, standalone score
  levels), and a symptom→fix diagnosis table, absorbed from the vendor
  pages and `dbreunig/building-with-jev-skill`.
- **Fastino-prescribed local wire** — the `gliner` family (`local`,
  `decide_1b`, `decide_multi`) now uses `gliner2.classification.Classifier`
  + `ClassificationSchema` exactly as the vendor's own playground does:
  one schema holding every head (`single` for choice/noul, `ordinal` for
  score scales), one `decoder="independent"` decode, full
  `probabilities` per answer (`confidence` = max). Spec construction is
  pure (`sys1.providers.classification_schema_spec`). Legacy
  `{label, confidence}` entries still parse. Measured: 12.4s per
  subprocess classify → 361ms first / 144ms steady-state via the server.
- **Classifier server** (`service/classifier_server.py`) — stdlib
  load-once server run inside the gliner venv; playground contract
  (`GET /ready`, `POST /infer`). Opt in via `providers.local_server_url`
  (per-model `{pid}_server_url` overrides); `_gliner_wire` falls back to
  the subprocess automatically, and a server that answers counts as
  available in `sys1 doctor`.
- **Inline task specs on the service** — `POST /v1/classify` accepts
  `task_spec` (the `[[tasks]]` shape, built via the new
  `sys1.task_from_spec`) so consumers with dynamic heads — e.g.
  zcode-router's per-install workflow labels — can classify without
  registering a task.
- **`examples/routing-judge.toml`** — the zcode-router's four routing
  questions as a sys1 task, for the local fastino Decide provider.
- **Per-head confidence floors** — `Head.floor: float | None` plus a
  `floor=` parameter on `make_choice` / `make_noul` / `make_score` and
  the TOML/JSON task loader. Precedence ladder (`gating.effective_floor`):
  per-provider `{pid}_confidence_floor` config key > `head.floor` >
  task `confidence_floor` > global > built-in provider default. Both
  `interpret()` and `cascade_classify()` resolve floors through the same
  helpers, so walk and verdict can't drift.
- **Recipe pack** (`examples/`) — five recipes ported from the TypeSafe
  cookbook patterns: guardrail hazard battery (5 nouls + severity
  score, caller-side review/action/block tiers, fails closed), citation
  check (noul `floor = 0.85` + strength score), composite lead scoring,
  passage re-rank (one fan-out call, return-nothing-if-weak), and a
  taxonomy walk (per-level choice head with a weak-edge floor bail).
  Each ships as a sourced task file plus a short script; all are
  smoke-tested offline in `tests/test_recipe_pack.py`.
- **`examples/custom-task.toml` now parses** (pre-existing breakage:
  multi-line TOML inline tables, and `provider_heads` written as a dict
  where the loader takes a list).

### Changed (2026-09-28)
- **Local noul/score answer shapes** — noul answers are docs-accurate
  (`{"type": "noul", "noul": p_yes}`, no confidence; gating derives
  `max(p, 1−p)`); score answers map the selected ordinal level back to
  `score_min + index` and carry a `legend` when the head has level
  descriptions. `_LOCAL_INLINE` was migrated off the legacy
  `GLiNER2.classify_text` surface (per-head loop, one model load per
  call) to the prescribed API.
- **Hosted `decide_model` string corrected** to `fastino/GLiNER2.5-Decide`
  (was the dashed `GLiNER-2.5-Decide`); provider-id semantics documented:
  `decide` = hosted wire, the Decide model is what `local` runs.
- `tests/fixtures/jev_sample.json` noul answers are docs-accurate — no
  `confidence` key (it never existed on the wire); `test_sdk_shape`
  asserts the real shape.
- Test suite: 126 → 211 tests; new `tests/test_recipe_pack.py`,
  `tests/test_local_wire.py`.
- Docs: new `docs/features/gating-and-recipes.md`; noul gating and the
  floor precedence ladder added to TECHNICAL-DOCUMENTATION §5 and
  FUNCTIONAL-SPECIFICATIONS §1/§2/§3.

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
