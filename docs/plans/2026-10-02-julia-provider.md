---
status: completed
created: 2026-10-02
updated: 2026-10-02
slug: julia-provider
---

# Julia-1 provider — a BERT-class systemone model, wired and calibrated

## Context

Supersoniclabs' Julia-1 (`julia-1`, https://api.supersoniclabs.ai/v1/systemone) is a
BERT-based decision model on the `/v1/systemone` wire: same three primitives
(noul/choice/score), 8192-token window, **$0.025 per 1M input tokens / $0 output** —
the cheapest hosted model in the pool. Fan-out is native (BERT single pass): 6 heads
in one request measured 0.47s; single-call latency 0.5–1.5s — faster than glide,
slightly slower than jev.

Live-verified contract (2026-10-02 probes — the evidence base for every delta below):

- **Auth**: `Authorization: Bearer` works. **`Idempotency-Key` header is REQUIRED**
  (missing → HTTP 400 `{"error": "idempotency_key_required"}`). Send one fresh uuid
  per logical call (reused across that call's retries — the point of the header).
- **User-Agent REQUIRED (client-side delta)**: Cloudflare on this host returns
  **403 error 1010** for Python-urllib's default signature — which is exactly what
  `http_post_json` sends. The julia wire must pass `User-Agent` via `extra_headers`.
  Verified: default UA → 403; `sys1/x` UA → 200.
- **Strict validation** (Drex/GliDE-strict): question objects accept exactly
  `type` / `instructions` / `criteria` — an extra `id` → 422
  ("unknown field `id`, expected `instructions` or `criteria`").
  **`Head.to_drex_head()` is a drop-in question mapper** (probe: noul with
  `{true,false}` criteria accepted; choice label→description map accepted).
- **Score takes `criteria` as an ARRAY** of level descriptions (a map → 422
  "invalid type: map, expected a sequence"; Jev's `levels` key → 422 "unknown field").
  Same ordered-array convention `to_drex_head()` already produces.
- **Score is 0-indexed** like Drex/GliDE: 5 criteria → probabilities keyed
  `"0".."4"`, `score` = Σ index×p (2.296 with p(3)=0.65 dominant). Shift by
  `head.score_min` at parse. **`legend` arrives as `null`** (Drex sends an array,
  GliDE an index-keyed dict) — parse synthesizes the legend from `head.labels`.
- **Confidence = top1 probability** on choice/score (`confidence` byte-equals
  `probabilities[top1]` on both probes) — a probability scale, NOT GliDE's
  top1−top2 margin. Keep as returned; the 0.7 floor thresholds it directly.
- **noul**: `{type, noul: p}` — no confidence field on the wire, so nothing to
  strip; gating derives max(p, 1−p) family-consistently.
- **Envelope**: `answers` keyed by question name; `usage.input_tokens/output_tokens/
  total_tokens`; echoes `model: "julia-1"`.
- **Window**: 8192-token rendered-prompt limit. Overflow does NOT surface as a
  structured error — a ~9.7K-token request returned a raw **HTTP 502**. Bound the
  state client-side: `julia_max_chars` (12,000 ≈ measured ~2.4–2.5 chars/token on
  prose with headroom for question text; the repetitive-text probe hit 6.2 c/t).
- One first-connection TLS stall observed (curl error 35) that never recurred across
  ~10 subsequent calls — `http_post_json`'s network-error retry covers it.

Key handling: `SUPERSONIC_API_KEY` stored in `<repo>/.env` (gitignored) at probe
time — sys1/.env is the canonical home for all sys1 decision-model keys (user
directive, 2026-10-02).

## Approach

### Phase 1 — julia provider (the GliDE pattern, julia deltas)

Dedicated `_julia_wire` (house pattern: one wire factory per vendor, deltas in its
docstring) and `parse_julia` (parse_glide skeleton, julia deltas):

1. **`state` is a plain string** bounded by new `providers.julia_max_chars`
   (default 12,000 — 8192-token window with headroom), NOT `scan.max_diff_chars`.
2. **Questions via `to_drex_head()`** — verified live (no `id`; noul
   `{true,false}`; score criteria array).
3. **`http_post_json` extras**: `extra_headers={"Idempotency-Key": str(uuid4()),
   "User-Agent": "sys1"}` — both required by this host (probes above).
4. **`parse_julia`** (parse_glide skeleton, two deltas):
   - choice/score: **keep** the explicit confidence (top1-probability scale —
     directly thresholdable; no strip decision like GliDE's margin).
   - score: same `score_min` shift; `legend` is `null` on the wire → synthesize
     `{str(head.score_min + i): label}` from `head.labels`. No `expected_level`
     ride-along (not in the response).
5. **Registration**: tier `core`, `wire="jev"`, `classify=_julia_wire("julia")`,
   `_hosted_available`; `"julia"` into `_requires_key`'s core tuple;
   `gating._DEFAULT_FLOORS["julia"] = 0.7`; `heads_for` jev-inheritance extended
   to `("drex", "glide", "julia")`.
6. **Telemetry**: `usage.input_tokens/output_tokens` →
   `token_prompt/completion/total` (same mapping as `_glide_wire`;
   `total_tokens` also present but the sum is equivalent).
7. **Routing**: register + add `julia` to `_SIZE_EXCLUDED` (8192-token bound —
   below glide's 40K, so the size gate must push it back before glide does), but
   **do NOT touch the default chain** (`glide,drex,jev`): the cascade contract
   says the first provider must be well-calibrated, so calibration evidence
   (phase 1 gate) precedes any chain change. A follow-up decision may admit
   julia as a small-input head (it is cheaper and faster than glide) — recorded
   in the Outcome, not smuggled in here.

Config: `julia_api_url` (https://api.supersoniclabs.ai/v1/systemone), `julia_model`
(pinned `julia-1`), `julia_api_key_env` (SUPERSONIC_API_KEY), `julia_max_chars`
(12,000).

### Calibration gate (closes phase 1)

Run the four-way label comparison julia vs glide vs jev vs drex on the
2026-10-02 change corpus (same six texts as the glide retry) + the user's
rollback scenario. Record agreement + confidence deltas in the Outcome. This
evidence (not cost) drives any later routing decision — house rule: route on
capacity + shape + calibration, never on cost.

## Acceptance criteria

- [ ] `_julia_wire` sends `state` as a plain string bounded by `julia_max_chars`,
      model/url/key from config, questions via `to_drex_head()`, and the required
      `Idempotency-Key` + `User-Agent` headers (body+headers asserted with urlopen mocked)
- [ ] `parse_julia` maps choice→`label`/`confidence`(kept)/`probabilities`,
      noul→`{"type": "noul", "noul": p}` (no confidence — none exists), score→
      `score_min`-shifted float with legend synthesized from `head.labels`;
      missing answers → declined; non-JSON/4xx surfaces as RuntimeError
- [ ] Telemetry keys `http_status`, `latency_ms`, `retries`, `structured_ok`,
      `token_prompt/completion/total` populated from `usage` (mock 225/0)
- [ ] `julia` registered tier="core", wire="jev"; `providers --all`, doctor, and
      the dashboard show it when `SUPERSONIC_API_KEY` is set; `_requires_key` covers julia
- [ ] `heads_for(task, "julia")` returns the jev head set wherever a task defines one
- [ ] `gating._DEFAULT_FLOORS["julia"]` exists with the top1-probability semantics documented
- [ ] `routing._SIZE_EXCLUDED` includes julia; the default chain is unchanged
      (`glide,drex,jev`) and `route()` output for short/long inputs is asserted unchanged
- [ ] `SUPERSONIC_API_KEY` in repo `.env` (gitignored); a live
      `sys1.classify('julia', 'change', ...)` parses 4 heads
- [ ] Four-way calibration recorded in this plan's Outcome (label agreement +
      confidence deltas vs glide/jev/drex)
- [ ] Full suite green; ruff clean for all new code (existing in-flight
      plan-surface files excluded — not this plan's scope)

## Files to be touched

- `src/sys1/providers.py` — `_julia_wire` + register + `_requires_key`
- `src/sys1/parsers.py` — `parse_julia`
- `src/sys1/config.py` — `julia_*` defaults
- `src/sys1/gating.py` — floor entry
- `src/sys1/tasks.py` — `heads_for` julia→jev inheritance (one line)
- `src/sys1/routing.py` — `_SIZE_EXCLUDED` + docstring note
- `tests/test_providers.py`, `tests/test_parsers.py`
- `README.md`, `TECHNICAL-DOCUMENTATION.md`, `CLAUDE.md` — roster/wire/state rows
- `CLAUDE.local.md` — key table row (`SUPERSONIC_API_KEY`, name only)
- `.env` — `SUPERSONIC_API_KEY` (gitignored, never committed)

## Out of scope

- Adding julia to the routed default chain (waits on calibration evidence; the
  Outcome records the recommendation)
- Quota tracking / adaptive routing (unchanged house position)
- The `Idempotency-Key` header for other vendors (only this host requires it;
  `http_post_json` stays untouched — extras are per-wire)
- A global default User-Agent in `http_post_json` (other wires work unmodified;
  scoping the header to julia avoids perturbing working integrations)
- Liquid d1 / candidate-tier wiring

## Verification

- `.venv/bin/python -m pytest -q` — full suite green
- `.venv/bin/ruff check src tests` — clean (repo-root check includes the
  in-flight untracked plan-surface files; scoped check is the gate for this plan)
- `SYS1_PROVIDERS__ENABLED=core .venv/bin/python -m sys1.cli providers --all | grep julia`
  → `[on ] julia  jev  configured → available`
- Live single-provider: `sys1.classify('julia', 'change', 'Help! My payouts have
  been failing for 3 days.')` — 4 heads parsed, verdict computed
- Live routing unchanged: `route('change', short)` → `[glide, drex, jev]`;
  `route('change', 'x'*150_000)` → drex-first
- Calibration: julia vs glide/jev/drex on the six-text corpus → Outcome

## Linked artifacts

- `TECHNICAL-DOCUMENTATION.md` — wire table + "Julia-1 deltas" section
- `docs/recaps/` — session recap on implementation (housekeeping rule 5)
- Probe evidence: 2026-10-02 session — 8 live probes quoted in Context

## Outcome (2026-10-02) — per-criterion evidence

1. **_julia_wire** (`providers.py`) — plain-string state bounded by `julia_max_chars`
   ("abcd" at 4, `TestJuliaProvider::test_state_truncated_by_julia_max_chars`); questions
   via `to_drex_head()` with no `id` field and `true`/`false` noul criteria (asserted in
   `test_call_julia_wire_headers_and_usage`); **`Idempotency-Key` (asserted parseable uuid)
   and `User-Agent` headers present on the request** — the Cloudflare 1010 / urllib-UA
   finding encoded; 502 → RuntimeError (`test_502_window_overflow_raises`).
2. **parse_julia** (`parsers.py`) — choice confidence kept (top1-probability scale, asserted
   byte-equal to the probe value); noul has no confidence on the wire and none after parse;
   score 0-indexed → `score_min` shift (2.296 → 3.296) with **legend synthesized from
   `head._score_criteria()` when the wire sends `legend: null`** (the sent criteria array IS
   the legend, in wire order); missing → declined; the 2026-10-02 live-probe response shapes
   captured verbatim in `TestParseJulia::test_live_probe_shapes`.
3. **Telemetry** — `usage.input_tokens/output_tokens` → `token_prompt/completion/total`
   (mock 152/0/152 asserted; live classify 4 heads, structured_ok).
4. **Roster** — tier `core`, `wire="jev"`; `providers --all` shows
   `[on ] julia  jev  configured → available` (key from repo `.env`);
   `provider_config_view` url-kind with `SUPERSONIC_API_KEY`.
5. **Floor** — `gating._DEFAULT_FLOORS["julia"] = 0.7` with top1-probability semantics
   documented (directly thresholdable, unlike GliDE's margins).
6. **heads_for** — `heads_for(task, "julia")` returns the jev change-head set (4 heads),
   asserted equal to jev's id list (`test_heads_for_inherits_jev_set`); live classify
   returned all 4.
7. **Key** — `SUPERSONIC_API_KEY` in repo `.env` (gitignored), stored at probe time.
8. **Routing unchanged** — `route('change', short)` → `[glide, drex, jev]`;
   `route('change', 150k chars)` → `[drex, jev, glide]`; `julia` in `_SIZE_EXCLUDED`
   (`test_routing_default_chain_unchanged_and_size_excluded`); default chain untouched.
9. **Calibration (four-way, six-text change corpus + probe text; log=False;
   /tmp/julia_calibration_2026-10-02.json)** — **julia agrees with glide AND jev only 1/6
   on diff_type and 1/6 on risk_tier.** Failure pattern: refactors labeled docs/test
   (dt_conf 0.24–0.69), a flag-gated feature rated risk=high 0.97, the big-bang Friday
   deploy rated risk=low 0.85, breaking_change under-detected (0.21–0.25 vs family 0.9+
   on t5/t6). Its risk_tier confidence runs high on labels the family disagrees with —
   overconfident, not miscalibrated-low. **Calibration gate FAILS for chain admission:
   julia stays out of the default chain** (house rule: route on capacity+shape+
   calibration, never cost). Position: cheap fast BERT triage head — 0.5–1.2s, ~18×
   cheaper input than glide, fan-out native — candidate for per-task adoption via
   `providers.route.by_task` only with per-task fitted floors, and for bulk
   classification where the Jev-family taxonomies are not the contract. Latency:
   julia 0.5–1.2s vs glide 0.7–9.0s / jev 0.2–0.46s / drex 0.27–0.53s.
10. **Tests** — 321 passed (13 new julia tests across test_providers/test_parsers);
    ruff clean for all of src+tests (remaining repo-root errors are in the concurrent
    plan-surface session's untracked scripts, not this plan's scope).

Incidental: the GLiDE "~free" billing claim (user-corrected 2026-10-02) is fixed in
config.py's comment and README's provider row in this commit — the earlier correction
had landed in config only.
