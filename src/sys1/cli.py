"""
Minimal CLI companion to the sys1 library.

Only what the library itself owns: classify a string from a file or stdin,
print a health report, and show the version. Anything repo-aware (git hooks,
staged diff detection, fleet scans) stays in `dev-decisions`, which imports
this library — keeping sys1 a clean, app-agnostic dependency.
"""

from __future__ import annotations

import argparse
import json
import sys as _sys

from . import __version__, classify, health_report, list_tasks


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sys1", description="Reusable decision-model abstraction.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="Run one or more providers against an input text.")
    c.add_argument("--provider", default="decide", help="provider id, 'both', 'all', or comma list")
    c.add_argument("--task", default="change", help="registered task name")
    c.add_argument("--input", "-i", default="-", help="path to text file, or '-' for stdin")
    c.add_argument("--no-log", action="store_true", help="skip the JSONL calibration log")
    c.add_argument("--json", "-j", action="store_true", help="emit raw JSON instead of human summary")
    c.set_defaults(func=_cmd_classify)

    d = sub.add_parser("doctor", help="Provider availability snapshot.")
    d.add_argument("--json", "-j", action="store_true", help="emit JSON")
    d.set_defaults(func=_cmd_doctor)

    t = sub.add_parser("tasks", help="List registered task names.")
    t.set_defaults(func=_cmd_tasks)

    pr = sub.add_parser("providers", help="List every provider with its flags (core/candidate, enabled, configured).")
    pr.add_argument("--json", "-j", action="store_true", help="emit JSON")
    pr.add_argument("--all", action="store_true", help="include disabled candidates")
    pr.set_defaults(func=_cmd_providers)

    v = sub.add_parser("version", help="Print version and exit.")
    v.set_defaults(func=lambda _a: print(__version__) or 0)

    return p


def _cmd_classify(args: argparse.Namespace) -> int:
    if args.input == "-":
        text = _sys.stdin.read()
    else:
        text = open(args.input, encoding="utf-8", errors="replace").read()
    result = classify(args.provider, args.task, text, log=not args.no_log)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, default=str))
    else:
        print(f"task: {result.task}  providers: {','.join(result.providers_used) or '(none)'}")
        print(f"verdict: {result.verdict}  escalated: {result.escalated}  latency: {result.latency_ms}ms")
        if result.summary:
            print("\n".join(result.summary))
    return 0 if result.verdict == "pass" else (2 if result.verdict == "block" else 1)


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = health_report()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for pid, info in sorted(report.items()):
        tick = "✓" if info["available"] else "✗"
        flag = "" if info["enabled"] else "  (disabled)"
        print(f"  {tick} {pid}: {info['name']} — {info['reason']}{flag}")
    return 0


def _cmd_providers(args: argparse.Namespace) -> int:
    report = health_report()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    groups = {"core": [], "candidate": []}
    for pid, info in report.items():
        groups.setdefault(info["tier"], groups["candidate"]).append((pid, info))
    for tier, rows in groups.items():
        if tier != "core" and not args.all:
            continue
        rows.sort()
        print(f"{tier}:")
        for pid, info in rows:
            en = "on " if info["enabled"] else "off"
            cfg_ = "configured" if info["configured"] else "not configured"
            state = "available" if info["available"] else info["reason"]
            print(f"  [{en}] {pid:12} {info['wire']:10} {cfg_:16} → {state}")
    if not args.all:
        print("\n(candidate providers hidden — pass --all to list them)")
    return 0


def _cmd_tasks(args: argparse.Namespace) -> int:
    for name in list_tasks():
        print(name)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
