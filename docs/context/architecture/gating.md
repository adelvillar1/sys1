---
title: Gating — confidence semantics, floors, and verdicts
status: shipped
sources:
  - src/sys1/gating.py
  - tests/test_gating.py
related:
  - architecture/tasks-and-heads.md
  - architecture/routing-and-cascade.md
---

# Gating

`gating.py` is the policy half: it turns the normalized `{provider: {head_id: Answer}}` dict into a
verdict (`pass` / `escalate` / `block`) plus a human summary. `interpret` walks every provider's
answers, applies the per-provider floor, and escalates on any declined label or sub-floor
confidence.

## Confidence semantics differ per provider family

- Jev-family (jev, drex, glide, liquid): confidence is a distribution-shape statistic
  `(N*max_prob - 1)/(N - 1)`; a 0.7 on a 5-choice question is not a 0.7 on a binary.
- Julia: confidence is the top1 probability — same 0.7 floor applies (a probability scale, not a
  margin), but a Julia 0.7 is not literally "70% sure" the way ModernBERT's is.
- Decide/local/ModernBERT: max-probability; 0.7 is closer to literal "70% sure".

**Noul answers carry no explicit confidence on the Jev wire** — the 0-1 output is P(true).
`answer_confidence` derives the comparable certainty `max(p, 1 - p)` so a confident "no" clears a
floor like a confident "yes". An explicit `confidence` field always wins when a provider emits one.
A non-numeric or missing number degrades to `None`, and `None` is never floor-gated ("cannot
verify"), it only escalates when the label itself is missing.

## The floor ladder

`effective_floor` resolves per head+provider, top wins:

1. explicit `{pid}_confidence_floor` config key (per-provider tuning always wins)
2. the head's own `floor`
3. task `confidence_floor`
4. global `classify.confidence_floor`
5. `_DEFAULT_FLOORS[pid]` (per-provider code defaults: decide/local 0.7, modernbert 0.55, laya 0.6, …)

`floors_for` merges layers 3–5 plus the config keys into one dict; it applies a task/global floor to
*every* registered provider (custom candidates included) via `setdefault`, not just those with a
default entry. `interpret` and `cascade_classify` both call the same helpers so the two paths cannot
drift; the cascade passes `floor_override` so its walk and its verdict agree.

## Verdicts and exit codes

`block_on_classification` (config) decides whether an escalation is a `block` or an `escalate`.
`final_exit_code` maps to the git-hook convention: 0 pass, 2 block, 1 warn. The summary lines render
each head with its label and confidence, marked `(conf x.xx derived)` when the noul certainty was
computed rather than explicit, so the floor comparison stays auditable in gate output.

## Gotchas

- Never reuse one floor number across provider families — see the hard rule in CLAUDE.md and the
  per-provider notes in `_DEFAULT_FLOORS`.
- Answers for meta keys (`_raw`, `_unmatched:*`) are skipped by `interpret` (anything starting `_`).
