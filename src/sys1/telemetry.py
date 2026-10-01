"""
JSONL logging and calibration helpers.

Same on-disk shape dev-decisions writes — `<log_dir>/YYYY/MM/DD/events.jsonl`
plus an optional feedback join. Lets existing dashboards point at either
location; the keys match.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import Answer

DEFAULT_REDACT = ("content", "context", "match", "_raw")


def log_dir_for(cfg: dict) -> Path:
    base = cfg["classify"].get("log_dir") or str(Path.home() / ".local" / "share" / "sys1" / "logs")
    return Path(base).expanduser() / datetime.now(timezone.utc).strftime("%Y/%m/%d")


def log_record(record: dict, cfg: dict | None = None, *, redact: Iterable[str] = DEFAULT_REDACT) -> Path | None:
    """Append one JSONL record. Returns the path, or None if the write failed.

    Logging must never raise — a broken disk should not break a git hook.
    """
    try:
        base = log_dir_for(cfg or {"classify": {}})
        base.mkdir(parents=True, exist_ok=True)
        path = base / "events.jsonl"
        rec = copy.deepcopy(record)
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
        for field in redact:
            _redact_field(rec, field)
        with open(path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return path
    except Exception:
        return None


def _redact_field(obj: Any, field: str) -> None:
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k == field and isinstance(v, str):
                obj[k] = "[redacted]"
            else:
                _redact_field(v, field)
    elif isinstance(obj, list):
        for item in obj:
            _redact_field(item, field)


def sanitize_for_log(answers: dict[str, dict[str, Answer]], redact_fields: Iterable[str] = DEFAULT_REDACT) -> dict:
    """Deep-copy + strip anything that looks like raw text the user might not want logged."""
    safe = copy.deepcopy(answers)
    fields = tuple(redact_fields)
    for heads in safe.values():
        if not isinstance(heads, dict):
            continue
        for _head_id, entry in heads.items():
            if isinstance(entry, dict):
                for f in fields:
                    if f in entry and isinstance(entry[f], str):
                        entry[f] = "[redacted]"
    return safe


# ── calibration helpers ───────────────────────────────────────────────────────


def confidence_histogram(answers: dict[str, dict[str, Answer]], *, bins: int = 10) -> dict[str, list[int]]:
    """Bucket per-provider confidence into `[0, 1/bins, ..., 1]` for the dashboard."""
    buckets: dict[str, list[int]] = defaultdict(lambda: [0] * bins)
    for prov, heads in answers.items():
        for _head_id, entry in heads.items():
            if not isinstance(entry, dict):
                continue
            conf = entry.get("confidence")
            if conf is None:
                continue
            try:
                c = float(conf)
            except (TypeError, ValueError):
                continue
            if c != c:  # NaN
                continue
            idx = min(bins - 1, max(0, math.floor(c * bins)))
            buckets[prov][idx] += 1
    return dict(buckets)


def provider_agreement(answers: dict[str, dict[str, Answer]]) -> dict[str, dict[str, Any]]:
    """
    Cross-provider agreement per head. Useful for the cascade / reconciliation
    ledger (ported from zcode-router-kit's recordCascadeEscalation).

    Returns `{head_id: {providers_with_label, agree, disagree, abstain, distribution}}`
    where `providers_with_label` is the count of providers that returned a label
    for this head, `agree` is whether they all picked the same one, etc.
    """
    by_head: dict[str, dict[str, str]] = defaultdict(dict)
    for prov, heads in answers.items():
        for head_id, entry in heads.items():
            if head_id.startswith("_") or not isinstance(entry, dict):
                continue
            label = entry.get("label") or entry.get("score")
            if label is None:
                continue
            by_head[head_id][prov] = str(label)
    out: dict[str, dict[str, Any]] = {}
    for head_id, per_prov in by_head.items():
        values = list(per_prov.values())
        if not values:
            continue
        c = Counter(values)
        top, top_count = c.most_common(1)[0]
        out[head_id] = {
            "providers_with_label": len(values),
            "abstain": 0,
            "agree": top_count == len(values),
            "disagree": top_count != len(values),
            "majority_label": top if top_count > len(values) / 2 else None,
            "distribution": dict(c),
        }
    return out


# ── feedback (predicted → actual human review outcome) ───────────────────────


def record_feedback(
    *,
    input_sha256: str,
    task: str,
    head_id: str,
    predicted_label: str | None,
    predicted_confidence: float | None,
    provider: str,
    actual_label: str | None,
    escalated: bool,
    cfg: dict | None = None,
) -> Path | None:
    """One row in the feedback log. Joins with `events.jsonl` via `input_sha256`."""
    return log_record(
        {
            "op": "feedback",
            "input_sha256": input_sha256,
            "task": task,
            "head_id": head_id,
            "predicted": predicted_label,
            "predicted_confidence": predicted_confidence,
            "provider": provider,
            "actual": actual_label,
            "escalated": escalated,
        },
        cfg=cfg,
    )


def calibration_curve(records: Iterable[dict], *, bins: int = 10) -> list[dict]:
    """
    From a stream of feedback rows, compute per-bin predicted-vs-actual accuracy.
    Bins cover confidence `[0, 1]`. Output: `[{"bin_lower", "bin_upper", "count",
    "predicted_mean", "accuracy"}, ...]`.
    """
    bins_data: list[list[float]] = [[] for _ in range(bins)]
    bins_correct: list[list[bool]] = [[] for _ in range(bins)]
    for r in records:
        conf = r.get("predicted_confidence")
        if conf is None:
            continue
        try:
            c = float(conf)
        except (TypeError, ValueError):
            continue
        idx = min(bins - 1, max(0, int(c * bins)))
        bins_data[idx].append(c)
        bins_correct[idx].append(bool(r.get("predicted") == r.get("actual")))

    out: list[dict] = []
    for i in range(bins):
        if not bins_data[i]:
            continue
        lower = i / bins
        upper = (i + 1) / bins
        acc = sum(1 for x in bins_correct[i] if x) / len(bins_correct[i])
        out.append(
            {
                "bin_lower": round(lower, 2),
                "bin_upper": round(upper, 2),
                "count": len(bins_data[i]),
                "predicted_mean": round(sum(bins_data[i]) / len(bins_data[i]), 3),
                "accuracy": round(acc, 3),
            }
        )
    return out
