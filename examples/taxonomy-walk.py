#!/usr/bin/env python3
"""Hierarchical classification cascade — official cookbook
/cookbooks/hierarchical_classification (via systemonemodels.org/recipes →
hierarchical-classification-cascade).

Pattern: walk a taxonomy one level at a time. At each level, the current
node's children are the options of a single Choice question, so the walk costs
one classify() call per LEVEL rather than per node. Each edge multiplies into
a cumulative path probability; depth normalization (the geometric mean)
stops deep paths from automatically losing to shallow ones.

`head.floor` bails out of a subtree: a weak edge stops the walk instead of
producing a confident-looking leaf. Below the floor the answer escalates, and
the walk stops at the last good node — the caller decides what to do with an
incomplete path (ask a human, fall back to a flat classifier).

Probe calls are not logged: the JSONL calibration set is for real gating
decisions, not tree probes.

Usage:
    .venv/bin/python examples/taxonomy-walk.py [--provider decide] < issue.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sys1

TAXONOMY: dict[str, list[str]] = {
    "root": ["billing", "technical", "account"],
    "billing": ["refund", "invoice", "payment-failed"],
    "technical": ["bug", "how-to", "integration"],
    "account": ["login", "permissions", "deletion"],
}

EDGE_FLOOR = 0.6  # weak-edge bail: below this the walk stops at the node


def walk(text: str, provider: str, cfg: dict, node: str = "root", path=None, cum=1.0, depth=0):
    """Descend the taxonomy; return (path, cumulative, depth, stop_reason|None)."""
    path = path or []
    children = TAXONOMY.get(node)
    if not children:
        return path, cum, depth, None
    head = sys1.make_choice(
        f"Which single category best fits this text? Options: {', '.join(children)}.",
        children,
        id="edge",
        floor=EDGE_FLOOR,
    )
    result = sys1.classify(provider, sys1.Task(id=f"walk_{node}", heads=[head]), text, cfg=cfg, log=False)
    answer = result.head("edge", provider=provider) or {}
    label, conf = answer.get("label"), answer.get("confidence")
    if result.verdict == "escalate" or label is None or not isinstance(conf, (int, float)):
        return path, cum, depth, f"weak edge below {node} (conf {conf}) — stopped"
    return walk(
        text, provider, cfg, str(label), [*path, str(label)], cum * float(conf), depth + 1
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk a taxonomy over text, one level per call.")
    ap.add_argument("file", nargs="?", help="text file (default: stdin)")
    ap.add_argument("--provider", default="decide", help="provider id (default: decide)")
    args = ap.parse_args()

    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    path, cum, depth, stop = walk(text, args.provider, sys1.load_config())
    if not path:
        print("no confident path found")
        return 1
    normalized = cum ** (1 / depth)
    print(" > ".join(path) + f"   (cumulative {cum:.3f}, depth {depth}, normalized {normalized:.3f})")
    if stop:
        print(f"note: {stop}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
