# sys1 — Technical Documentation

> **For:** Developer onboarding and reference
> **Repo:** https://github.com/adelvillar1/sys1
> **Production:** N/A (library; no hosted service)

This is the developer-onboarding contract — summary-style, with links into `docs/` for deep dives. The two layers stay in sync as part of finishing a feature (see CLAUDE.md "Housekeeping protocol").

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack](#2-tech-stack)
3. [Architecture](#3-architecture)
4. [API Reference](#4-api-reference)
5. [Provider Registry & Wire Shapes](#5-provider-registry--wire-shapes)
6. [Task System](#6-task-system)
7. [Configuration](#7-configuration)
8. [Telemetry & Calibration](#8-telemetry--calibration)
9. [Service Layer](#9-service-layer)
10. [Development Workflow](#10-development-workflow)
11. [Scripts Reference](#11-scripts-reference)

---

## 1. Project Overview

Reusable Python library that normalizes multiple decision-model providers (Jev, GLiNER-2.5-Decide, local GLiNER, ModernBERT) behind one API. stdlib-only at runtime. Produces JSONL calibration logs consumed by the `dev-decisions` CLI dashboard.

<add-when-implemented>
- Key differentiators from alternatives
- Consumer integration patterns (CLI, HTTP service, programmatic)
</add-when-implemented>

---

## 2. Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python ≥ 3.10 |
| Runtime deps | stdlib only (`dependencies = []` in pyproject.toml) |
| Optional HTTP | FastAPI + uvicorn (`[service]` extra) |
| Local models | GLiNER2 / torch / transformers in a uv-managed venv (subprocess) |
| Build | Hatchling |
| Lint | ruff (E/F/I/N/UP/B/SIM/RUF) |
| Tests | pytest ≥ 7 (211 tests) |

For the full module map, see [`docs/architecture/overview.md`](docs/architecture/overview.md).

---

## 3. Architecture

<add-when-implemented>
- Module dependency diagram
- Data flow for a classify() call (config → task → providers → parsers → gating → log)
- Cascade escalation flow
</add-when-implemented>

Detailed architecture: [`docs/architecture/overview.md`](docs/architecture/overview.md)

---

## 4. API Reference

Primary entry point: `sys1.classify(provider, task, text, cfg=None, *, log=True) → ClassifyResult`

Reliability wrappers: `sys1.cascade_classify(...)` (provider chain, stop at the first floor-clearing provider) and `sys1.consistency_classify(..., n=5)` (self-consistency: n independent draws, per-head samples/mean/stdev, uncertainty band 0.30–0.70 → review — illustrative bounds, fit from the calibration stream).

Key types:
- `Head` / `Task` / `Provider` / `ClassifyResult` — `src/sys1/types.py`
- `make_choice()` / `make_noul()` / `make_score()` — head factories
- `register_task()` / `get_task()` / `list_tasks()` / `load_tasks_file()` — task registry

Detailed API docs: [`docs/architecture/api.md`](docs/architecture/api.md)

---

## 5. Provider Registry & Wire Shapes

Providers are registered via `sys1.register(Provider(...))` into a global `REGISTRY`. Three wire families:

| Wire | Providers | Request shape |
|------|-----------|---------------|
| `openai` | decide, tev1, kev, laya | `/v1/chat/completions` with `classifications` schema |
| `jev` | jev, drex, glide, julia, liquid, clm, openjev | `/v1/systemone` with `{state, questions}` (liquid's origin: api.liquid.ai/decisions/v1, plus its `images` extension) |
| `gliner` | local, decide_1b, decide_multi | fastino-prescribed `gliner2.classification` API — classifier server or subprocess |

Provider id note: **`decide` means the hosted wire** (api.fastino.ai); the Decide *model* is what the `local` provider runs. Hosted is off every consumer's default path — see `docs/plans/2026-09-28-fastino-decide-adoption.md`.

### Local fastino wire (the prescribed classification API)

The `gliner` wire follows the vendor's own playground (`fastino/gliner25-decide-playground`, `model.py`): one `ClassificationSchema` holding every head as a named task (`single` for choice/noul, `ordinal` for score scales), one `Classifier.classify(text, schema, config=ClassificationConfig(decoder="independent"))` decode, and the full `probabilities` distribution per task in every answer (`confidence` = max). Spec construction is pure (`providers.classification_schema_spec`), so the schema shape is testable without loading a model.

Two serving modes, identical answer shapes:

- **Classifier server** (recommended): `local_server_url` config key (per-provider `{pid}_server_url` override; one server per model). `service/classifier_server.py` runs inside the venv — load-once, one inference at a time, `GET /ready`, `POST /infer` with the playground's `{text, tasks} → {tasks: {name: {labels, probabilities}}}` contract. Measured 2026-09-28: 12.4s per subprocess classify → 361ms first / 144ms steady-state via the server.
- **Subprocess fallback**: `_LOCAL_INLINE` runs the same prescribed API in a one-shot venv process. Used automatically when the server is unconfigured or unreachable (`telemetry.mode` records which served; `server_fallback` carries the reason).

Noul answers from the local wire are docs-accurate (`{"type": "noul", "noul": p_yes}` — no confidence field); score answers map the selected ordinal level back to `score_min + index` and carry a `legend` when the head has level descriptions. `on_infeasible="relax"` keeps constrained decoding best-effort; a surfaced `InfeasibleError` declines every head so the gate escalates.

Provider flags: `enabled` (tokens: `core`/`all`/`optin`/explicit ids) minus `disabled`. Persisted to `~/.config/sys1/state.json` by the dashboard.

### Jev primitives (verified 2026-09-28 against docs.typesafe.ai/api)

One `/v1/systemone` call carries every head in a single `questions` map — `to_jev_head()` always fans out. Request: `{model, state, questions: {id: {type, instructions, criteria}}}`. Response: `{model, answers: {id: ...}, usage}`.

| Primitive | Question `criteria` | Answer fields |
|---|---|---|
| `choice` | map of label → rubric (≤ 255 options) | `choice`, `probabilities`, `confidence` |
| `score` | **ordered array of level descriptions, 2–10 levels, index 0 = lowest** | `score` (probability-weighted position; can land between levels), `legend` (level → description map), `probabilities`, `confidence` |
| `noul` | optional `{true, false}` descriptions | `noul` (0–1); no separate `confidence` — the number is the probability |

Wire rules that have bitten before:

- Score criteria is the **array** form — no `min`/`max` fields exist on the wire. `make_score()` renders levels as string labels for Decide-shaped providers and converts to the ordered array for Jev in `Head._score_criteria()`.
- Question ids are routing keys for our code; they are not sent to the model.
- `confidence` (choice/score only) is Jev's distribution-shape statistic, not the winning probability — see README "Confidence semantics". Gate noul on the `noul` value itself.
- Output tokens are free: batch every independent question in one call. A second call is only for genuinely dependent questions (options or state that can't exist yet).
- `noul` answers carry no `confidence` per the docs (fixture `jev_sample.json` is now docs-accurate). Gate noul on the value itself — see [Noul gating](#noul-gating) below.

### Drex deltas (verified 2026-09-30 against drex.nace.ai/llms-full.txt)

`drex` speaks the same `/v1/systemone` wire but diverges from Jev in four load-bearing ways, all handled inside `_drex_wire` / `to_drex_head()` / `parse_drex`:

- **`state` is a plain string** (the documented canonical input), not `{"text": ...}` — and it is bounded by `providers.drex_max_chars` (default 400,000), *not* `scan.max_diff_chars`: the 131,072-token state window on `drex-v1.5` is the feature.
- **Noul criteria keys**: Drex reads only `true`/`false` (other keys are ignored server-side); `to_drex_head()` maps `make_noul`'s yes/no descriptions onto those.
- **Score is 0-indexed**: Drex returns `score = Σ index × probability` over the criteria array; `parse_drex` shifts by `head.score_min` and converts Drex's returned legend array into sys1's `{level: description}` dict.
- **Usage keys**: `input_tokens`/`output_tokens` (no `total_tokens`) map onto `token_prompt`/`token_completion`/`token_total`; `evaluation_time_ms` and `request_id` ride along as telemetry extras.

Heads: `heads_for(task, "drex")` inherits the `jev` head set wherever a task defines one (same primitives, same noul semantics). Retry policy matches Drex's guidance — 429/529/500 retryable via the standard backoff, 401/402/422 fail fast; client timeout should stay ≥60s (Drex waits up to 55s model-side). Model defaults to pinned `drex-v1.5`; `drex-latest` is the documented stable alias.

### GLiDE deltas (probed live 2026-10-01 against docs.fastino.ai/concepts/decision-models)

`glide` (Fastino, `fastino/glide`, billed like hosted GLiNER-2.5-Decide — not free; corrected 2026-10-02) speaks the same `/v1/systemone` wire and reuses `to_drex_head()` verbatim — probed live: an extra `id` field per question or `yes`/`no` noul criteria keys return 422 `extra_forbidden`. Deltas handled inside `_glide_wire` / `parse_glide`:

- **40,000-token rendered-prompt limit**: `state` is a plain string bounded by `providers.glide_max_chars` (default 100,000 ≈ headroom at ~2.5 chars/token) — long-context work belongs to Drex (131,072 tokens).
- **Score is 0-indexed like Drex** (shifted by `head.score_min`), but `probabilities`/`legend` arrive as a dict keyed by index string (`"0".."N-1"`) — the legend keys shift directly where Drex sends a legend *array*. `expected_level` (Σ index × p) rides along as a calibration extra.
- **Confidence is explicit on every answer kind** with documented formulas: noul `|2p−1|`, choice/score `top1−top2` margin. `parse_glide` **strips noul confidence** (on that scale p=0.85 scores 0.70 — stricter than the family's derived `max(p, 1-p)`) and keeps choice/score margins, which run systematically lower than Jev's concentration statistic (calibration 2026-10-01: glide agreed with jev 6/6 on diff_type labels, 5/6 on risk_tier; noul p tracked within ~0.05).
- **Auth**: both `Authorization: Bearer` and `X-API-Key` accepted — `http_post_json` unmodified. `state` also accepts object/array forms; sys1 sends a plain string. Response echoes the model id with the vendor prefix stripped (`glide`).
- **Usage keys**: `input_tokens`/`output_tokens` + a top-level `token_usage` int; no `evaluation_time_ms`/`request_id` on this wire. Latency is bimodal (1–8s observed, 17.5s tail) — well under the 30s timeout, but not the latency pick.

Heads: `heads_for(task, "glide")` inherits the `jev` head set like drex. Needs `FASTINO_API_KEY` (repo `.env`).

### Julia-1 deltas (probed live 2026-10-02, evidence in docs/plans/2026-10-02-julia-provider.md)

`julia` (Supersoniclabs, `julia-1`) is a BERT-based decision model on the same `/v1/systemone` wire — `$0.025 per 1M input tokens / $0 output`, fan-out native (all heads in one request), 0.5–1.2s latency. Deltas handled inside `_julia_wire` / `parse_julia`:

- **Two headers are REQUIRED on this host**: `Idempotency-Key` (missing → 400 `idempotency_key_required`; one fresh uuid per logical call, reused across that call's retries) and a `User-Agent` — Cloudflare 403s (error 1010) Python-urllib's default signature, which is what `http_post_json` sends, so the wire passes both via `extra_headers`.
- **8,192-token window**: `state` is a plain string bounded by `providers.julia_max_chars` (default 12,000 ≈ headroom at the measured ~2.4–2.5 chars/token prose rate). Window overflow does NOT return a structured error — a ~9.7K-token request came back as a raw HTTP 502 — so the client-side bound is the only guard.
- **Score takes `criteria` as an ordered ARRAY** (a map → 422 "invalid type: map, expected a sequence"; Jev's `levels` key → 422) — exactly what `to_drex_head()` produces. Strict validation like Drex/GliDE: extra fields (`id`) → 422.
- **Score is 0-indexed like Drex/GliDE** (shifted by `head.score_min`), `probabilities` keyed by index string — but **`legend` arrives as `null`** (Drex sends an array, GliDE an index-keyed dict). `parse_julia` synthesizes the legend from `head._score_criteria()` — the criteria array the wire sent, in wire order.
- **Confidence semantics: top1 probability** on choice/score (`confidence` byte-equals `probabilities[top1]`) — a probability scale directly thresholdable at the 0.7 floor, NOT GliDE's top1−top2 margin. noul is `{type, noul: p}` with no confidence field — gating derives `max(p, 1−p)` with nothing to strip.
- **Usage keys**: `input_tokens`/`output_tokens`/`total_tokens` inside `usage`; no top-level `token_usage`, no `evaluation_time_ms`/`request_id`.

Heads: `heads_for(task, "julia")` inherits the `jev` head set. Needs `SUPERSONIC_API_KEY` (repo `.env`). **Routing: registered core but NOT in the default chain** — four-way calibration (2026-10-02, six-text change corpus) found julia agreeing with glide AND jev only 1/6 on diff_type and 1/6 on risk_tier (overconfident on disagreeing labels, breaking-change under-detection), failing the cascade's first-provider calibration premise. Position: cheap fast triage head; per-task adoption via `providers.route.by_task` only with fitted floors. It IS in `_SIZE_EXCLUDED` (8,192-token bound) so any custom chain respects the size gate.

### Liquid d1 deltas (probed live 2026-10-05, evidence in docs/plans/2026-10-05-liquid-d1-provider.md)

`liquid` (Liquid AI, `d1`) implements the TypeSafe System One API natively at `https://api.liquid.ai/decisions/v1/systemone` — same state/questions/answers contract as jev/drex/glide/julia — plus the OpenJev `images` extension that makes it the pool's first vision-capable decision model. Deltas handled inside `_liquid_wire` / `parse_liquid`:

- **Images (the OpenJev extension)**: `POST` accepts up to 8 images — base64 `data:image/…;base64,` URLs or `{content_type, base64}` objects (JPEG/PNG/WebP/GIF, 5MB each, 4.5MB request-body cap; remote URLs rejected). Images precede the state in the prompt, every question sees all of them, and each question bills them again: 1.5 input tokens per 32×32 patch, ≤10,000 patches per request, no image >100× as long as wide. `classify()`/`cascade_classify()` take an optional `images` list (forwarded by every wire factory; only `_liquid_wire` emits it); `telemetry["image_count"]` records the count and the JSONL log carries that count, never the payload.
- **65,536-token prompt window**: every question re-renders the whole state, so `state` is a plain string bounded by `providers.liquid_max_chars` (default 150,000 ≈ headroom at the measured ~4.4 chars/token prose rate); text cap 250KB; either overflow returns 422. The 150K-char bound sits above the router's long-input threshold, so liquid needs no `_SIZE_EXCLUDED` entry.
- **Confidence semantics: the Jev-family distribution statistic** `(N*max_prob − 1)/(N − 1)` on choice/score — verified byte-equal on the wire (3 options, top1 p=0.9306 → conf 0.8958) — directly thresholdable at the 0.7 family floor, NOT GliDE's margin. noul is `{type, noul: p}` with no confidence field; gating derives `max(p, 1−p)`.
- **Score is 0-indexed like Drex/GliDE** (shifted by `head.score_min`); `probabilities`/`legend` arrive as dicts keyed by index string, so legend keys shift directly; no `expected_level` extra.
- **Auth**: plain `Authorization: Bearer` — no idempotency or User-Agent headers required (both were the julia host's quirks). Strict validation like Drex/GliDE/Julia: question shape is `to_drex_head()` verbatim, anything extra is a 422.
- **Usage keys**: `usage.input_tokens`/`output_tokens` (output always 0) plus `cost` in USD — $0.04 per 1M input tokens, output free; `cost` rides into telemetry as `cost_usd` for the calibration stream.

Heads: `heads_for(task, "liquid")` inherits the `jev` head set. Needs `LIQUID_API_KEY` (repo `.env`). **Routing: registered core but NOT in the default chain** — the calibration gate is the phase-1 close-out; live probes (text change-diff classify + a 64×64 solid-blue PNG read as "blue" at conf 0.99995) confirm wire-level fitness. It is NOT in `_SIZE_EXCLUDED` (150K-char bound, 65,536-token window). The `d1:free` model (text-only, keyless, 32K-token window) is never exercised — user constraint 2026-10-05; the wire pins model `d1`.

### Routing (cascade default chains)

`routing.route(task, text, cfg)` is the deterministic policy that builds `cascade_classify`'s chain when none is passed explicitly. It is a policy function, not an adaptive router — decision calls are low-volume and near-free at the head of the chain; quality lives in floors and calibration, not model scoring.

1. **Chain source**: `providers.route.by_task[task.id]` override, else `providers.route.default_chain` (default `glide,drex,jev` — hosted-only by design; `local`/`modernbert` are never auto-routed).
2. **Size gate**: past `providers.route.long_input_chars` (default: `glide_max_chars`), providers whose documented bound cannot take the input (glide, julia) move to the back — drex-first for long inputs.
3. **Availability**: providers that are not enabled+usable right now drop out.
4. **Task allow-list**: `Task.providers` (e.g. safety's) is a hard filter, preserving chain order.

Explicit `provider_chain` arguments and `classify()` bypass routing entirely. The decision rides into the JSONL record as `route_chain` / `route_reason` (`default` | `task` | `long-input` | `availability-filtered` — added keys only). `providers.route.enabled = false` restores the legacy `["decide", "jev"]` default. See `docs/plans/2026-10-01-glide-provider-and-router.md`.

### Noul gating

A Jev noul answer has no `confidence` field, so `answer_confidence(entry)` derives the floor-comparable certainty: `max(p, 1 − p)` — a confident "no" (p=0.05) clears a 0.7 floor the same as a confident "yes" (p=0.95), and a coin flip (p=0.55) escalates. An explicit `confidence` on the answer still wins, so Decide-shaped nouls (which arrive as `{label, confidence}`) keep the old behavior exactly. Bool nouls have no gateable number: nothing to derive, and `label is None` (declined) still escalates. Details: [`docs/features/gating-and-recipes.md`](docs/features/gating-and-recipes.md).

### Jev jagged edges (task-authoring rules)

TypeSafe documents what jev-1.13 does badly (`docs.typesafe.ai/model-jaggedness/jev-1.13`). Task authors and reviewers apply these as rules:

- **Never ask Jev to count, do arithmetic, or compare dates** — one noul per item and sum in code; extract date parts as Choices over enumerated options and assemble in code.
- **Score levels are weakly calibrated as numbers** — threshold, rank, or round a score; never reconstruct a magnitude from it. (`confidence` describes distribution shape, not correctness.)
- **Filter state in code; send only needed fields** — large irrelevant state is a documented distractor, and text in state is *not treated as hostile* (no built-in injection defense; that is what the guardrail battery is for).
- **No structural invariants** — P(x) vs 1−P(not x) is not guaranteed, and a noul can disagree with a choice on the same fact. Our derived noul certainty is a routing convention, not a model-verified identity; never port thresholds across primitives or providers.
- **Self-consistency** (`sys1.consistency_classify`) is the vendor's reliability pattern for borderline calls: repeat draws, band the per-head signal, route the middle band to review.

### Official TypeSafe references

- Docs index (with `llms.txt`): https://docs.typesafe.ai/ — API reference at `/api`
- Agent skill (drop-in for Claude Code/Codex): https://docs.typesafe.ai/agent-skill
- Patterns: fan-out `/patterns/fan-out`, confidence routing `/patterns/confidence-routing`, composite scoring, intent routing
- Cookbooks: LLM guardrails `/cookbooks/llm_guardrails`, parallel questions, classification-using-confidence, SDE cascade

### Guardrail batteries (from the official LLM-guardrails cookbook)

Screen LLM input and output with independent Noul hazard batteries plus one severity Score, all in a single call. Policy lives in code: the strict defaults are review ≥ 0.35, action ≥ 0.70, severity promotes review → block ≥ 2.0; permissive raises action to 0.85. Guardrails fail closed — a failed provider call must never read as "pass". Redact secrets before text goes into `state`. The pre-registered `safety` task (destructive/reversible nouls) is the in-repo example of the input side.

### Confidence floors scale with action risk

The official confidence-routing pattern sets floors per action by consequence: a catch-all floor (~0.6) for uncertainty, high-stakes actions auto-approve only above ~0.85. sys1 models this at four granularities — per-head `Head.floor`, per-task `confidence_floor`, per-provider `{pid}_confidence_floor`, global — plus the built-in `_DEFAULT_FLOORS` per provider. Set floors per task and per head — never one global cutoff — and fit them from the local JSONL calibration stream; published thresholds do not transfer across workloads.

Precedence ladder (single source of truth, `gating.effective_floor`): per-provider `{pid}_confidence_floor` config key > `head.floor` > `task.confidence_floor` > global `confidence_floor` > `_DEFAULT_FLOORS[pid]`.

Cross-provider warning: a head floor scalar applies in the **serving provider's own** confidence semantics — Jev's distribution-shape concentration and Decide's max-probability are different statistics, so never reuse one number across providers expecting equivalent meaning. The per-provider config key remains the cross-provider knob. Both `interpret()` and `cascade_classify()` resolve floors through the same helpers, so the two paths can't drift.

---

## 6. Task System

Eight pre-registered tasks: `change`, `commit_audit`, `deps_risk`, `docs_drift`, `api_drift`, `pr_gate`, `issue_triage`, `safety`. Each is a `Task(id, heads, provider_heads={...})`. Heads are keyed by stable 8-hex `id` (sha1 prefix of the task text).

Load custom tasks from TOML or JSON with `sys1.load_tasks_file(path)`. Heads accept an optional per-head `floor` (uncertainty floor in the serving provider's confidence semantics — see §5); the loader passes it through to `make_choice`/`make_noul`/`make_score`. TOML head specs are inline tables and must stay on one line (TOML 1.0); `provider_heads.<prov>` is a list of head specs.

`examples/` ships five recipes ported from the TypeSafe cookbook patterns (guardrail hazard battery, citation check, composite lead scoring, passage re-rank, taxonomy walk) as task files plus short scripts — usage and vendor sources in [`examples/README.md`](examples/README.md), smoke-tested in `tests/test_recipe_pack.py` against a stub provider. The recipes are not built-ins; they demonstrate the `load_tasks_file` idiom.

---

## 7. Configuration

Config precedence: `DEFAULTS` ← `~/.config/sys1/config.toml` ← `~/.config/dev-decisions/config.toml` ← `extra_paths` ← `<repo>/.sys1.toml` / `<repo>/.dev-decisions.toml` ← `SYS1_*` env vars ← `~/.config/sys1/state.json`.

Before any of that, `load_config()` self-configures the provider keys from the env file (`load_provider_env`): `$SYS1_ENV_FILE` → `~/.config/sys1/env` → legacy `~/.config/dev-decisions/env`. Values are `setdefault` into `os.environ` — a key the caller exported wins; the return carries names only, never values. This is why consumers get the full hosted pool from any shell without sourcing anything.

Key sections: `scan`, `classify`, `providers`, `log`. Provider keys follow the pattern `{pid}_api_url`, `{pid}_model`, `{pid}_api_key_env`, `{pid}_requires_key`.

---

## 8. Telemetry & Calibration

JSONL log at `<log_dir>/YYYY/MM/DD/events.jsonl`. Each record carries `op` (`classify` or `feedback`), `input_sha256`, per-provider `answers` and `telemetry`, `verdict`, `escalated`, `latency_ms`, `confidence_histogram`; cascade records additionally carry `cascade`, `chain`/`winner` and the routing decision `route_chain` / `route_reason` (see [Routing](#routing-cascade-default-chains)).

Feedback joins via `input_sha256` (16-hex prefix). `calibration_curve()` produces per-bin predicted-vs-actual accuracy from feedback rows.

---

## 9. Service Layer

Optional FastAPI service at `service/main.py`. Mount the dashboard with `app.include_router(dashboard.router)`.

Endpoints:
- `POST /v1/classify` — single classify (accepts `task` for a registered name or `task_spec` for an inline `[[tasks]]`-shaped definition — built via `sys1.task_from_spec`, never registered; this is how consumers with dynamic heads, e.g. the zcode-router's workflow labels, use sys1)
- `POST /v1/cascade` — cascade classify
- `GET /v1/health` — provider report
- `GET /v1/tasks` — task list
- `GET /v1/tasks/{id}` — single task
- `POST /v1/feedback` — calibration feedback
- `GET /v1/calibration` — calibration dashboard data
- `GET /v1/recent` — tail of JSONL log
- `GET /dashboard` — control dashboard (HTML)

Auth: optional bearer token (`SYS1_BEARER_TOKEN`). Mutating and data endpoints require it; health, tasks, and the dashboard page itself stay open.

---

## 10. Development Workflow

The plan-build-recap-document cycle:

1. **Plan** — draft a plan at `docs/plans/YYYY-MM-DD-<slug>.md` with acceptance criteria. See [`docs/plans/README.md`](docs/plans/README.md).
2. **Build** — implement, validate with `pytest` + `ruff`.
3. **Recap** — write a session recap at `docs/recaps/SESSION-RECAP-YYYY-MM-DD.md` with criteria status.
4. **Document** — update this file and `FUNCTIONAL-SPECIFICATIONS.md` for the affected area.

Trivial work compresses the cycle: skip plan, brief recap, no contract-doc updates.

---

## 11. Scripts Reference

<add-when-implemented>
- Most-used scripts and common flags
</add-when-implemented>

Full catalog (when present): [`docs/scripts/README.md`](docs/scripts/README.md)
