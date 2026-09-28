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
| Tests | pytest ≥ 7 (126 tests) |

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

---

## 6. Task System

Eight pre-registered tasks: `change`, `commit_audit`, `deps_risk`, `docs_drift`, `api_drift`, `pr_gate`, `issue_triage`, `safety`. Each is a `Task(id, heads, provider_heads={...})`. Heads are keyed by stable 8-hex `id` (sha1 prefix of the task text).

Load custom tasks from TOML or JSON with `sys1.load_tasks_file(path)`.

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
