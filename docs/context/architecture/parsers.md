---
title: Parsers — provider wire shapes to the normalized Answer
status: shipped
sources:
  - src/sys1/parsers.py
  - tests/test_parsers.py
related:
  - architecture/providers-and-flags.md
  - architecture/tasks-and-heads.md
---

# Parsers

`parsers.py` turns one provider's raw response into the shared `{head_id: Answer}` dict from
`types.py`. The Answer shape mirrors dev-decisions' log keys so JSONL consumers never migrate:
choice `{"label", "confidence", "probabilities"?}`, noul `{"noul": 0.95}` (no confidence — p IS the
certainty), score `{"score", "legend"?, "confidence"?}`, or `{"_raw": ...}` when nothing parsed.

## Per-provider entry points

- `parse_decide` — hosted Decide hides JSON in chat-completions `choices[0].message.content`;
  `_match_keys_to_heads` maps returned keys back to head ids via `_head_keys` (id, task text,
  truncated legacy forms), keeping unmatched keys as `_unmatched:*` raw entries.
- `parse_jev` — accepts both the SDK shape (`{answers: {id: {...}}}`) and a chat-completions
  wrapper around it (`_maybe_json`). `_jev_entry` normalizes choice/score/noul, including the
  boolean-under-`label` and `"yes"`/`"no"` string variants Jev sometimes returns.
- `parse_drex` — `/v1/systemone` answers with Drex's calibrated probability fields; noul reads the
  explicit confidence when present.
- `parse_glide` — like Drex but **strips noul confidence** (GLiDE computes it on a `|2p-1|` margin
  scale) so gating derives `max(p, 1-p)` family-consistently — see [gating.md](gating.md).
- `parse_julia` — like Drex; choice/score confidence is Julia's **top1 probability** (kept — a
  probability scale, thresholdable at the family floor); score legend arrives as `null` and is
  synthesized from `head._score_criteria()`.
- `parse_liquid` — like Drex; choice/score confidence is the Jev-family **distribution statistic**
  `(N*max_prob - 1)/(N - 1)` (kept — directly thresholdable at the family floor, NOT a margin);
  score legend/probabilities arrive as dicts keyed by index strings, so the legend keys shift
  directly; no `expected_level` extra.
- `parse_gliner2` — the fastino-prescribed gliner2 API result: `_prescribed_answer` reads the full
  `probabilities` distribution per task; `_legacy_answer` handles the old flat task[:40]-keyed
  script shape. Choice answers keep their label probability; noul becomes the P(true) float.
- `parse_modernbert` — cosine-similarity eval output (`all_scores`, `top2_gap` ride along).

Every parser maps missing heads to `types.declined(reason)` (confidence 0.0) so gating escalates
instead of guessing.

## Gotchas

- Tolerant by design: a content string that fails JSON parsing becomes `{"_raw": ...}` and the
  provider's `structured_ok` telemetry flag goes false — parsing never raises out.
- The gliner2 spec → schema translation lives in both `_LOCAL_INLINE` (providers.py) and
  `service/classifier_server.py`; keep the two identical when the spec shape changes.
