"""
Core dataclasses shared across the sys1 library.

The wire representation of an `Answer` is a plain dict that preserves the
existing dev-decisions / zcode-router-kit JSON shapes, so consumer dashboards
reading our JSONL log keep working unchanged.

Heads are keyed by a stable `id` (either supplied explicitly, or derived
deterministically from the task text). The legacy `task[:40]` truncation in
dev-decisions is gone: results dict keys are always head.id.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

HeadKind = Literal["choice", "noul", "score"]


def head_id_from_task(task: str) -> str:
    """Stable 8-hex-char id derived from the task text (sha1 prefix)."""
    return hashlib.sha1(task.encode("utf-8")).hexdigest()[:8]


@dataclass
class Head:
    """
    A single decision head (question) the model should answer.

    Field mapping across providers:
      - Decide / local / ModernBERT: `task` → `task`, `labels` → `labels`.
      - Jev: `task` → `instructions`, `descriptions` → `criteria`,
        `kind` → `type`. Jev supports per-option descriptions; Decide accepts
        flat labels only (descriptions ignored on hosted Decide).
      - `floor` (optional) raises this head's confidence floor above the
        task/global layer — see `gating.effective_floor` for the precedence
        ladder. Expressed in the serving provider's own confidence semantics.
    """

    id: str
    kind: HeadKind
    task: str
    labels: list[str] = field(default_factory=list)
    descriptions: dict[str, str] | None = None
    multi_label: bool = False
    score_min: int = 1
    score_max: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)
    floor: float | None = None  # per-head confidence floor override; see gating.effective_floor

    def to_decide_head(self) -> dict:
        return {"task": self.task, "labels": list(self.labels), "multi_label": self.multi_label}

    def to_local_head(self) -> dict:
        # Local GLiNER and ModernBERT use the Decide head schema.
        return self.to_decide_head()

    def to_jev_head(self) -> dict:
        if self.kind == "score":
            # Jev score criteria is an ordered array of level descriptions
            # (2-10 levels, index 0 = lowest) - verified against
            # docs.typesafe.ai/api and the ScoreCriteria SDK tuple type.
            # It is NOT a label->description map, and there is no min/max on
            # the wire: the levels are the array positions.
            criteria: Any = self._score_criteria()
        else:
            # Jev choice/noul criteria is a label->rubric map; Decide accepts
            # flat labels only.
            criteria = self.descriptions or {label_str: label_str for label_str in self.labels}
        return {
            "id": self.id,
            "type": self.kind,
            "instructions": self.task,
            "criteria": criteria,
        }

    def _score_criteria(self) -> list:
        labels = self.labels
        if all(label_str.isdigit() for label_str in labels):
            labels = sorted(labels, key=int)
        if self.descriptions:
            return [self.descriptions.get(label_str, label_str) for label_str in labels]
        return list(labels)

    def to_drex_head(self) -> dict:
        """
        Drex (/v1/systemone) question shape. Same three kinds as Jev, with one
        load-bearing delta: Drex's noul criteria reads ONLY `true`/`false`
        keys (any other key is ignored server-side), so make_noul's yes/no
        descriptions map onto those. choice criteria is the same
        label→description map and score criteria the same ordered level array
        as to_jev_head().
        """
        criteria: Any
        if self.kind == "score":
            criteria = self._score_criteria()
        elif self.kind == "noul":
            yes = str(self.metadata.get("yes", "yes"))
            no = str(self.metadata.get("no", "no"))
            descriptions = self.descriptions or {}
            criteria = {
                "true": descriptions.get(yes, f"Yes, {self.task.lower()}"),
                "false": descriptions.get(no, "No."),
            }
        else:
            criteria = self.descriptions or {label_str: label_str for label_str in self.labels}
        return {
            "type": self.kind,
            "instructions": self.task,
            "criteria": criteria,
        }

    def wire_head_for(self, provider: str) -> dict:
        if provider in ("local", "modernbert"):
            return self.to_local_head()
        if provider == "jev":
            return self.to_jev_head()
        if provider == "drex":
            return self.to_drex_head()
        return self.to_decide_head()


def make_choice(
    task: str,
    labels: list[str],
    *,
    id: str | None = None,
    descriptions: dict[str, str] | None = None,
    multi_label: bool = False,
    floor: float | None = None,
    **meta: Any,
) -> Head:
    return Head(
        id=id or head_id_from_task(task),
        kind="choice",
        task=task,
        labels=list(labels),
        descriptions=descriptions,
        multi_label=multi_label,
        metadata=meta,
        floor=floor,
    )


def make_noul(
    task: str,
    *,
    id: str | None = None,
    yes_label: str = "yes",
    no_label: str = "no",
    descriptions: dict[str, str] | None = None,
    floor: float | None = None,
    **meta: Any,
) -> Head:
    labels = [yes_label, no_label]
    return Head(
        id=id or head_id_from_task(task),
        kind="noul",
        task=task,
        labels=labels,
        descriptions=descriptions or {yes_label: f"Yes, {task.lower()}", no_label: "No."},
        metadata={**meta, "yes": yes_label, "no": no_label},
        floor=floor,
    )


def make_score(
    task: str,
    *,
    id: str | None = None,
    score_min: int = 1,
    score_max: int = 5,
    legend: list[str] | None = None,
    floor: float | None = None,
    **meta: Any,
) -> Head:
    # Levels rendered as string labels so provider conversion is uniform
    # across wires. A Jev score answer is a probability-weighted position on
    # the scale and can land between levels (a float); Decide-style
    # providers pick one of these labels.
    labels = [str(i) for i in range(score_min, score_max + 1)]
    descriptions = None
    if legend:
        # strict: a legend whose length doesn't match the level count is an
        # authoring error — fail loudly instead of silently truncating
        descriptions = dict(zip(labels, legend, strict=True))
    return Head(
        id=id or head_id_from_task(task),
        kind="score",
        task=task,
        labels=labels,
        descriptions=descriptions,
        score_min=score_min,
        score_max=score_max,
        metadata=meta,
        floor=floor,
    )


@dataclass
class Task:
    """A named collection of heads plus optional escalation policy overrides."""

    id: str
    heads: list[Head]
    description: str = ""
    providers: list[str] | None = None  # provider allow-list (default: all registered)
    confidence_floor: float | None = None
    provider_heads: dict[str, list[Head]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def head_ids(self) -> list[str]:
        return [h.id for h in self.heads]

    def find(self, head_id: str) -> Head | None:
        return next((h for h in self.heads if h.id == head_id), None)


# ── provider result wire shape (preserves dev-decisions JSON) ────────────────
#
# Each provider call returns `dict[head_id, Answer]`. Answer is a plain dict
# mirroring the existing shapes so the JSONL log + dashboards keep working:
#
#   {"label": "...", "confidence": 0.83, "probabilities": {...}, "type": "choice"}
#   {"noul": 0.95,  "type": "noul"}   # Jev: no confidence field - p IS the certainty
#   {"score": 3.4,   "confidence": 0.77, "legend": {"3": "level-3 rubric"}, "type": "score"}
#   {"_raw": "<unparsed>"}
#
# A missing head (provider returned no answer for it) becomes a `declined`
# entry with confidence=0.0 so downstream gating treats it as escalate-worthy.

Answer = dict


def declined(reason: str = "no-answer") -> Answer:
    return {"_raw": "", "_declined": reason, "confidence": 0.0}


# ── provider interface ────────────────────────────────────────────────────────

ClassifyFn = Callable[[str, list[Head], dict, dict], dict[str, Answer]]
AvailabilityFn = Callable[[dict], bool]


@dataclass
class Provider:
    """
    A pluggable decision provider.

    `classify(text, heads, cfg, telemetry, images=None) -> dict[head_id,
    Answer]` — the provider's response, already normalized to the Answer wire
    shape above. Implementations own retry/backoff and telemetry population.
    `images` is an optional list of base64 image payloads (data URLs or
    {content_type, base64} objects); wires that accept vision input (liquid)
    forward them to the provider, text-only wires ignore them.

    Flag fields (see providers.py `enabled_set`):
      - `tier`: "core" (the built-ins, on by default) or "candidate"
        (opt-in models, off until listed in `providers.enabled`).
      - `wire`: which request shape the provider speaks — "jev", "openai",
        or "gliner". The dashboard and the wire factories key off this.
      - `is_configured`: cheap check that the provider's endpoint / venv /
        API key exists, independent of the enable flag. When None, the
        report falls back to `is_available`.
    """

    id: str
    name: str
    classify: ClassifyFn
    is_available: AvailabilityFn
    description: str = ""
    tier: str = "core"
    wire: str = "openai"
    is_configured: AvailabilityFn | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ── top-level result ──────────────────────────────────────────────────────────

Verdict = Literal["pass", "escalate", "block", "error"]


@dataclass
class ClassifyResult:
    task: str
    provider: str
    providers_used: list[str]
    answers: dict[str, dict[str, Answer]]  # provider_id → head_id → Answer
    telemetry: dict[str, dict] = field(default_factory=dict)
    verdict: Verdict = "pass"
    escalated: bool = False
    latency_ms: int = 0
    reason: str | None = None
    summary: list[str] = field(default_factory=list)

    def head(self, head_id: str, provider: str | None = None) -> Answer | None:
        """Return the answer for `head_id` from one provider (first match if None)."""
        provs: tuple[str, ...] = (provider,) if provider is not None else tuple(self.answers.keys())
        for prov in provs:
            answers = self.answers.get(prov) or {}
            if head_id in answers:
                return answers[head_id]
        return None

    def best_answer(self, head_id: str) -> Answer | None:
        """Highest-confidence answer for a head across all providers."""
        candidates = [self.answers[p].get(head_id) for p in self.answers]
        candidates = [a for a in candidates if isinstance(a, dict)]
        if not candidates:
            return None
        return max(candidates, key=lambda a: float(a.get("confidence") or 0.0))

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the log record and the HTTP response."""
        return {
            "task": self.task,
            "provider": self.provider,
            "providers_used": list(self.providers_used),
            "answers": {p: dict(h) for p, h in self.answers.items()},
            "telemetry": {p: dict(t) for p, t in self.telemetry.items()},
            "verdict": self.verdict,
            "escalated": self.escalated,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "summary": list(self.summary),
        }
