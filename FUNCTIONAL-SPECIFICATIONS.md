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
- Provider keys self-configure: availability does not depend on the calling shell — the layer loads its env file (`$SYS1_ENV_FILE` / `~/.config/sys1/env` / legacy `~/.config/dev-decisions/env`) on every `load_config()`, so hosted providers register even from a bare shell or cron.
- Calls each provider in order, collecting `{head_id: Answer}` per provider.
- If no provider succeeds, returns `verdict: "error"`, `escalated: true`, `reason` explaining why.
- If at least one succeeds, runs `interpret()` over the collected answers.
- Default verdict is `"pass"`; becomes `"escalate"` if any head is declined (`label is None`) or its confidence is below the head's floor.
- Optionally appends a JSONL record (on by default; skip with `log=False`).

Head interpretation:

- **choice** — gated on `confidence` (the max probability).
- **noul** — the 0–1 answer is the probability of "true"; gateable certainty is `max(p, 1 − p)`, so a confident "no" clears the floor the same as a confident "yes". An explicit `confidence` field (Decide-shaped nouls) wins over the derived certainty. A bool noul (`True`/`False`) has no gateable number and isn't floored; `noul: null` is a decline and escalates.
- **score** — the numeric answer rides the ordered scale (may land between levels); gated on its `confidence`.

<add-when-implemented>
- Multi-label semantics (all selected labels must clear the floor independently)
</add-when-implemented>

---

## 2. Cascade Classification

- `cascade_classify(provider_chain, task, text)` walks providers in order.
- With no explicit `provider_chain`, the chain is routed (`routing.route`, deterministic): per-task override (`providers.route.by_task`) else the default `glide,drex,jev` — hosted-only by design — size-gated past `glide_max_chars` (drex first for long inputs), filtered by availability and the task's own provider allow-list. `providers.route.enabled = false` restores the legacy `["decide", "jev"]`. The decision rides into the JSONL record as `route_chain` / `route_reason`.
- Stops at the first provider where **every head** has a real label/noul/score AND its confidence clears the head's floor. Noul answers clear on the derived certainty `max(p, 1 − p)`; a noul with an explicit `confidence` clears on that — same rule as `interpret()`.
- The floor per head resolves through the same ladder as `interpret()` (§3): per-provider config key > `head.floor` > explicit `confidence_floor` argument > task > global > provider default.
- If nothing clears, returns the last provider that did return answers.
- `providers_used` records every provider consulted.
- Winner's answers drive the verdict via `interpret()` (same floors, so walk and verdict can't disagree).

### Consistency classification

- `consistency_classify(provider, task, text, *, n=5, low=0.30, high=0.70)` re-asks the same task `n` independent times (official self-consistency pattern).
- Per head the signal is the raw noul probability P(true), or the winner's max probability for choice/score heads; Decide-shaped boolean nouls (hosted decide) estimate P(true) as the boolean's confidence when true, `1 − confidence` when false. Draws with no gateable number (declined) count as samples but score as `None` ("counted but not scored").
- Returns per-head `samples` / `mean` / `stdev` and a `band`: below `low` → `"no"`, above `high` → `"yes"`, inclusive between → `"uncertain"` (route to review); `None` when no draw was usable.
- The band bounds ship as **illustrative** starting points — fit them from the JSONL calibration stream; published thresholds do not transfer across workloads.
- Each draw is an ordinary classify() call: with `log=True` the log gains n regular records (same shape), so feedback joins work per draw.

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

Floor precedence ladder (highest wins), resolved per head by `gating.effective_floor`:

1. per-provider config key `{pid}_confidence_floor` (the cross-provider knob)
2. head `floor` (per-head; set in code via the `make_*` factories or in a task file)
3. explicit `confidence_floor` argument / `Task.confidence_floor`
4. global `classify.confidence_floor`
5. built-in `_DEFAULT_FLOORS[pid]`

A head floor scalar applies in the **serving provider's own** confidence semantics (Jev concentration vs Decide max-prob) — never reuse one number across providers expecting equivalence. Noul answers gate on the derived certainty (§1). Summary lines mark derived confidences as `(conf … derived)`. Full rule reference: [`docs/features/gating-and-recipes.md`](docs/features/gating-and-recipes.md).

<add-when-implemented>
- Block-mode hook examples (pre-commit returning 2, CI gating)
</add-when-implemented>

---

## 4. Provider Management

- Four core providers (`decide`, `jev`, `local`, `modernbert`) are enabled by default. **`decide` means the hosted Fastino wire; the Decide *model* is what `local` runs** — hosted is not on any consumer's default path.
- Seven candidate providers (`clm`, `openjev`, `kev`, `tev1`, `laya`, `decide_1b`, `decide_multi`) are opt-in: add to `providers.enabled` and configure their endpoint.
- The `gliner` wire (`local`, `decide_1b`, `decide_multi`) uses the fastino-prescribed classification API with two serving modes: the load-once classifier server (`providers.local_server_url`, per-provider `{pid}_server_url`; run one server per model) or the venv subprocess fallback. Answer shapes are identical; telemetry records `mode` and any `server_fallback` reason.
- `sys1 doctor` prints availability reasons per provider (key missing, venv absent, no listener) — a configured classifier server that answers counts as available.
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
