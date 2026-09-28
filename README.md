# sys1

**A reusable decision-model abstraction for System One judgment in code.**
Jev, GLiNER-2.5-Decide, local GLiNER2, and ModernBERT behind one Python API
and one wire shape — same call from CLI, git hooks, CI gates, an HTTP service,
and any language that speaks JSON.

```python
import sys1

# One call. Pick a task. Get a normalized answer + a verdict.
result = sys1.classify(
    provider="decide",          # "decide" | "jev" | "local" | "modernbert" | "both" | "all" | an enabled candidate id
    task="change",              # "change" | "pr_gate" | "issue_triage" | ... or your own
    text=staged_diff,
)
if result.escalated:
    print("Low confidence or null verdict — human review recommended.")
    sys.exit(1)
apply_label(result.head("diff_type", "decide")["label"])
```

`sys1` doesn't *replace* your branching logic. It replaces the **judgment**
inside that branching — "is this a breaking change?", "is this PR risky?",
"should this command escalate to a human?" — with a calibrated model answer.
You still gate on the answer; you still route on it; what moves out of code
and into a *decision contract* is the criteria.

---

## What's inside

| Component | File | What it does |
|-----------|------|--------------|
| `classify()` | `api.py` | Top-level: resolve → call providers → interpret → log |
| `cascade_classify()` | `api.py` | Walk a provider chain; stop at the first that clears |
| `Head` / `Task` / `Answer` | `types.py` | Dataclasses: stable head ids, provider wire shapes |
| `Provider` | `providers.py` | Pluggable registry: 4 core + 7 opt-in candidates |
| `parse_decide` / `_jev` / `_local` / `_modernbert` | `parsers.py` | Wire-shape normalization to the shared Answer dict |
| `interpret()` | `gating.py` | Confidence floors → pass / escalate / block verdict |
| `log_record()` | `telemetry.py` | JSONL log; redaction; calibration joins |
| 8 pre-registered tasks | `tasks.py` | change, commit_audit, deps_risk, docs_drift, api_drift, pr_gate, issue_triage, safety |
| HTTP service | `service/main.py` | FastAPI wrapper, same API, any language |
| `sys1` CLI | `cli.py` | `classify`, `doctor`, `providers`, `tasks`, `version` |

The library is **stdlib-only** at runtime. `urllib` for HTTP providers,
`subprocess` for the local GLiNER venv. `pip install sys1` adds no hard
dependencies; FastAPI is opt-in via `pip install 'sys1[service]'`.

---

## Providers

| id | Backend | Strengths | Caveats |
|----|---------|-----------|---------|
| `decide` | Fastino hosted `GLiNER-2.5-Decide` (OpenAI-compatible chat-completions) | Fast, structured schema (`{"classifications": [...]}`), $0.03/$0 pricing on the docs / $0.15/$0.15 on the live catalog — verify before budgeting | Flat `labels` only, no per-option descriptions on hosted. HTTP 425 on cold models; retry with backoff. `constrained_classification` is gated to `multi-v1`, not hosted Decide. |
| `jev` | TypeSafe hosted `Jev-1.13.0` (`/v1/systemone`) | Three primitives (choice / noul / score), per-option `criteria` descriptions, decision-quality scores on benchmarks (Decision Index 51.67 vs Decide's 9.98) | Higher latency than Decide. `confidence` is a distribution-shape statistic — do NOT threshold it on the same number as Decide. |
| `local` | `gliner2` in a uv-managed venv (subprocess) | No network, no cost, runs offline | Needs the venv path. Slower per-call (model load), unless you wrap a persistent server. |
| `modernbert` | `answerdotai/ModernBERT-base` sentence encoder (eval only, no fine-tuning) | Fast + cheap + a useful calibration comparison | Noisy; lower confidence floor (0.55 default) reflects that. Use for calibration, not for gates. |

### Candidate providers (opt-in)

Beyond the four core providers, seven more are **registered but disabled by
default**. Each speaks an existing wire, so turning one on is config, not code:

| id | Backend | Wire | To enable |
|----|---------|------|-----------|
| `clm` | CLM-8B (Contrastive-LM state-action scorer) | `jev` (`/v1/systemone`) | run `clm-serve`, flip the flag |
| `openjev` | openjev / JevK5 community reproduction | `jev` | serve behind `/v1/systemone` |
| `kev` | Kev 0.8B/4B/9B (Apache 2.0) | `openai` | vLLM / Ollama behind `/v1/chat/completions` |
| `tev1` | Tev1 (Together AI decision model) | `openai` | hosted — set `TOGETHER_API_KEY` |
| `laya` | Laya (Convai, 421M ModernBERT) | `openai` | serve it (wire provisional) |
| `decide_1b` | GLiNER2.5-Decide-1B | `gliner` (local venv) | `uv pip install gliner2` in the venv |
| `decide_multi` | GLiNER2.5-multi-Decide | `gliner` (local venv) | same venv |

Flip them with the `providers.enabled` flag — a comma list of ids or any of
the tokens `core`, `all` / `*`, `optin` / `candidates`, plus a `disabled`
list that always wins:

```toml
# ~/.config/sys1/config.toml
[providers]
enabled = "core,clm"          # built-ins + CLM-8B
# enabled = "all"             # everything registered
# disabled = "tev1"           # subtract from whatever `enabled` resolved to
```

Same keys are env-overridable: `SYS1_PROVIDERS__ENABLED="core,kev"`.
The control dashboard (below) writes the same state to
`~/.config/sys1/state.json`, which beats TOML/env so toggles take effect
without restart. `sys1 providers` / `sys1 doctor` show every provider's
flag, configuration, and reachability; `call_provider` on a disabled id
raises with the fix-it hint.

### Confidence semantics (important)

Jev's `confidence` is not the same kind of number as Decide's:

```
Jev:      confidence ≈ (N × max_prob − 1) / (N − 1)        # distribution-shape
Decide:   confidence = max(probabilities)                   # literal max-prob
```

For a binary (N=2) the two agree. For N≥3, Jev's confidence diverges from
max-prob and a "0.7 Jev confidence" on a 5-way choice means something
different than "0.7 Decide confidence." Tune them separately via
`cfg["classify"]["jev_confidence_floor"]` and `…["decide_confidence_floor"]`.

Noul answers are the exception: a Jev noul (`{"noul": 0.95}`) carries **no
confidence field** — the 0–1 output is the probability of "true", so sys1
gates it on `max(p, 1 − p)` (a confident "no" clears the floor, a coin
flip escalates). An explicit `confidence` on an answer — Decide-shaped
nouls arrive as `{label, confidence}` — always wins over the derived value.
Floors apply per head (`head.floor`), per task, per provider (config key),
or globally, in that order of precedence — see
[`docs/features/gating-and-recipes.md`](docs/features/gating-and-recipes.md).

### Jev usage notes

One `/v1/systemone` request carries every head — `classify()` already fans
out. Score heads send criteria as an ordered array of level descriptions
(2–10 levels) and come back with a `legend` map. Before designing tasks,
read the official patterns (fan-out, confidence routing, LLM guardrails, SDE
cascade) — primitive shapes and links: [TECHNICAL-DOCUMENTATION.md §5](TECHNICAL-DOCUMENTATION.md),
vendor docs at https://docs.typesafe.ai/.

---

## Install

### As a library

```bash
pip install sys1                # or: pip install git+https://github.com/adelvillar1/sys1
```

### With the HTTP service

```bash
pip install 'sys1[service]'
uvicorn service.main:app --host 0.0.0.0 --port 8400
```

### From a local clone (development)

```bash
git clone https://github.com/adelvillar1/sys1.git
cd sys1
uv sync --all-extras
uv run pytest                   # 188 tests
uv run sys1 doctor              # provider availability check
uv run sys1 providers           # registry: core + candidates, flag/config/health
```

---

## Quick start

```python
import sys1

# A registered task ("change" = diff-type + risk)
result = sys1.classify("decide", "change", open("staged.diff").read())
print(result.verdict, result.head("diff_type", "decide"))

# Two providers, side by side
both = sys1.classify(["decide", "jev"], "pr_gate", open("pr.txt").read())
print(both.telemetry["decide"]["latency_ms"], both.telemetry["jev"]["latency_ms"])

# Cascade: cheap first, escalate if low confidence
cascade = sys1.cascade_classify(["decide", "jev"], "issue_triage", issue_body)
```

### Your own task (TOML)

```toml
# tasks.example.toml
[[tasks]]
id = "security_review"
description = "Flag diffs that touch auth, PII, or crypto."
heads = [
  { id = "touches_auth", kind = "noul", floor = 0.85, task = "Does this diff change auth, sessions, or token handling?" },
  { id = "touches_pii",  kind = "noul", task = "Does this diff read, write, or transmit personally-identifiable data?" },
  { id = "severity",     kind = "choice", task = "If flagged, severity?", labels = ["low", "medium", "high"] },
]
```

```python
sys1.load_tasks_file("tasks.example.toml")
result = sys1.classify("decide", "security_review", diff_text)
```

`floor` on a head sets that head's uncertainty floor (here: 0.85 for the
high-stakes auth question); TOML head specs must stay on one line.

### Custom provider

```python
from sys1 import Provider, Head, register, Answer

def classify_via_my_model(text: str, heads: list[Head], cfg: dict, tele: dict) -> dict[str, Answer]:
    out = call_my_endpoint(...)            # your logic
    return {h.id: {"label": ..., "confidence": ...} for h in heads}

register(Provider(
    id="mine",
    name="My in-house model",
    classify=classify_via_my_model,
    is_available=lambda cfg: True,
))

result = sys1.classify("mine", "change", text)
```

---

## Recipe pack (examples)

`examples/` ships working patterns ported from the TypeSafe cookbooks
(details, sources, and run instructions in [`examples/README.md`](examples/README.md)):

| Example | Shape | Use it when |
|---|---|---|
| `guardrail-injection.toml` + `screen_message.py` | 5 hazard nouls + severity score in one fan-out call | Screening untrusted input/output; policies stay in code (review ≥ 0.35, action ≥ 0.70, block ≥ 2.0) and fail closed |
| `citation-check.toml` | noul "does the passage support the claim?" (`floor = 0.85`) + strength score | Verifying a claim against a quoted passage; below the high-stakes floor a human confirms |
| `lead-scoring.py` | 4 composite score heads | Blending ordered per-signal ratings with explicit weights |
| `rerank-passages.py` | N score heads in one call | Ranking passages (or any candidates); returns nothing when the best is weak |
| `taxonomy-walk.py` | one choice head per taxonomy level | Hierarchical classification on a budget: one call per level, cumulative confidence, weak edges bail via `head.floor` |

Every recipe runs against a live provider (`sys1 doctor` first) and is
smoke-tested offline in `tests/test_recipe_pack.py` with a stub provider —
no keys needed to check the wiring.

---

## HTTP service

A thin FastAPI wrapper so non-Python callers (Node, Go, shell, CI) use the
same `classify()` and the same JSONL log.

```bash
pip install 'sys1[service]'
uvicorn service.main:app --port 8400
```

```bash
curl -X POST http://localhost:8400/v1/classify \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SYS1_TOKEN" \
  -d '{"provider":"decide","task":"change","text":"<diff text>"}'
```

Endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/classify` | Run providers for one text payload |
| `POST` | `/v1/cascade` | Chain providers; stop at the first that clears |
| `GET`  | `/v1/health` | Per-provider availability snapshot |
| `GET`  | `/v1/tasks` | List registered task names + head summaries |
| `GET`  | `/v1/calibration` | Confidence curve + per-provider accuracy (from the JSONL log) |
| `GET`  | `/dashboard` | Control dashboard (single-page app) |
| `GET`  | `/v1/providers` | Full registry view: flags, config shape, availability + reasons |
| `POST` | `/v1/providers/{id}/flag` | Enable/disable a provider — persists `state.json` |
| `POST` | `/v1/providers/{id}/probe` | TCP/venv reachability probe |
| `GET`  | `/v1/recent` | Tail of the JSONL event log |

The service reads `SYS1_BEARER_TOKEN` from its env; when set, requests without
a matching `Authorization: Bearer …` header get `401`. When unset, the service
runs open — appropriate only behind a private network. The `/dashboard` page
and the read-only `/v1/providers` snapshot stay open even with a token set
(they expose only non-secret config shape); everything mutating — flags,
probes, classify, feedback, calibration, recent — requires the bearer.

---

## Control dashboard

`http://<host>:8400/dashboard` is a single-page operator console:

- **Provider cards** for the whole registry — tier/wire chips, a status pill
  (ready / not-configured / unreachable / disabled), the exact reason, the
  non-secret config view, and a setup hint on candidates. A toggle enables or
  disables any provider (writes `state.json`), and a probe button checks the
  endpoint without a model call.
- **Live classify console** — pick a task, set the floor, select providers
  (unavailable ones are struck through but selectable anyway), paste text,
  run. Verdict, latency, per-head labels with confidence bars, and per-head
  telemetry (HTTP status, tokens, retries) render inline.
- **Feedback capture** — type the expected label next to any head and save;
  it lands as a `feedback` row joined to the classify row by `input_sha256`,
  which is what feeds the calibration curve.
- **Recent activity** — the JSONL tail: time, task, providers, verdict,
  latency, labels, errors.

The bearer token goes in the header input once per browser session (kept in
`sessionStorage`); the page itself is always served so the JS can prompt.

---

## Calibration telemetry

Every `classify()` call (with `log=True`, the default) appends one JSONL record
to `~/.local/share/sys1/logs/YYYY/MM/DD/events.jsonl`:

```json
{
  "op": "classify",
  "task": "change",
  "provider": "decide",
  "providers_used": ["decide"],
  "input_chars": 1842,
  "input_sha256": "ab12cd34ef56ab78",
  "answers": {"decide": {"diff_type": {"label": "feat", "confidence": 0.91}, ...}},
  "verdict": "pass",
  "escalated": false,
  "latency_ms": 142,
  "telemetry": {"decide": {"http_status": 200, "latency_ms": 142, "retries": 0, ...}},
  "confidence_histogram": {"decide": [0,0,0,0,0,0,0,0,1,1]},
  "ts": "2026-09-27T10:11:12.345+00:00"
}
```

Join a human-feedback row back to a classify row via `input_sha256`:

```python
sys1.record_feedback(
    input_sha256="ab12cd34ef56ab78",
    task="change",
    head_id="diff_type",
    predicted_label="feat",
    predicted_confidence=0.91,
    provider="decide",
    actual_label="fix",         # the human's review outcome
    escalated=False,
)
```

`sys1.calibration_curve(feedback_rows)` turns the joined stream into a
per-bin predicted-vs-actual table for a dashboard.

---

## Why these decisions are hard to abstract away

Branching logic in code has two pieces:

1. **The criteria** — "is this a breaking change?"
2. **The control flow** — "if breaking, require two reviewers"

A decision model can replace (1): the criteria move into a head's task +
descriptions, the model returns a calibrated answer + confidence. The control
flow is yours: `if result.escalated:` is real Python, not a model call. sys1
replaces the *functions* that returned "yes"/"no"/"major", not the `if`
statements that gate on them.

When a judgment is **pattern-matching-shaped** (does this look like X?),
a System One model gives a more calibrated answer than regex or heuristics.
When it's **reasoning-shaped** (prove this invariant holds), it gives a worse
one — use a strong LLM or static analyzer for that, not sys1.

---

## Project layout

```
sys1/
├── src/sys1/
│   ├── types.py         Head, Answer, Task, Provider, ClassifyResult
│   ├── providers.py     core + candidate registry, wire factories, flags
│   ├── parsers.py       provider wire-shape → normalized Answer
│   ├── tasks.py         8 pre-registered tasks + TOML/JSON loader
│   ├── config.py        defaults, TOML load, env overrides, state.json
│   ├── gating.py        interpret() + final_exit_code()
│   ├── telemetry.py     JSONL log, redaction, histogram, feedback, calibration
│   ├── api.py           classify(), cascade_classify(), health_report()
│   └── cli.py           sys1 classify / doctor / providers / tasks / version
├── tests/               188 tests, recorded fixtures + mocked transports
├── service/
│   ├── main.py          FastAPI wrapper (classify/cascade/health/tasks/calibration)
│   ├── dashboard.py     control dashboard router + single-page app
│   ├── pyproject.toml   service-specific deps
│   └── Dockerfile       container build
├── examples/            recipe pack: guardrail/citation task files + pattern scripts
├── pyproject.toml       package metadata + optional extras
├── README.md            (you're here)
├── CHANGELOG.md         version history
└── LICENSE              Apache 2.0
```

---

## Relationship to other projects

- **[`dev-decisions`](https://github.com/adelvillar1/dev-decisions)** — the
  git-hook CLI this library was extracted from. `dev-decisions` is a consumer:
  it owns the repo-aware glue (find the staged diff, format the message,
  manage hooks) and delegates the model calls to `sys1`.
- **[`zcode-router-kit`](https://github.com/adelvillar1/zcode-router-kit)** —
  the Node.js auto-router. Today it talks to Jev/Decide directly in JS. The
  cascade pattern in `sys1.cascade_classify()` and the cross-provider
  reconciliation ledger in `sys1.provider_agreement()` are ports of
  `router/fastino.mjs` + `router/usage.mjs`; the same logic can run from
  either language by calling the service.
- **`gliner2`** and the **TypeSafe SDKs** are not dependencies. `sys1` speaks
  the hosted HTTP APIs directly via stdlib `urllib`, and the local GLiNER path
  via a small `subprocess` script that targets whatever venv you point at.

---

## Contributing

Issues and PRs welcome. Two things help most:

1. **Provider adapters.** A new model that speaks one of the supported shapes
   (or a documented wire format we can normalize) is the highest-leverage
   contribution. Open an issue first so we can agree on the contract.
2. **Calibration data.** Real-world `(predicted, actual)` pairs from your
   `record_feedback()` stream against a known task help set per-provider
   floors and write the calibration article.

Run the test suite before pushing:

```bash
uv run pytest
uv run ruff check .
```

---

## License

Apache 2.0. See [LICENSE](LICENSE).
