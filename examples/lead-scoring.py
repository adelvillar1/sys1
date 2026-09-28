#!/usr/bin/env python3
"""Composite lead scoring — official pattern /patterns/composite-scoring (via
systemonemodels.org/recipes → composite-lead-scoring).

Pattern: several Score heads (each an ordered scale) normalize to 0-1 with
(score - min) / (max - min), then combine with explicit weights. The weights
are a business decision, so they live here in code — the model supplies the
per-signal scores, not the blend.

All heads run in ONE classify() call (fan-out). Score answers are
probability-weighted positions and may land between levels — normalization
handles the floats as-is.

Usage:
    .venv/bin/python examples/lead-scoring.py [--provider decide] < lead.json
where lead.json is {"title": ..., "description": ...}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sys1

# the blend: signal -> (score range, weight). Weights are yours to defend.
SIGNALS = {
    "budget_fit": ((1, 5), 0.35),
    "urgency": ((1, 5), 0.25),
    "authority": ((1, 3), 0.25),
    "engagement": ((1, 5), 0.15),
}

TASK = sys1.Task(
    id="lead_scoring",
    description="Composite lead score from ordered per-signal ratings.",
    heads=[
        sys1.make_score("How well does this lead's company match our ideal budget?", score_min=lo, score_max=hi, id=sid)
        for sid, ((lo, hi), _) in SIGNALS.items()
    ],
)


def score_lead(text: str, provider: str = "decide", cfg: dict | None = None):
    """Return (weighted 0-1 score, per-signal normalized parts)."""
    cfg = cfg or sys1.load_config()
    result = sys1.classify(provider, TASK, text, cfg=cfg, log=False)
    parts: dict[str, float] = {}
    for sid, ((lo, hi), _) in SIGNALS.items():
        answer = result.head(sid, provider=provider) or {}
        raw = answer.get("score")
        if not isinstance(raw, (int, float)):
            raise SystemExit(f"provider returned no score for {sid!r} — cannot blend")
        parts[sid] = (float(raw) - lo) / (hi - lo)
    total = sum(parts[sid] * SIGNALS[sid][1] for sid in SIGNALS)
    return total, parts


def main() -> int:
    ap = argparse.ArgumentParser(description="Composite-score a lead from a JSON blob.")
    ap.add_argument("file", nargs="?", help="lead JSON file (default: stdin)")
    ap.add_argument("--provider", default="decide", help="provider id (default: decide)")
    args = ap.parse_args()

    blob = json.loads(Path(args.file).read_text() if args.file else sys.stdin.read())
    text = blob.get("description") or blob.get("title") or json.dumps(blob)
    total, parts = score_lead(text, provider=args.provider)
    for sid, part in parts.items():
        print(f"  {sid:<14} {part:.2f}  (weight {SIGNALS[sid][1]:.2f})")
    print(f"composite score: {total:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
