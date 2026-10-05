---
status: completed
created: 2026-10-05
updated: 2026-10-05
slug: liquid-d1-provider
---

# Liquid AI d1 provider — the pool's first vision-capable decision model

## Context

The user asked for a second chance for Liquid AI (deferred from the julia plan as
"Liquid d1 / candidate-tier wiring"): a 64K context window, native vision, and
"system 1 compatible api responses". Liquid d1 (`d1`, https://api.liquid.ai)
turns out to be the TypeSafe System One API itself — `POST /decisions/v1/systemone`
with the same state/questions/answers contract as jev/drex/glide/julia, plus one
extension (OpenJev `images`) that makes it the pool's only vision-capable model.

Live-verified contract (2026-10-05 probes — the evidence base for every delta below):

- **Auth**: `Authorization: Bearer <LIQUID_API_KEY>`; no idempotency or
  User-Agent headers required (both were julia-host requirements — d1 needs
  neither, unlike Julia's Cloudflare front).
- **Endpoint**: `POST https://api.liquid.ai/decisions/v1/systemone`, model id
  `d1` (`GET /decisions/v1/models` lists exactly two decision models: `d1` with
  `input_modalities: ["text", "image"]` and `d1:free` with `["text"]`).
- **Strict validation like Drex/GliDE/Julia**: question objects accept exactly
  `type`/`instructions`/`criteria` — a wrong shape is a 422. **`Head.to_drex_head()`
  is a drop-in question mapper** (same convention as the other three wires).
- **Images (the OpenJev extension)**: up to 8 images per request as base64
  `data:image/…;base64,` URLs or `{content_type, base64}` objects (JPEG/PNG/WebP/
  GIF, 5MB each, 4.5MB request-body cap). Remote URLs rejected. Images come
  before the state in the prompt, every question sees all of them, and each
  question bills its images again: 1.5 input tokens per 32×32 patch
  (`ceil(w/32)×ceil(h/32)` per image, rounded up), ≤10,000 patches per request,
  no image >100× as long as wide. This is the reason sys1's `classify()` and
  `cascade_classify()` gained an optional `images` kwarg — see the fragment notes.
- **Window**: d1 caps each rendered prompt at 65,536 tokens (every question
  re-renders the whole state, images included) and 250KB of text; either
  overflow returns 422 — bound the state client-side at `liquid_max_chars`.
  Probed at ~4.4 chars/token on prose (9,639 chars → 2,173 tokens), so the
  150,000-char default keeps dense text under the cap with headroom.
- **Choice/Score confidence is the Jev-family distribution statistic**
  `(N*max_prob − 1)/(N − 1)` over the probabilities — verified byte-equal on the
  wire (3 options, top1 p=0.9305552282044707 → conf 0.8958328423067061; the
  score probe p=0.7769657544995859 → conf 0.6654486317493788). NOT a
  top1−top2 margin — directly thresholdable at the 0.7 family floor; kept as
  returned by `parse_liquid`.
- **noul**: `{type, noul: p}` — no confidence field on this wire (same as Julia);
  gating derives max(p, 1−p) family-consistently with nothing to strip.
- **Score**: 0-indexed expected level like Drex/GliDE (shifted by `head.score_min`
  at parse); `legend` and `probabilities` arrive as dicts keyed by index STRINGS
  ("0".."N-1"), so legend keys shift directly. No `expected_level` extra.
- **Usage**: `usage.input_tokens/output_tokens` (output always 0) plus `cost` in
  USD when the model is priced — d1 reports $4e-8/token ($0.04 per 1M input,
  output free). Mapped onto the contract telemetry keys; `cost` rides along as
  `cost_usd` for the calibration stream.
- **user constraint (2026-10-05)**: the free `d1:free` model must never be
  exercised — all probes and all code target `d1` only.

Key handling: `LIQUID_API_KEY` is in the auto-loaded env layer
(`~/.config/dev-decisions/env` today; the canonical `~/.config/sys1/env` when it exists — see
`config.load_provider_env`), so liquid live-registers from any shell with no sourcing. Per the
user directive (2026-10-02) the repo's gitignored `.env` keeps the local reference copy of the
same key. No new env file was created: a partial `~/.config/sys1/env` would shadow the legacy
file and break the other providers that read their keys from there.

## Approach

### Phase 1 — liquid provider (the julia/glide pattern, d1 deltas)

Dedicated `_liquid_wire` (house pattern: one wire factory per vendor, deltas in
its docstring) and `parse_liquid` (parse_glide skeleton, d1 deltas):

1. **`state` is a plain string** bounded by new `providers.liquid_max_chars`
   (default 150,000 — the 65,536-token cap has ~4.4 chars/token headroom on
   prose), NOT `scan.max_diff_chars`.
2. **Questions via `to_drex_head()`** — same strict shape as the other wires.
3. **`images` passthrough** (the one structural change beyond the wire itself):
   every wire factory, `call_provider`, `classify`, and `cascade_classify` gained
   an optional `images` param; only `_liquid_wire` emits it (body `images` +
   `telemetry["image_count"]` — count only, never content, in telemetry or logs).
4. **`parse_liquid`** (parse_glide skeleton, two deltas):
   - choice/score: **keep** confidence (distribution statistic — thresholdable
     at the family floor, like Julia's top1-p; no GliDE-style margin strip).
   - score: same `score_min` shift; legend is an index-keyed dict → keys shift
     directly; synthesize from `head._score_criteria()` when absent.
5. **Registration**: tier `core`, `wire="jev"`, `classify=_liquid_wire("liquid")`,
   `_hosted_available`; `"liquid"` into `_requires_key`'s core tuple;
   `gating._DEFAULT_FLOORS["liquid"] = 0.7`; `heads_for` jev-inheritance extended
   to `("drex", "glide", "julia", "liquid")`.
6. **Telemetry**: `usage.input_tokens/output_tokens` →
   `token_prompt/completion/total`; `cost` → `cost_usd` extra.
7. **Routing**: register but **do NOT touch the default chain** (`glide,drex,jev`)
   — the cascade contract's calibration gate comes first. Unlike julia, liquid
   does NOT go into `_SIZE_EXCLUDED`: its 150K-char bound (65,536-token window)
   exceeds the router's long-input threshold, so the size gate never pushes it
   back for a reason that no longer applies.

Config: `liquid_api_url` (https://api.liquid.ai/decisions/v1/systemone),
`liquid_model` (pinned `d1`), `liquid_api_key_env` (LIQUID_API_KEY),
`liquid_max_chars` (150,000).

### Calibration gate (closes phase 1)

Compare liquid vs the family (glide/drex/jev/julia) on the standard six-text
change corpus + the vision probes. Record label agreement and confidence deltas
in the Outcome. Vision agreement has no family baseline — no other provider
speaks images — so that evidence is qualitative (correctness on known inputs).

## Acceptance criteria

- [x] `_liquid_wire` sends `state` as a plain string bounded by `liquid_max_chars`,
      model/url/key from config, questions via `to_drex_head()` (no `id`),
      bearer auth without extra headers, images only when passed (mock asserts)
- [x] `parse_liquid` keeps choice/score confidence (distribution statistic,
      byte-equal to probes), noul without confidence, score shifted by
      `score_min` with dict legend keys shifted; missing answers → declined
- [x] Telemetry keys populated from `usage` + `cost_usd`; `image_count` when
      images are sent; count never logged as content
- [x] `liquid` registered tier="core", wire="jev"; providers --all / doctor /
      dashboard show it when `LIQUID_API_KEY` is set; `_requires_key` covers it
- [x] `heads_for(task, "liquid")` returns the jev head set wherever defined
- [x] `gating._DEFAULT_FLOORS["liquid"] = 0.7` with distribution-statistic
      semantics documented
- [x] `liquid` NOT in `routing._SIZE_EXCLUDED`; default chain unchanged
- [x] `LIQUID_API_KEY` in the auto-loaded env layer (doctor/providers see it); live text classify
      parses 4 heads; live vision classify reads a PNG correctly
- [x] Full suite green; ruff clean (the two pre-existing repo-root lint errors
      in unrelated scripts were fixed in this commit, not silenced)

## Files to be touched

- `src/sys1/providers.py` — `_liquid_wire` + register + `_requires_key` + images param
- `src/sys1/parsers.py` — `parse_liquid`
- `src/sys1/config.py` — `liquid_*` defaults
- `src/sys1/gating.py` — floor entry
- `src/sys1/tasks.py` — `heads_for` liquid→jev inheritance
- `src/sys1/api.py` — `images` kwarg on classify/cascade_classify + log count
- `src/sys1/types.py`, every wire factory, `tests/*` — uniform images param
- `docs/context/**` fragments, `README.md`, `TECHNICAL-DOCUMENTATION.md`,
  `CLAUDE.md`, `FUNCTIONAL-SPECIFICATIONS.md` — roster/wire/vision rows
- `.env` — `LIQUID_API_KEY` (gitignored, never committed)

## Out of scope

- Adding liquid to the routed default chain (waits on calibration evidence;
  the Outcome records the recommendation)
- A vision-first task or head set (no task in the registry needs images yet;
  callers pass `images=` to `classify()`/`cascade_classify()` explicitly)
- Any use of `d1:free` (user constraint 2026-10-05: the free model is never
  exercised — text-only, keyless, 32K-token window)
- Batch/streaming endpoints (not part of the System One wire)

## Verification

- `.venv/bin/python -m pytest -q` — full suite green
- `.venv/bin/ruff check .` — clean (repo-wide; the pre-existing errors in
  `.agents/skills/sys1-context/scripts/build-map.py` and `src/sys1/__init__.py`
  were repaired in this commit)
- `.venv/bin/sys1 doctor` → `✓ liquid: Liquid AI d1 (hosted) — available`
- `.venv/bin/sys1 providers --all` → `[on ] liquid  jev  configured → available`
- Live text: `sys1.classify('liquid', 'change', <checkout-timeout text>)` — 4
  heads parsed, verdict computed (escalate on risk_tier's 0.37 < 0.7 floor)
- Live vision: `sys1.classify('liquid', <choice/noul/score task>, 'A 64x64 image.',
  images=["data:image/png;base64,…"])` — 64×64 solid-blue PNG read as "blue"
  (conf 0.99995), shows_text noul 5.4e-06, complexity 1.0

## Linked artifacts

- `TECHNICAL-DOCUMENTATION.md` — wire table + "Liquid d1 deltas" section
- `docs/context/architecture/providers-and-flags.md` — registry + vision notes
- `docs/recaps/` — session recap on implementation (housekeeping rule 5)
- Probe evidence: 2026-10-05 session — model list, text request, image request

## Outcome (2026-10-05) — per-criterion evidence

1. **_liquid_wire** (`providers.py`) — plain-string state bounded by
   `liquid_max_chars` ("abcd" at 4, `test_state_truncated_by_liquid_max_chars`);
   questions via `to_drex_head()` with no `id` field and `true`/`false` noul
   criteria (asserted); url == `https://api.liquid.ai/decisions/v1/systemone`;
   bearer auth with no extra headers (asserted); `models` == "d1"; images only
   when passed — `test_images_passthrough` asserts both d1-accepted shapes
   (data-URL and `{content_type, base64}`) land in the body and
   `telemetry["image_count"]` is set; text-only calls stay minimal.
2. **parse_liquid** (`parsers.py`) — choice confidence kept byte-equal to the
   probe (0.8958328423067061, asserted alongside the statistic identity
   `(N*max−1)/(N−1)`); noul has no confidence on the wire and none after parse
   (0.9433475830538234); score 0-indexed → `score_min` shift (1.1237 → 2.1237)
   with dict legend keys shifted and no `expected_level`; legend synthesized
   from score criteria when absent; live-probe response shapes captured verbatim
   in `TestParseLiquid::test_live_probe_shapes` (text probe + the blue-PNG probe).
3. **Telemetry** — `usage.input_tokens/output_tokens` → `token_prompt/completion/
   total` (mock 171/0/171 + `cost_usd` 6.84e-06 asserted); live text classify:
   506 tokens, cost $2.02e-05; live vision: 166 tokens, image_count 1, cost
   $6.64e-06.
4. **Roster** — tier `core`, `wire="jev"`; doctor shows
   `✓ liquid: Liquid AI d1 (hosted) — available` and `providers --all` shows
   `[on ] liquid  jev  configured → available` (key from the auto-loaded env layer);
   `provider_config_view` url-kind with `LIQUID_API_KEY`; `CORE` in
   test_provider_flags gains liquid (tier assertion green).
5. **Floor** — `gating._DEFAULT_FLOORS["liquid"] = 0.7`, distribution-statistic
   semantics documented (directly thresholdable, unlike GliDE's margins).
6. **heads_for** — `heads_for(task, "liquid")` returns the jev head set (4 heads
   for change), asserted equal to jev's id list (`test_heads_for_inherits_jev_set`).
7. **Key** — `LIQUID_API_KEY` lives in the auto-loaded env layer (legacy
   `~/.config/dev-decisions/env`), so doctor/providers see it with no sourcing;
   the repo `.env` reference copy was updated with the same key (gitignored).
8. **Routing unchanged** — `route('change', short)` → `[glide, drex, jev]`;
   liquid NOT in `_SIZE_EXCLUDED` (150K-char bound > the long-input threshold);
   `test_routing_default_chain_unchanged_and_not_size_excluded` asserts both.
9. **Live verification** — text: diff_type fix (conf 0.921), risk_tier high
   (0.372 — below floor → escalate, correct behavior), breaking_change noul
   0.0049, personal_data noul 0.9047; vision: 64×64 solid-blue PNG → color
   "blue" conf 0.99995, shows_text noul 5.4e-06 (derived conf 1.0), complexity
   score 1.0 ("plain"), image_count 1 in telemetry. Verdict: pass.
10. **Calibration note** — text answers on the change corpus are sensibly
    conservative (risk high on a double-charge/rollback text = family-consistent
    with drex/jev readings); the calibration corpus comparison is not yet run —
    recorded as the phase-1 gate for any chain admission. Vision has no family
    baseline (pool-wide first), so its evidence is qualitative: correct on
    known inputs.
11. **Tests** — 347 passed (18 new: 17 liquid-specific across
    test_providers/test_parsers + the api-level images-passthrough test, plus the
    flags-roster update); `ruff check .` clean repo-wide (4 pre-existing errors
    repaired: 2 in `.agents/skills/sys1-context/scripts/build-map.py`, RUF022 in
    `src/sys1/__init__.py`'s `__all__`).

Incidental: the repo-root ruff check had drifted (pre-existing I001/F401 in an
untracked skill script and RUF022 in `__init__.py`) — both repaired per the
fix-don't-leave-debt convention, in this commit.
