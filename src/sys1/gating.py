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

The default floor (0.7) was tuned for Decide in dev-decisions and is the
same default here; consumers should re-tune for Jev via
`cfg["classify"]["jev_confidence_floor"]` when both are in play.
"""

from __future__ import annotations

from .types import Answer, Head, Task, Verdict

# Default confidence floors per provider family. Tune per provider with
# `{provider}_confidence_floor` in [classify] (honored for every registered
# provider, candidates included — see _floors_for).
_DEFAULT_FLOORS = {
    # core
    "decide": 0.7,
    "local": 0.7,
    "modernbert": 0.55,  # fast but noisy, lower floor
    "jev": 0.7,
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
) -> tuple[Verdict, bool, list[str]]:
    """
    Walk every provider's answers, apply the per-provider floor, and decide.

    Returns `(verdict, escalated, summary)`. `summary` is a list of lines that
    can be joined with `\n` for the human-facing gate output — same shape the
    dev-decisions CLI prints today.

    `task` (optional) supplies the task's own `confidence_floor` override;
    when set it takes precedence over the global `cfg.classify.confidence_floor`
    (per-provider `{pid}_confidence_floor` overrides still win — see
    `_floors_for`).
    """
    escalated = False
    summary: list[str] = []
    task_floor = task.confidence_floor if task is not None else None
    floors = _floors_for(cfg, task_floor=task_floor)

    for prov, answers in answers_by_provider.items():
        floor = floors.get(prov, cfg["classify"].get("confidence_floor", 0.7))
        summary.append(f"\n── {prov} ──")
        for head_id, entry in answers.items():
            if head_id.startswith("_"):
                continue  # meta keys (_raw, _unmatched:*)
            label = _label_of(entry)
            conf = entry.get("confidence")
            rendered = f"{str(label)[:60] if label is not None else '(declined)'}"
            if conf is not None:
                summary.append(f"  {head_id}: {rendered} (conf {float(conf):.2f})")
            else:
                summary.append(f"  {head_id}: {rendered}")

            if label is None:
                escalated = True
                continue
            if conf is not None and float(conf) < floor:
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


def _heads_by_id(heads: list[Head] | None) -> dict[str, Head]:
    return {h.id: h for h in (heads or [])}


def _floors_for(cfg: dict, task_floor: float | None = None) -> dict[str, float]:
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
    # per-provider overrides win for every registered provider, not just those
    # with a default entry above.
    for prov in list(REGISTRY) + list(_DEFAULT_FLOORS):
        key = f"{prov}_confidence_floor"
        if key in cfg["classify"]:
            base[prov] = float(cfg["classify"][key])
    return base
