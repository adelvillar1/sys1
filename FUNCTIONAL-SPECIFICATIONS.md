# sys1 — Functional Specifications

> **For:** Product/UX reference and developer onboarding (user-flow contract)
> **Repo:** https://github.com/adelvillar1/sys1
> **Production:** N/A (library; no hosted service)

This is the user-flow contract — what the system does from a caller's perspective. It captures intended behavior, edge cases, and the rules that govern features. The two layers stay in sync as part of finishing a feature (see CLAUDE.md "Housekeeping protocol").

---

## Table of Contents

1. [Classification Behavior](#1-classification-behavior)
2. [Cascade Classification](#2-cascade-classification)
3. [Gating & Exit Codes](#3-gating--exit-codes)
4. [Provider Management](#4-provider-management)
5. [Calibration & Feedback](#5-calibration--feedback)
6. [Logging](#6-logging)
7. [Configuration](#7-configuration)
8. [Edge Cases & Error States](#8-edge-cases--error-states)
9. [CLI Reference](#9-cli-reference)
10. [Service Endpoints](#10-service-endpoints)

---

## 1. Classification Behavior

- Caller invokes `sys1.classify(provider, task, text)`.
- System resolves the task (registered name or inline `Task` instance).
- Resolves provider ids (aliases: `both`, `all`, `core`, `optin`, comma-separated).
- Filters providers by `providers.enabled` flag and `is_available()` check.
- Calls each provider in order, collecting `{head_id: Answer}` per provider.
- If no provider succeeds, returns `verdict: "error"`, `escalated: true`, `reason` explaining why.
- If at least one succeeds, runs `interpret()` over the collected answers.
- Default verdict is `"pass"`; becomes `"escalate"` if any head has `label is None` or `confidence < floor`.
- Optionally appends a JSONL record (on by default; skip with `log=False`).

<add-when-implemented>
- Multi-label semantics (all selected labels must clear the floor independently)
- Score head interpretation (numeric scale, legend display)
- noul head interpretation (boolean; `False` is a real answer, not a decline)
</add-when-implemented>

---

## 2. Cascade Classification

- `cascade_classify(provider_chain, task, text)` walks providers in order.
- Stops at the first provider where **every head** has a real label/noul/score AND confidence ≥ floor.
- If nothing clears, returns the last provider that did return answers.
- `providers_used` records every provider consulted.
- Winner's answers drive the verdict via `interpret()`.

<add-when-implemented>
- Per-task provider chain defaults
- Cascade reconciliation when providers disagree (majority vs. high-confidence tiebreak)
</add-when-implemented>

---

## 3. Gating & Exit Codes

- `pass` → exit 0
- `escalate` → exit 1 (warn)
- `block_on_classification=True` + escalated → exit 2 (block)
- `final_exit_code()` is the policy function; the CLI `classify` subcommand maps verdict → exit code.

<add-when-implemented>
- Block-mode hook examples (pre-commit returning 2, CI gating)
</add-when-implemented>

---

## 4. Provider Management

- Four core providers (`decide`, `jev`, `local`, `modernbert`) are enabled by default.
- Seven candidate providers (`clm`, `openjev`, `kev`, `tev1`, `laya`, `decide_1b`, `decide_multi`) are opt-in: add to `providers.enabled` and configure their endpoint.
- `sys1 doctor` prints availability reasons per provider (key missing, venv absent, no listener).
- `sys1 providers --all` prints the full registry with flag state.
- Control dashboard (`/dashboard`) toggles flags and probes endpoints; state persists to `~/.config/sys1/state.json`.

---

## 5. Calibration & Feedback

- Every classify call can write a JSONL record with `input_sha256`, answers, telemetry, and a `confidence_histogram`.
- `/v1/feedback` records a `predicted` → `actual` pair keyed on `input_sha256`.
- `/v1/calibration` reads the JSONL log and returns:
  - `classify_records` / `feedback_records` counts
  - `calibration_curve` (per-bin accuracy)
  - Per-provider `histogram` (confidence buckets) and `agreement` (cross-provider consensus)

---

## 6. Logging

- Path: `<log_dir>/YYYY/MM/DD/events.jsonl` (UTC).
- Append-only; never raises (`log_record` catches all exceptions and returns `None`).
- Redacts `content`, `context`, `match`, `_raw` by default (configurable via `log.redact_fields`).

---

## 7. Configuration

- Loaded by `sys1.load_config()` with the precedence chain: defaults ← global TOML ← repo-local TOML ← `SYS1_*` env vars ← `state.json`.
- Provider keys: `{pid}_api_url`, `{pid}_model`, `{pid}_api_key_env`, `{pid}_requires_key`.
- Common env vars: `SYS1_LOG_DIR`, `SYS1_STATE_FILE`, `SYS1_BEARER_TOKEN`, per-provider API key env vars.

---

## 8. Edge Cases & Error States

<add-when-implemented>
- Per-edge-case behavior table (network timeout, malformed response, missing venv, empty text)
- Fail-open policy: broken provider must not break the gate
- JSONL write failure must not break the gate
- Subprocess timeout (local / modernbert providers)
</add-when-implemented>

---

## 9. CLI Reference

| Command | Purpose |
|---------|---------|
| `sys1 classify --provider <id> --task <name> -i <file>` | Classify text from file or stdin |
| `sys1 doctor [--json]` | Provider availability snapshot |
| `sys1 providers [--all] [--json]` | Registry with flag state |
| `sys1 tasks` | List registered tasks |
| `sys1 version` | Print version |

---

## 10. Service Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/v1/classify` | bearer if set | Single classify |
| POST | `/v1/cascade` | bearer if set | Cascade classify |
| GET | `/v1/health` | open | Provider report |
| GET | `/v1/tasks` | open | Task list |
| GET | `/v1/tasks/{id}` | open | Single task |
| POST | `/v1/feedback` | bearer if set | Record feedback |
| GET | `/v1/calibration` | bearer if set | Calibration dashboard |
| GET | `/v1/recent` | bearer if set | Tail JSONL log |
| GET | `/dashboard` | open | Control dashboard (HTML) |
