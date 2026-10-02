#!/usr/bin/env python3
"""Retroactive plan-surface probe: replay a completed plan and grade the layers.

Runs the two sys1 layers (criterion->surface mapping, pairwise dependency)
over a draft-feature-plan document, then grades the resulting judgment graph
against the repo's actual commit sequence since the plan was created:

  - Kendall tau between the predicted build order and first-touch commit order
  - edge direction precision (predicted prerequisite actually landed first)
  - adjacent-transition coverage (commit-to-commit criterion hops covered by
    a predicted edge)
  - a threshold sweep, so the go/no-go call has numbers under it

Every answered head also lands a feedback row (predicted vs actual) in the
sys1 calibration log, joined by input_sha256 — the first planning-domain
rows in the JSONL.

Usage:
    .venv/bin/python scripts/probe-plan-replay.py ~/Projects/second-brain/docs/plans/2026-10-01-control-tower-foundation.md \
        --repo-root ~/Projects/second-brain
Providers default to the active roster (map: glide,drex — deps: jev); keys
come from the environment the usual sys1 way.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import sys1
from sys1 import plansurface as ps
from sys1.telemetry import record_feedback

DEFAULT_OUT_DIR = Path.home() / ".local/share/sys1/probes"


def _frontmatter_created(plan_text: str) -> str | None:
    block = plan_text.split("---")[1] if plan_text.startswith("---") else ""
    m = re.search(r"^created:\s*(\S+)", block, re.M)
    return m.group(1) if m else None


def render_report(plan_path: Path, repo_root: Path, since: str, artifact: dict,
                  rec: dict, commit_index: dict[str, dict]) -> str:
    L: list[str] = []
    L.append(f"# plan-surface probe: {plan_path.name}")
    L.append("")
    L.append(f"- plan: `{plan_path}`")
    L.append(f"- repo: `{repo_root}`")
    L.append(f"- since: {since}")
    L.append(f"- providers: map={artifact['meta']['provider_map']}, deps={artifact['meta']['provider_deps']}")
    L.append(f"- criteria: {len(artifact['criteria'])}, edges: {len(artifact['edges'])}, "
             f"uncertain: {len(artifact['uncertain'])}, cycle breaks: {len(artifact['cycle_breaks'])}")
    L.append("")
    L.append("## Predicted vs actual order")
    L.append("")
    L.append("| # | predicted | actual |")
    L.append("|---|-----------|--------|")
    for k in range(max(len(artifact["order"]), len(rec["actual_order"]))):
        p = artifact["order"][k] if k < len(artifact["order"]) else ""
        a = rec["actual_order"][k] if k < len(rec["actual_order"]) else ""
        L.append(f"| {k + 1} | {p} | {a} |")
    L.append("")
    L.append(f"Kendall tau: **{rec['tau']}**  (commits used: {rec['commits_used']}, "
             f"skipped by prefix: {rec['commits_skipped_prefix']})")
    L.append("")
    at = rec["at_threshold"]
    L.append(f"At dep threshold {at['threshold']}: direction precision **{at['direction_precision']}**, "
             f"adjacent coverage **{at['adjacent_coverage']}**")
    L.append("")
    L.append("## Threshold sweep")
    L.append("")
    L.append("| threshold | edges | direction precision | adjacent coverage |")
    L.append("|-----------|-------|---------------------|-------------------|")
    for s in rec["sweep"]:
        L.append(f"| {s['threshold']} | {s['n_edges']} | {s['direction_precision']} | {s['adjacent_coverage']} |")
    L.append("")
    L.append("## Dependency edges")
    L.append("")
    for e in artifact["edges"]:
        L.append(f"- {e['before']} -> {e['after']}  p={e['p']}")
    if artifact["uncertain"]:
        L.append("")
        L.append("Uncertain pairs (in band, reported not dropped):")
        for u in artifact["uncertain"]:
            pair = u["pair"]
            L.append("- {} / {}  reading={}  {}".format(pair[0], pair[1], u["reading"], u["probs"]))
    if artifact["cycle_breaks"]:
        L.append("")
        for cb in artifact["cycle_breaks"]:
            d = cb["dropped"]
            L.append(f"- cycle break: dropped {d['before']} -> {d['after']} (p={d['p']})")
    L.append("")
    L.append("## Criteria")
    L.append("")
    for node in artifact["criteria"]:
        mods = ", ".join(f"{m['path']} ({m['p']})" for m in node["modules"]) or "unmapped"
        risks = f" risks: {', '.join(node['risks'])}" if node["risks"] else ""
        L.append(f"- {node['id']}: {node['text']}  ->  {mods}{risks}")
    L.append("")
    L.append("## Commit -> criterion mapping")
    L.append("")
    L.append("| sha | message | criterion | source | files |")
    L.append("|-----|---------|-----------|--------|-------|")
    for c in rec["mapping"]:
        full = commit_index.get(c["sha"], {})
        files = ", ".join(full.get("files", [])[:4])
        L.append(f"| {c['sha']} | {c['message'][:60]} | {c['criterion']} | {c.get('source', '')} | {files} |")
    if rec["criteria_unimplemented"]:
        L.append("")
        L.append(f"No commits mapped: {', '.join(rec['criteria_unimplemented'])}")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a plan through the plan-surface layers and grade vs git.")
    ap.add_argument("plan", help="path to the draft-feature-plan markdown file")
    ap.add_argument("--repo-root", required=True, help="repository the plan was implemented in")
    ap.add_argument("--map-provider", default="glide,drex")
    ap.add_argument("--deps-provider", default="jev")
    ap.add_argument("--since", default=None, help="git --since for ground-truth commits (default: plan frontmatter created)")
    ap.add_argument("--max-commits", type=int, default=400)
    ap.add_argument("--map-floor", type=float, default=ps.MAP_FLOOR)
    ap.add_argument("--map-top", type=int, default=ps.MAP_TOP)
    ap.add_argument("--dep-threshold", type=float, default=ps.DEP_THRESHOLD)
    ap.add_argument("--attribute-provider", default="jev",
                    help="commit -> criterion attribution layer ('' disables)")
    ap.add_argument("--band", type=float, default=ps.DEP_BAND)
    ap.add_argument("--out", default=None, help="output directory (default: ~/.local/share/sys1/probes)")
    args = ap.parse_args()

    plan_path = Path(args.plan).expanduser()
    repo_root = Path(args.repo_root).expanduser()
    if not plan_path.exists():
        print(f"error: plan not found: {plan_path}", file=sys.stderr)
        return 1
    cfg = sys1.load_config()

    plan_text = plan_path.read_text()
    criteria = ps.parse_criteria(plan_text)
    if not criteria:
        print("error: no checkbox criteria found in plan", file=sys.stderr)
        return 1
    created = _frontmatter_created(plan_text)
    # frontmatter `created` can post-date the first implementation commit
    # (same-night work, date bumped on later edits), so default the window to
    # created minus one day and let --since override.
    default_since = None
    if created:
        try:
            d = datetime.fromisoformat(created)
            default_since = (d.replace(hour=0, minute=0, second=0) - timedelta(days=1)).date().isoformat()
        except ValueError:
            pass
    since = args.since or default_since or "1970-01-01"

    print(f"[probe] {len(criteria)} criteria, inventorying {repo_root} ...")
    modules = ps.inventory_surface(repo_root)
    print(f"[probe] {len(modules)} inventory entries; layer 1 (map) via {args.map_provider} ...")
    map_res = ps.run_map(criteria, modules, provider=args.map_provider, cfg=cfg)

    print(f"[probe] layer 2 (pairwise deps) via {args.deps_provider} ...")
    dep_res = ps.run_deps(criteria, provider=args.deps_provider, cfg=cfg)

    artifact = ps.assemble(map_res, dep_res, criteria, modules,
                           map_floor=args.map_floor, map_top=args.map_top,
                           dep_threshold=args.dep_threshold, band=args.band)

    commits = ps.load_commits(repo_root, since=since, max_commits=args.max_commits)
    attributed: dict[str, str] = {}
    if args.attribute_provider and commits:
        print(f"[probe] attribution layer ({args.attribute_provider}) over {len(commits)} commits ...")
        attr_res = ps.run_attribution(commits, criteria, provider=args.attribute_provider, cfg=cfg)
        for k, c in enumerate(commits):
            label = (attr_res["answers"].get(f"a{k}_crit") or {}).get("label")
            if label and label != "none":
                attributed[c["sha"]] = label
    graded = ps.grade_and_record(artifact, commits, map_res, dep_res, attributed=attributed or None, cfg=cfg)
    rec, n_feedback = graded["reconcile"], graded["feedback_rows"]
    commit_index = {c["sha"]: c for c in commits}

    out_dir = Path(args.out).expanduser() if args.out else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = out_dir / f"{plan_path.stem}-{stamp}.md"
    artifact_path = out_dir / f"{plan_path.stem}-{stamp}.surface.json"
    report_path.write_text(render_report(plan_path, repo_root, since, artifact, rec, commit_index))
    artifact_path.write_text(json.dumps(artifact, indent=2))

    at = rec["at_threshold"]
    print(f"[probe] providers: map={artifact['meta']['provider_map']} deps={artifact['meta']['provider_deps']}")
    print(f"[probe] predicted order: {' -> '.join(artifact['order']) or '(none)'}")
    print(f"[probe] actual order:    {' -> '.join(rec['actual_order']) or '(none)'}")
    print(f"[probe] tau={rec['tau']}  direction_precision={at['direction_precision']}  "
          f"adjacent_coverage={at['adjacent_coverage']}  feedback_rows={n_feedback}")
    print(f"[probe] report: {report_path}")
    print(f"[probe] artifact: {artifact_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
