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
| Tests | pytest ≥ 7 (188 tests) |

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
| `jev` | jev, clm, openjev | `/v1/systemone` with `{state, questions}` |
| `gliner` | local, decide_1b, decide_multi | subprocess into uv venv |

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

### Noul gating

A Jev noul answer has no `confidence` field, so `answer_confidence(entry)` derives the floor-comparable certainty: `max(p, 1 − p)` — a confident "no" (p=0.05) clears a 0.7 floor the same as a confident "yes" (p=0.95), and a coin flip (p=0.55) escalates. An explicit `confidence` on the answer still wins, so Decide-shaped nouls (which arrive as `{label, confidence}`) keep the old behavior exactly. Bool nouls have no gateable number: nothing to derive, and `label is None` (declined) still escalates. Details: [`docs/features/gating-and-recipes.md`](docs/features/gating-and-recipes.md).

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

Key sections: `scan`, `classify`, `providers`, `log`. Provider keys follow the pattern `{pid}_api_url`, `{pid}_model`, `{pid}_api_key_env`, `{pid}_requires_key`.

---

## 8. Telemetry & Calibration

JSONL log at `<log_dir>/YYYY/MM/DD/events.jsonl`. Each record carries `op` (`classify` or `feedback`), `input_sha256`, per-provider `answers` and `telemetry`, `verdict`, `escalated`, `latency_ms`, `confidence_histogram`.

Feedback joins via `input_sha256` (16-hex prefix). `calibration_curve()` produces per-bin predicted-vs-actual accuracy from feedback rows.

---

## 9. Service Layer

Optional FastAPI service at `service/main.py`. Mount the dashboard with `app.include_router(dashboard.router)`.

Endpoints:
- `POST /v1/classify` — single classify
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
