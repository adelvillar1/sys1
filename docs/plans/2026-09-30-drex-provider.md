---
status: completed
created: 2026-09-30
updated: 2026-09-30
slug: drex-provider
---

# Drex provider — Jev-class hosted model (128K context) on the sys1 roster

## Context

Nace's Drex (https://drex.nace.ai) is a Jev-class decision model: `POST /v1/systemone` takes one `state` plus typed questions (`noul`/`choice`/`score`) and returns calibrated probabilities — the same three primitives and wire family as TypeSafe Jev, with a **131,072-token state window** on `drex-v1.5` (vs Jev's dev-diff-scale inputs). The user verified the key (2026-09-30: noul 0.931 on an urgency question, 8.8ms model time) and picked "wire as a sys1 provider": add `drex` to the core roster alongside `decide`/`jev`.

Key lives in `<repo>/.env` as `DREX_API_KEY` (gitignored; pointer in CLAUDE.local.md). Agent docs: https://drex.nace.ai/llms-full.txt.

## Approach

A dedicated `_drex_wire` (not `_jev_wire` reuse) because four Drex deltas are load-bearing:

1. **`state` is a plain string** (the documented canonical input), not Jev's `{"text": ...}` wrapper — and it is **not** truncated to `scan.max_diff_chars`: the 128K window is the feature. New `providers.drex_max_chars` (default 400,000 ≈ headroom under 131,072 tokens) bounds it instead.
2. **Noul criteria keys**: Drex reads only `true`/`false`; Jev's criteria map uses the yes/no labels. New `Head.to_drex_head()` maps `make_noul`'s descriptions onto `{"true": ..., "false": ...}`. choice/score criteria are identical to `to_jev_head()`.
3. **Score is 0-indexed**: Drex returns `score = Σ index × probability` over the criteria array; sys1 scales run `score_min..score_max`. `parse_drex` shifts by `score_min` and converts Drex's returned legend *array* into sys1's `{level: description}` dict.
4. **Usage keys**: Drex reports `input_tokens`/`output_tokens` (no `total_tokens`); the wire maps them onto the contract telemetry keys `token_prompt`/`token_completion`/`token_total` and records `evaluation_time_ms` + `request_id` as extras.

Everything else is house machinery: `http_post_json` retry/backoff (429/529/500 retryable — matches Drex's guidance; 401/402/422 fail fast), `_hosted_available` (key present = available), `_requires_key` core tuple, tier `core` (auto-enabled via `providers.enabled = "core"`), `wire="jev"` so the dashboard/flags/config view treat it as a URL+key provider, and `gating._DEFAULT_FLOORS["drex"] = 0.7` (Jev-family calibrated semantics; noul derives `max(p, 1-p)` via the existing `answer_confidence`).

Config: `drex_api_url` (https://drex.nace.ai/v1/systemone), `drex_model` (pinned `drex-v1.5`; `drex-latest` alias documented), `drex_api_key_env` (DREX_API_KEY), `drex_max_chars`.

## Acceptance criteria

- [x] `Head.to_drex_head()` emits Drex-valid questions: noul criteria under `true`/`false`, choice criteria label→description map, score criteria ordered level array
- [x] `parse_drex` normalizes Drex answers to the shared Answer wire: choice→`label`/`confidence`/`probabilities`, noul→`{"noul": p}` (no confidence field — derived in gating), score→`score` in sys1 scale units (score_min shift) + legend dict; missing answers → declined
- [x] `_drex_wire` sends `state` as a plain string bounded by `drex_max_chars`, model/url/key from config, questions keyed by head id; telemetry keys `http_status`, `latency_ms`, `retries`, `structured_ok`, `token_prompt/completion/total` all populated (usage mapped from `input_tokens`/`output_tokens`)
- [x] `drex` is registered tier="core", wire="jev", available iff `DREX_API_KEY` is set; appears in `sys1 providers --all`, `doctor`, and the dashboard report with the non-secret config view
- [x] `gating._DEFAULT_FLOORS["drex"]` exists with Jev-family semantics documented
- [x] Tests cover head mapping, answer parsing (incl. score shift + legend), wire body shape + usage mapping (urlopen mocked), availability/config, floor entry — full suite stays green, ruff clean
- [x] A live one-question noul call through `sys1.classify(provider="drex", ...)` matches the raw curl result (± format drift)

## Files to be touched

- `src/sys1/types.py` (to_drex_head), `src/sys1/parsers.py` (parse_drex), `src/sys1/config.py` (defaults), `src/sys1/providers.py` (_drex_wire + register + _requires_key), `src/sys1/gating.py` (floor)
- `tests/test_providers.py`, `tests/test_parsers.py`
- `TECHNICAL-DOCUMENTATION.md` (provider registry section), `README.md` (provider table, if it lists one)

## Out of scope

- Making `drex` a *default* provider for any dev-decisions gate (adoption/parity is a separate plan — the user explicitly chose roster-only)
- `state` as structured JSON objects (sys1 sends text today)
- dev-decisions-side changes; the service dashboard HTML (flags machinery already lists core providers generically)

## Verification

- `.venv/bin/python -m pytest -q` — full suite green
- `.venv/bin/ruff check .` — clean
- `SYS1_PROVIDERS__ENABLED=core .venv/bin/python -m sys1.cli providers --all | grep drex`
- Live: `set -a && . ./.env && set +a && .venv/bin/python -c "import sys1; r = sys1.classify('drex', 'change', 'Help! My payouts have been failing for 3 days.'); print(r.answers)"` — answer parses, verdict computed

## Linked artifacts

- `TECHNICAL-DOCUMENTATION.md` — provider registry table + telemetry keys
- `docs/recaps/` — session recap notes the .env/.gitignore change (housekeeping rule 5)

## Outcome (2026-09-30) — per-criterion evidence

1. **to_drex_head** — `types.py`; noul criteria under `true`/`false` proven by `TestDrexProvider::test_noul_criteria_uses_true_false_keys` and parser-test head fixtures.
2. **parse_drex** — choice→label/confidence/probabilities, noul p with no confidence field, score shifted by `score_min` (2.3 → 3.3 on a 1..5 scale) with legend array→dict; missing → declined (`TestParseDrex`, 5 cases).
3. **_drex_wire** — plain-string state bounded by `drex_max_chars` (4 → "abcd" in test), model/url/key from config, questions keyed by head id; telemetry `http_status/latency_ms/retries/structured_ok/token_prompt/token_completion/token_total` all asserted populated from `input_tokens`/`output_tokens` (106/212/318).
4. **Roster** — tier `core`, `wire="jev"`, `_requires_key` core tuple; live `providers --all` shows `[on] drex  jev  configured → available`; `provider_config_view` is url-kind with `DREX_API_KEY`.
5. **Floor** — `gating._DEFAULT_FLOORS["drex"] = 0.7`, asserted in tests.
6. **Tests** — 249 passed (was 238), ruff clean for all new code (16 pre-existing errors unchanged, per CLAUDE.md's known-debt list).
7. **Live** — real call through `sys1.classify('drex', 'change', 'Help! My payouts have been failing for 3 days.')`: 4 heads (inherited jev's change-task heads incl. both nouls), diff_type=fix 0.84, risk_tier=low 0.32 → verdict `escalate` (correct gate behavior), 237 prompt tokens, 303ms, structured_ok.

Also landed: `heads_for(task, "drex")` inherits the `jev` head set where tasks define one — the Jev-replacement property, no per-task duplication.
