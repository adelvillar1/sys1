#!/usr/bin/env python3
"""Pair-calibration probe for the vision_qc task (phase 2 of
docs/plans/2026-10-05-vision-qc-task.md) — a probe, not a service feature.

For every asset in a corpus manifest, run the same four head questions two
ways:

  (a) pixels path — `classify("liquid", "vision_qc", brief, images=[…])`:
      d1 reads the rendered artifact directly.
  (b) mediated path (the today-chain, reproduced) — `classify(["glide","drex","jev"], …)`
      over the brief plus a *blind text description* of the asset (written by
      a vision describer that is not in the sys1 pool, and never shown the
      brief or the ground-truth manifest). The text family judges the
      paraphrase, the way the image->description->sys1 judge workflow does
      today.

Per head, the probe records the pixels answer, each mediated answer, the
mediated majority, agreement (pixels vs majority), and the hand-annotated
ground truth from the manifest. The question the output answers: does
d1-on-pixels catch describer confabulation?

Corpus manifest (JSON): {"assets": [{"id", "image", "description", "brief",
"expect": {head_id: value}}, ...]}, paths relative to --root (default: the
manifest's directory). Image bytes stay on disk (gitignored data/); the probe
commits only this script and the recorded Outcome table.

Usage:
    .venv/bin/python scripts/vision_probe.py \
        --manifest data/vision_probe/manifest.json \
        --out data/vision_probe/agreement.md \
        --json-out data/vision_probe/agreement.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import sys1  # noqa: E402
from sys1 import Task, classify, get_task  # noqa: E402
from sys1.cli import load_images  # noqa: E402
from sys1.gating import answer_confidence  # noqa: E402

TEXT_FAMILY = ["glide", "drex", "jev"]  # today's default hosted chain


# ── answer normalization ──────────────────────────────────────────────────────


def decide(entry: dict | None) -> tuple[str, float | None]:
    """One answer -> (decision string, floor-comparable confidence).

    Noul heads answer true/false from the probability of "true" (>= 0.5);
    choice heads answer with the label. Missing/declined answers are "-" —
    the gate treats those as escalation, the probe as no evidence.
    """
    if not entry:
        return "-", None
    p = entry.get("noul")
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        return ("true" if float(p) >= 0.5 else "false"), answer_confidence(entry)
    label = entry.get("label")
    if label is None:
        return "-", None
    return str(label), answer_confidence(entry)


def majority(decisions: list[str]) -> str:
    """Majority of mediated decisions; "split" when no strict winner."""
    live = [d for d in decisions if d != "-"]
    if not live:
        return "-"
    counts = {d: live.count(d) for d in set(live)}
    best = max(counts.values())
    winners = [d for d, c in counts.items() if c == best]
    return winners[0] if len(winners) == 1 else "split"


def expect_norm(v) -> str:
    """Manifest ground truth -> decision string. None = unannotated."""
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ── the two paths ─────────────────────────────────────────────────────────────


def pixels_row(brief: str, image_path: Path, cfg: dict) -> tuple[dict, dict]:
    """(a) d1 reads the artifact. Returns ({head: (decision, conf)}, telemetry)."""
    task = get_task("vision_qc")
    result = classify(
        "liquid", task, brief, cfg,
        images=load_images([str(image_path)]),
        extra_log_fields={"probe": "vision_qc_pixels"},
    )
    answers = result.answers.get("liquid", {})
    row = {h.id: decide(answers.get(h.id)) for h in task.heads}
    tel = result.telemetry.get("liquid", {})
    if not answers:
        row = {h.id: ("-", None) for h in task.heads}
        tel = {**tel, "error": tel.get("error", result.reason)}
    return row, {**tel, "verdict": result.verdict, "latency_ms": result.latency_ms}


def mediated_row(brief: str, description: str, cfg: dict) -> tuple[dict, dict]:
    """(b) the text family judges the description. Returns per-provider rows."""
    src = get_task("vision_qc")
    task = Task(
        id="vision_qc_mediated",
        description=src.description + " (description-mediated probe copy)",
        heads=src.heads,  # same four questions; no providers allow-list
    )
    text = (
        "You are judging a rendered image asset you cannot see. Below is the "
        "written brief it was made against, then a vision describer's account "
        "of what the rendered asset actually shows. Answer each question about "
        "the rendered asset strictly from the description; when the "
        "description does not settle a question, decline rather than guess "
        "from the brief's intent.\n\n"
        f"BRIEF:\n{brief}\n\n"
        f"DESCRIPTION OF THE RENDERED ASSET:\n{description}"
    )
    result = classify(
        TEXT_FAMILY, task, text, cfg,
        extra_log_fields={"probe": "vision_qc_mediated"},
    )
    rows = {}
    for pid in TEXT_FAMILY:
        answers = result.answers.get(pid, {})
        rows[pid] = {h.id: decide(answers.get(h.id)) for h in src.heads}
    return rows, {"verdict": result.verdict, "providers_used": result.providers_used}


# ── report ────────────────────────────────────────────────────────────────────


def fmt(cell: tuple[str, float | None]) -> str:
    d, c = cell
    return f"{d} ({c:.2f})" if c is not None else d


def run(manifest_path: Path, root: Path, cfg: dict) -> list[dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    heads = [h.id for h in get_task("vision_qc").heads]
    records = []
    for asset in manifest["assets"]:
        aid = asset["id"]
        print(f"[probe] {aid}", file=sys.stderr)
        brief = (root / asset["brief"]).read_text(encoding="utf-8")
        description = (root / asset["description"]).read_text(encoding="utf-8")
        try:
            pix, pix_tel = pixels_row(brief, root / asset["image"], cfg)
        except Exception as e:  # one asset must not sink the table
            pix, pix_tel = {h: ("-", None) for h in heads}, {"error": str(e)[:200]}
        try:
            med, med_info = mediated_row(brief, description, cfg)
        except Exception as e:  # same policy for the mediated path
            med, med_info = {p: {h: ("-", None) for h in heads} for p in TEXT_FAMILY}, {"error": str(e)[:200]}
        per_head = {}
        for h in heads:
            decisions = [med[p][h] for p in TEXT_FAMILY]
            maj = majority([d[0] for d in decisions])
            expect = expect_norm(asset.get("expect", {}).get(h))
            per_head[h] = {
                "pixels": list(pix[h]),
                "mediated": {p: list(d) for p, d in zip(TEXT_FAMILY, decisions, strict=True)},
                "majority": maj,
                "agree": pix[h][0] == maj if pix[h][0] != "-" and maj != "-" else None,
                "expect": expect,
                "pixels_vs_expect": (
                    ("ok" if pix[h][0] == expect else "miss")
                    if expect not in ("-",) and pix[h][0] != "-"
                    else None
                ),
                "majority_vs_expect": (
                    ("ok" if maj == expect else "miss")
                    if expect not in ("-",) and maj != "-"
                    else None
                ),
            }
        records.append(
            {"asset": aid, "blind": asset.get("blind"), "heads": per_head,
             "pixels_telemetry": pix_tel, "mediated_info": med_info}
        )
    return records


def render_md(records: list[dict], heads: list[str]) -> str:
    lines = ["# vision_qc pair-probe: pixels (d1) vs description-mediated (glide,drex,jev)", ""]
    lines.append("Cells are decision (confidence). `agree` = pixels vs mediated majority. `vs expect` = hand-annotated ground truth.\n")
    for rec in records:
        lines.append(f"## {rec['asset']}")
        if rec.get("pixels_telemetry", {}).get("error"):
            lines.append(f"_pixels path error: {rec['pixels_telemetry']['error']}_")
        if rec.get("mediated_info", {}).get("error"):
            lines.append(f"_mediated path error: {rec['mediated_info']['error']}_")
        lines.append("")
        lines.append("| head | pixels (d1) | " + " | ".join(TEXT_FAMILY) + " | majority | agree | expect | d1 vs gt | med vs gt |")
        lines.append("|---" * (6 + len(TEXT_FAMILY)) + "|")
        for h in heads:
            r = rec["heads"][h]
            row = [h, fmt(tuple(r["pixels"]))]
            row += [fmt(tuple(r["mediated"][p])) for p in TEXT_FAMILY]
            row += [
                r["majority"],
                "-" if r["agree"] is None else ("Y" if r["agree"] else "N"),
                r["expect"],
                r["pixels_vs_expect"] or "-",
                r["majority_vs_expect"] or "-",
            ]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    # agreement roll-up per head
    lines.append("## agreement roll-up")
    lines.append("")
    lines.append("| head | assets | pixels=mediated | d1 vs gt | med vs gt |")
    lines.append("|---|---|---|---|---|")
    for h in heads:
        rs = [rec["heads"][h] for rec in records]
        pairs = [r for r in rs if r["agree"] is not None]
        gt_p = [r for r in rs if r["pixels_vs_expect"] is not None]
        gt_m = [r for r in rs if r["majority_vs_expect"] is not None]
        lines.append(
            f"| {h} | {len(rs)} | "
            f"{sum(1 for r in pairs if r['agree'])}/{len(pairs)} | "
            f"{sum(1 for r in gt_p if r['pixels_vs_expect'] == 'ok')}/{len(gt_p)} | "
            f"{sum(1 for r in gt_m if r['majority_vs_expect'] == 'ok')}/{len(gt_m)} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--root", type=Path, default=None, help="corpus root (default: manifest dir)")
    ap.add_argument("--out", type=Path, default=None, help="write the markdown table here")
    ap.add_argument("--json-out", type=Path, default=None, help="write raw records JSON here")
    args = ap.parse_args(argv)

    root = args.root or args.manifest.parent
    cfg = sys1.load_config()  # every run lands in the JSONL log (image_count, histogram)

    records = run(args.manifest, root, cfg)
    heads = [h.id for h in get_task("vision_qc").heads]
    md = render_md(records, heads)
    print(md)
    if args.out:
        args.out.write_text(md, encoding="utf-8")
    if args.json_out:
        args.json_out.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
