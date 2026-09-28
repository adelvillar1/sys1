#!/usr/bin/env python3
"""Semantic find — official cookbook /cookbooks/semantic_find.

Pattern: rank lines with a Choice question over line IDs (L001, L002, ...)
and, in the SAME request, ask an existence Noul. Ranking tells you where to
look; the noul tells you whether the result actually answers the query —
the cookbook's example scores a top line 0.86 relevant with only 0.14
existence, correctly flagged as absent.

Provider-swappable: the same recipe runs on jev, decide, or the local
Decide wire. A Choice accepts up to 255 options per request, so documents
are capped at 255 lines — beyond that the cookbook narrows in two passes
(not implemented here; this script exits with a hint instead).

The FOUND/ABSENT thresholds are the cookbook's starting points — fit your
own from the JSONL calibration stream.

Usage:
    .venv/bin/python examples/semantic-find.py [--provider decide] --query "termination clause" doc.txt
    .venv/bin/python examples/semantic-find.py --provider local --query "refund window" < doc.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sys1

# noul-existence thresholds (cookbook defaults — tune on your own data)
FOUND = 0.7    # >= FOUND -> answered in this document
ABSENT = 0.35  # <= ABSENT -> not in this document; between -> partially addressed
TOP_K = 5
MAX_LINES = 255  # vendor Choice ceiling; two-stage narrowing beyond this


def build_task(n_lines: int, query: str) -> sys1.Task:
    """One Choice head over the line IDs plus one existence Noul, in ONE call."""
    line_ids = [f"L{i + 1:03d}" for i in range(n_lines)]
    return sys1.Task(
        id="semantic_find",
        description="Find the lines that answer a query, and whether any line does.",
        heads=[
            sys1.make_choice(
                f'Which line of the document contains the answer to: "{query}"?',
                line_ids,
                id="where",
            ),
            # The query MUST ride in the existence noul's instructions too —
            # the cookbook's exists_question(query) parameterizes it; a
            # query-blind noul is noise (verified live 2026-09-28).
            sys1.make_noul(
                f'Does any line of the document state or directly imply the answer to: "{query}"?',
                id="exists",
            ),
        ],
    )


def _existence(answer: dict | None) -> tuple[float | None, str]:
    """(existence signal, verdict) from the exists answer.

    Two provider shapes arrive here: probabilistic nouls (Jev, local — a 0-1
    probability of "true", thresholded at FOUND/ABSENT) and Decide-shaped
    boolean nouls (hosted decide returns noul true/false plus a confidence;
    the boolean IS the verdict and its confidence is the certainty).
    """
    if not isinstance(answer, dict):
        return None, "unknown"
    noul = answer.get("noul")
    conf = answer.get("confidence")
    if isinstance(noul, bool):
        verdict = "answered" if noul else "absent"
        signal = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
        return signal, verdict
    if isinstance(noul, (int, float)):
        p = float(noul)
        if p >= FOUND:
            return p, "answered"
        if p <= ABSENT:
            return p, "absent"
        return p, "partial"
    return None, "unknown"


def find(query: str, doc_lines: list[str], provider: str, cfg: dict | None = None) -> dict:
    """One classify per query; return ranked lines plus the existence verdict."""
    if len(doc_lines) > MAX_LINES:
        raise SystemExit(
            f"{len(doc_lines)} lines exceeds the {MAX_LINES}-option Choice ceiling — "
            "narrow with a coarse pass first (two-stage narrowing not implemented here)"
        )
    cfg = cfg or sys1.load_config()
    tagged = "\n".join(f"L{i + 1:03d} {line}" for i, line in enumerate(doc_lines))
    task = build_task(len(doc_lines), query)
    result = sys1.classify(provider, task, tagged, cfg=cfg, log=False)

    where = result.head("where", provider=provider) or {}
    probs = where.get("probabilities") or {}
    ranked = sorted(
        ((int(str(k)[1:]), float(v)) for k, v in probs.items() if str(k).startswith("L")),
        key=lambda t: t[1],
        reverse=True,
    )
    # hosted decide may DECLINE a large flat choice (null answer) — ranking
    # is then empty and the existence verdict is what the caller gets.
    exists_signal, verdict = _existence(result.head("exists", provider=provider))

    return {"exists": exists_signal, "verdict": verdict, "ranked": ranked[:TOP_K], "lines": doc_lines}


def main() -> int:
    ap = argparse.ArgumentParser(description="Line-level semantic search over a document.")
    ap.add_argument("file", nargs="?", help="document file (default: stdin)")
    ap.add_argument("--query", required=True, help="what to look for")
    ap.add_argument("--provider", default="decide", help="provider id (default: decide)")
    args = ap.parse_args()

    doc = Path(args.file).read_text() if args.file else sys.stdin.read()
    out = find(args.query, doc.splitlines(), args.provider)

    label = {"answered": "answered in this document", "partial": "partially addressed",
             "absent": "not in this document", "unknown": "existence unknown"}[out["verdict"]]
    exists = "n/a" if out["exists"] is None else f"{out['exists']:.2f}"
    print(f"exists={exists} -> {label}")
    if out["verdict"] == "absent":
        return 0
    for line_no, score in out["ranked"]:
        preview = out["lines"][line_no - 1][:70]
        print(f"  L{line_no:03d}  {score:.2f}  {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
