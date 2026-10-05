"""
Confidence gating and verdict interpretation.

This is the policy half of the library: takes the normalized
`{provider: {head_id: Answer}}` dict from a classify() call and turns it into
a verdict ("pass" / "escalate" / "block") plus a human-readable summary.

Confidence semantics differ per provider and must not be thresholded with the
same number:

  - Jev: distribution-shape statistic `(N*max_prob - 1)/(N - 1)`. Binary
    (noul) collapses to max_prob; 3+ options diverge. A "0.7" Jev confidence
    on a 5-choice question means something different than on a binary.
  - Decide / local / ModernBERT: max-probability from the encoder head.
    0.7 is closer to a literal "70% sure".

Noul heads: a Jev noul answer carries no `confidence` field — the 0-1 output
is the probability of "true", so `answer_confidence` derives the comparable
certainty `max(p, 1 - p)` (a confident "no" clears a floor like a confident
"yes" does). An explicit `confidence` always wins when a provider emits one.

The default floor (0.7) was tuned for Decide in dev-decisions and is the
same default here; consumers should re-tune for Jev via
`cfg["classify"]["jev_confidence_floor"]` when both are in play.
"""

from __future__ import annotations

from .types import Answer, Head, Task, Verdict

# Default confidence floors per provider family. Tune per provider with
# `{provider}_confidence_floor` in [classify] (honored for every registered
# provider, candidates included — see floors_for).
_DEFAULT_FLOORS = {
    # core
    "decide": 0.7,
    "local": 0.7,
    "modernbert": 0.55,  # fast but noisy, lower floor
    "jev": 0.7,
    "drex": 0.7,  # Jev-family calibrated semantics; noul derives max(p, 1-p)
    "glide": 0.7,  # Jev-family: parse_glide strips noul confidence (|2p-1| scale) so noul derives max(p, 1-p)
    "julia": 0.7,  # noul derives max(p, 1-p); choice/score confidence is the top1 probability — directly thresholdable
    "liquid": 0.7,  # Jev-family: choice/score confidence is the (N*max_prob-1)/(N-1) distribution statistic; noul derives max(p, 1-p)
    # candidates — inherit their wire family's semantics
    "clm": 0.7,          # jev wire
    "openjev": 0.65,     # community reproduction, slightly less trusted
    "kev": 0.7,          # openai wire
    "tev1": 0.7,         # openai wire
    "laya": 0.6,         # openai wire, unconfirmed calibration
    "decide_1b": 0.7,    # gliner wire (Decide semantics)
    "decide_multi": 0.65,  # gliner wire, multilingual boundary variant
}


def interpret(
    answers_by_provider: dict[str, dict[str, Answer]],
    cfg: dict,
    heads: list[Head] | None = None,
    task: Task | None = None,
    *,
    floor_override: float | None = None,
) -> tuple[Verdict, bool, list[str]]:
    """
    Walk every provider's answers, apply the per-provider floor, and decide.

    Returns `(verdict, escalated, summary)`. `summary` is a list of lines that
    can be joined with `\n` for the human-facing gate output — same shape the
    dev-decisions CLI prints today.

    `task` (optional) supplies the task's own `confidence_floor` override;
    when set it takes precedence over the global `cfg.classify.confidence_floor`
    (per-provider `{pid}_confidence_floor` overrides still win — see
    `floors_for`). A head's own `floor` (see `effective_floor`) overrides the
    task/global layer but never the per-provider config key. `floor_override`
    (a request-level floor, e.g. cascade_classify's `confidence_floor`
    argument) outranks the task floor so the walk and the verdict agree.
    """
    escalated = False
    summary: list[str] = []
    task_floor = floor_override if floor_override is not None else (
        task.confidence_floor if task is not None else None
    )
    floors = floors_for(cfg, task_floor=task_floor)
    head_by_id = _heads_by_id(heads)

    for prov, answers in answers_by_provider.items():
        summary.append(f"\n── {prov} ──")
        for head_id, entry in answers.items():
            if head_id.startswith("_"):
                continue  # meta keys (_raw, _unmatched:*)
            label = _label_of(entry)
            conf = answer_confidence(entry)
            floor = effective_floor(cfg, floors, prov, head_by_id.get(head_id))
            rendered = f"{str(label)[:60] if label is not None else '(declined)'}"
            if conf is None:
                summary.append(f"  {head_id}: {rendered}")
            elif entry.get("confidence") is None:
                # no explicit confidence on the wire (Jev noul): show the
                # derived certainty so the floor comparison is auditable
                summary.append(f"  {head_id}: {rendered} (conf {conf:.2f} derived)")
            else:
                summary.append(f"  {head_id}: {rendered} (conf {float(conf):.2f})")

            if label is None:
                escalated = True
                continue
            if conf is not None and conf < floor:
                escalated = True
    verdict: Verdict
    if not escalated:
        verdict = "pass"
    elif cfg["classify"].get("block_on_classification", False):
        verdict = "block"
    else:
        verdict = "escalate"
    return verdict, escalated, summary


def final_exit_code(escalated: bool, cfg: dict) -> int:
    """EXIT_BLOCK / EXIT_WARN / EXIT_OK semantics (mirrors git hook convention)."""
    block = cfg["classify"].get("block_on_classification", False)
    if not escalated:
        return 0
    return 2 if block else 1


# ── helpers ───────────────────────────────────────────────────────────────────


def _label_of(entry: Answer) -> object | None:
    """The headline value: choice label, score integer, or noul boolean."""
    for key in ("label", "score", "noul"):
        if key in entry and entry[key] is not None:
            return entry[key]
    return None


def answer_confidence(entry: Answer) -> float | None:
    """The floor-comparable confidence for one answer, or None when it has none.

    Order of preference:
      1. An explicit `confidence` field (Decide/local/ModernBERT/candidates,
         and any provider that emits one).
      2. Jev noul answers carry no confidence — the 0-1 output is the
         probability of "true", so derive `max(p, 1 - p)`: a confident "no"
         (p=0.05) must clear a floor just like a confident "yes" (p=0.95).
      3. None — no gateable number (bool noul, missing field). Callers treat
         that as "cannot verify" and never floor-gate on it.

    Never raises: a non-numeric confidence degrades to None (the gate must
    not break on a malformed provider answer).
    """
    conf = entry.get("confidence")
    if conf is not None:
        try:
            return float(conf)
        except (TypeError, ValueError):
            return None
    p = entry.get("noul")
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return None
    prob = float(p)
    return max(prob, 1.0 - prob)


def effective_floor(
    cfg: dict, floors: dict[str, float], prov: str, head: Head | None = None
) -> float:
    """Resolve the confidence floor for one head on one provider.

    Precedence, top wins:
      1. explicit per-provider `{pid}_confidence_floor` config key
      2. the head's own `floor`
      3. task `confidence_floor`
      4. global `confidence_floor`
      5. `_DEFAULT_FLOORS[pid]`

    `floors` (from `floors_for(cfg, task_floor=...)`) already encodes layers
    3-5 plus the config key; this inserts the head layer *below* the config
    key so per-provider tuning still wins. A head floor is expressed in the
    *serving provider's own* confidence semantics (Jev concentration vs Decide
    max-prob) — fit it per provider and never reuse a number across providers
    expecting it to mean the same thing (see the hard rule in CLAUDE.md).
    Shared by `interpret()` and `cascade_classify()` so the two paths can't
    drift.
    """
    key = f"{prov}_confidence_floor"
    classify_cfg = cfg.get("classify", {})
    if key in classify_cfg:
        return float(classify_cfg[key])
    if head is not None and head.floor is not None:
        return float(head.floor)
    return floors.get(prov, classify_cfg.get("confidence_floor", 0.7))


def _heads_by_id(heads: list[Head] | None) -> dict[str, Head]:
    return {h.id: h for h in (heads or [])}


def floors_for(cfg: dict, task_floor: float | None = None) -> dict[str, float]:
    """Merge code defaults ← global floor ← task floor ← per-provider overrides.

    `task_floor` (a Task's own `confidence_floor`) overrides the global
    `cfg.classify.confidence_floor` but is still overridden by explicit
    per-provider `{pid}_confidence_floor` config keys.
    """
    from .providers import REGISTRY

    base = dict(_DEFAULT_FLOORS)
    global_floor = cfg["classify"].get("confidence_floor")
    effective_global = task_floor if task_floor is not None else global_floor
    if effective_global is not None:
        base = {k: float(effective_global) for k in base}
        # the task/global layer applies to every provider, custom and
        # registered candidates included — not just the ones with a default
        # entry above. Without this, a task floor silently falls back to the
        # global floor for providers outside _DEFAULT_FLOORS.
        for prov in REGISTRY:
            base.setdefault(prov, float(effective_global))
    # per-provider overrides win for every registered provider, not just those
    # with a default entry above.
    for prov in list(REGISTRY) + list(_DEFAULT_FLOORS):
        key = f"{prov}_confidence_floor"
        if key in cfg["classify"]:
            base[prov] = float(cfg["classify"][key])
    return base
