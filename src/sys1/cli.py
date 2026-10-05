"""
Minimal CLI companion to the sys1 library.

Only what the library itself owns: classify a string from a file or stdin
(optionally alongside image files via --images, vision-capable providers
only), print a health report, and show the version. Anything repo-aware (git hooks,
staged diff detection, fleet scans) stays in `dev-decisions`, which imports
this library — keeping sys1 a clean, app-agnostic dependency.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys as _sys
from pathlib import Path

from . import __version__, classify, health_report, list_tasks

# d1 (liquid) wire limits: PNG/JPEG/WebP/GIF only, ≤5MB per image, ≤8 images
# per request, remote URLs rejected — so the CLI rejects them too, and fails
# before any request is made.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 8


def _sniff_image(data: bytes) -> str | None:
    """Content type from magic bytes — d1 accepts exactly these four."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_images(paths: list[str]) -> list[str]:
    """Read image files into data URLs for the wire. Raises FileNotFoundError /
    ValueError with a clear message before anything is sent."""
    if len(paths) > MAX_IMAGES:
        raise ValueError(f"at most {MAX_IMAGES} images per request (got {len(paths)})")
    urls: list[str] = []
    for p in paths:
        data = Path(p).read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"{p}: {len(data):,} bytes exceeds the 5MB per-image cap")
        ctype = _sniff_image(data)
        if ctype is None:
            raise ValueError(f"{p}: not a PNG/JPEG/WebP/GIF image")
        urls.append(f"data:{ctype};base64,{base64.b64encode(data).decode()}")
    return urls


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sys1", description="Reusable decision-model abstraction.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="Run one or more providers against an input text.")
    c.add_argument("--provider", default="decide", help="provider id, 'both', 'all', or comma list")
    c.add_argument("--task", default="change", help="registered task name")
    c.add_argument("--input", "-i", default="-", help="path to text file, or '-' for stdin")
    c.add_argument("--no-log", action="store_true", help="skip the JSONL calibration log")
    c.add_argument("--json", "-j", action="store_true", help="emit raw JSON instead of human summary")
    c.add_argument(
        "--images",
        nargs="+",
        default=None,
        metavar="PATH",
        help="image files sent alongside the text to vision-capable providers "
        "(liquid/d1 only; text-only wires ignore them). PNG/JPEG/WebP/GIF, "
        "≤5MB each, ≤8 per request",
    )
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
        with open(args.input, encoding="utf-8", errors="replace") as f:
            text = f.read()
    images = None
    if args.images:
        try:
            images = load_images(args.images)
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=_sys.stderr)
            return 2
    result = classify(args.provider, args.task, text, log=not args.no_log, images=images)
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
