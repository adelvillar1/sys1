#!/usr/bin/env python3
"""Passage re-ranking — official cookbook /cookbooks/rerank_typesafe (via
systemonemodels.org/recipes → rerank-passages-score).

Pattern: generate one Score head per candidate passage and fan them all out
in a single classify() call, then sort and cut off in your code. Score heads
scale to dozens of candidates in one request — the marginal cost is one
request, not N.

Return NOTHING when the best passage is weak: an empty context beats a wrong
one, and the caller decides what to do about it.

Usage:
    .venv/bin/python examples/rerank-passages.py [--provider decide] [--top 3] \
        < passages.json
where passages.json is {"query": "...", "passages": ["...", "..."]}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sys1

SCALE_MIN, SCALE_MAX = 1, 5  # 1 = irrelevant, 5 = directly answers
WEAK_CUTOFF = 3.0  # below this, no passage is good enough to return


def build_task(n: int) -> sys1.Task:
    return sys1.Task(
        id="rerank",
        description="Rate each candidate passage for relevance to the query.",
        heads=[
            sys1.make_score(
                "How relevant is this passage to the query? 1 = irrelevant, 5 = directly answers it.",
                score_min=SCALE_MIN,
                score_max=SCALE_MAX,
                id=f"p{i}",
            )
            for i in range(n)
        ],
    )


def rerank(query: str, passages: list[str], provider: str = "decide", cfg: dict | None = None):
    """Return [(index, score)] above the cutoff, best first (may be empty)."""
    cfg = cfg or sys1.load_config()
    task = build_task(len(passages))
    state = f"QUERY: {query}\n\n" + "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages))
    result = sys1.classify(provider, task, state, cfg=cfg, log=False)
    scored: list[tuple[int, float]] = []
    for i in range(len(passages)):
        answer = result.head(f"p{i}", provider=provider) or {}
        raw = answer.get("score")
        if isinstance(raw, (int, float)):
            scored.append((i, float(raw)))
    ranked = sorted((s for s in scored if s[1] >= WEAK_CUTOFF), key=lambda s: s[1], reverse=True)
    return ranked


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-rank passages against a query.")
    ap.add_argument("file", nargs="?", help="passages JSON file (default: stdin)")
    ap.add_argument("--provider", default="decide", help="provider id (default: decide)")
    ap.add_argument("--top", type=int, default=3, help="how many to return (default: 3)")
    args = ap.parse_args()

    blob = json.loads(Path(args.file).read_text() if args.file else sys.stdin.read())
    query, passages = blob["query"], list(blob["passages"])
    ranked = rerank(query, passages, provider=args.provider)
    if not ranked:
        print(f"no passage scored >= {WEAK_CUTOFF} — returning nothing")
        return 0
    for i, score in ranked[: args.top]:
        print(f"  [{i}] {score:.1f}  {passages[i][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
