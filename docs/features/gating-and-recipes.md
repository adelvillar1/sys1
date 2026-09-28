# Feature: Noul gating, per-head floors, and the recipe pack

> Plan: [`docs/plans/2026-09-28-noul-gating-and-recipe-pack.md`](../plans/2026-09-28-noul-gating-and-recipe-pack.md)
> Branch: `feature/provider-flags-and-dashboard`

## What this feature does

Three related changes, all around one question: *when does a head answer clear the floor?*

1. **Noul answers are gated.** A Jev noul answer carries no `confidence` field — the 0–1 output is the probability of "true". sys1 now derives the floor-comparable certainty `max(p, 1 − p)` so noul heads gate at all.
2. **Floors can be per-head.** `Head.floor` slots into the precedence ladder above the task floor, so one task can hold risk-scaled thresholds ("reads act at 0.6, refunds need 0.85").
3. **The vendor recipes ship as examples.** Five recipes from the TypeSafe cookbook patterns, with sourced task files and short scripts under `examples/`.

## Noul gating rule

`gating.answer_confidence(entry)` resolves the one number a floor can gate on:

| Answer shape | Derived certainty |
|---|---|
| `{"confidence": 0.83}` (choice/score) | `0.83` — explicit field, as before |
| `{"noul": 0.95}` (Jev, "yes") | `max(0.95, 1 − 0.95) = 0.95` |
| `{"noul": 0.05}` (Jev, confident "no") | `max(0.05, 0.95) = 0.95` — a confident "no" is a real answer |
| `{"noul": 0.55}` (coin flip) | `0.55` — below a 0.7 floor, escalates |
| `{"noul": 0.95, "confidence": 0.60}` (Decide-shaped noul) | `0.60` — explicit confidence wins, derived value ignored |
| `{"noul": true}` / `{"noul": false}` (bool) | `None` — nothing to derive; not floored |
| `{"noul": null}` or declined | not a value; `label is None` escalates |

Why `max(p, 1 − p)`: for an accept/escalate gate, certainty is the right statistic — a confident "no" should clear the floor rather than look like failure. This matches the binary case of the CMU cascade paper (gate on the max probability) and the rule in the `typesafe-jev` skill.

**Raw-probability gating stays caller-side.** The hazard-battery pattern (act when p(jailbreak) ≥ 0.7) gates on the raw probability of a specific label — a different axis from model uncertainty. Those thresholds live in the recipe's caller code, e.g. the review/action/block tiers in `examples/screen_message.py`, never inside `interpret()`.

## Floor precedence ladder

Single source of truth: `gating.effective_floor`. Highest wins:

1. **per-provider config key** — `classify.{pid}_confidence_floor` (the cross-provider knob)
2. **head floor** — `Head.floor` (code factories or task files)
3. **task floor** — explicit `confidence_floor` argument / `Task.confidence_floor`
4. **global** — `classify.confidence_floor`
5. **provider default** — `_DEFAULT_FLOORS[pid]` (e.g. ModernBERT 0.55, Decide/Jev 0.7)

`interpret()` and `cascade_classify()` resolve floors through the same helpers (`answer_confidence`, `effective_floor`, `floors_for`), so the cascade walk floor and the verdict floor cannot drift.

**Cross-provider semantics (the trap the CLAUDE.md rule warns about).** A head floor scalar applies in the serving provider's own confidence semantics: Jev's `confidence` is a distribution-shape statistic (concentration), Decide's is the max probability. Reusing one number across providers and expecting equivalent behavior is a mistake. Express a per-provider threshold with the config key; use `head.floor` when the number means the same thing for the provider that will serve it.

## Per-head floor in code and task files

```python
from sys1 import make_noul, make_score, make_choice

# code factories
make_noul("Does this passage support the claim?", id="supported", floor=0.85)
make_score("Rate the fit 1–5.", score_min=1, score_max=5, id="fit", floor=0.6)
make_choice("Severity?", ["low", "high"], id="severity", floor=0.8)
```

Task files (`sys1.load_tasks_file`):

```toml
[[tasks]]
id = "citation_check"
heads = [
  { id = "supported", kind = "noul", floor = 0.85, task = "Does the passage support the claim?" },
]
```

TOML head specs are inline tables — they must stay on one line (TOML 1.0; tomllib enforces it). `provider_heads.<prov>` is a list of head specs.

## Recipe pack

Five recipes, each a task file plus a short script, ported from the TypeSafe cookbook patterns (all credited in [`examples/README.md`](../../examples/README.md)):

| Recipe | Pattern | Heads | Caller-side policy |
|---|---|---|---|
| `guardrail-injection.toml` + `screen_message.py` | `/cookbooks/llm_guardrails` | 5 hazard nouls (`floor = 0.6`) + severity score | review ≥ 0.35, action ≥ 0.70, block ≥ 2.0; fails closed |
| `citation-check.toml` | `/cookbooks/citation_check` + `/patterns/confidence-routing` | supported noul (`floor = 0.85`) + strength score | high-stakes floor only; below it, escalates |
| `lead-scoring.py` | `/patterns/composite-scoring` | 4 score heads, one fan-out call | weights + `(score − min)/(max − min)` in code |
| `rerank-passages.py` | `/cookbooks/rerank_typesafe` | N score heads (one per passage), one fan-out call | sort, cutoff 3.0, return-nothing-if-weak |
| `taxonomy-walk.py` | `/cookbooks/hierarchical_classification` | one choice head (`id="edge"`, `floor = 0.6`) per level | cumulative confidence, depth normalization, weak-edge bail |

Common design lesson from the vendor patterns: **the model returns labels and numbers; thresholds stay in your code.** The recipe scripts each hold their policy as named constants, and `head.floor` gates only model uncertainty — an axis the recipes keep separate from the policy tiers.

Run any recipe with a live provider (`sys1 doctor` first) or rehearse the wiring offline: every recipe is smoke-tested in `tests/test_recipe_pack.py` against a registered stub provider — no API keys, no network.

Threshold numbers in the recipes (0.6, 0.85, 0.35/0.70/2.0) are vendor starting points, not tuned values. Fit them from your own JSONL calibration stream (`sys1.record_feedback` + `sys1.calibration_curve`); published thresholds do not transfer across workloads.

## Behavior changes to be aware of

- Tasks whose noul heads are served by Jev used to pass on any non-declined answer; they now gate on the noul probability. `dev-decisions`' `safety` task is the loudest case — verdicts differ there by design (this was the bug). `block_on_classification` stays opt-in, so exit codes change only for operators who asked for blocking.
- `floors_for()` now applies the effective task/global floor to custom (non-core) providers, which previously fell through to the global default. This is what keeps cascade parity for stubs and custom providers.
- Answers stay verbatim in the JSONL log (public contract) — derived certainty is computed at gate time, not persisted. Dashboards that want it can recompute via `sys1.answer_confidence`.
- `examples/custom-task.toml` had never parsed (multi-line inline tables + a `provider_heads` dict where the loader wants a list); it is fixed and covered by `test_shipped_task_files_all_parse`.
