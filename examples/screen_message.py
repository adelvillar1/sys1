#!/usr/bin/env python3
"""Guardrail screening: the policy half of the official TypeSafe cookbook
/cookbooks/llm_guardrails.

guardrail-injection.toml holds the question half (five hazard Nouls plus a
severity Score, fanned out in one call). This script holds the thresholds —
the model returns labels and numbers, the policy stays in your code:

    review  — any hazard probability >= REVIEW_THRESHOLD (0.35)
    action  — any hazard probability >= ACTION_THRESHOLD (0.70)
    block   — severity score >= SEVERITY_BLOCK (2.0)

Jev noul answers carry no confidence field; the 0-1 output is the probability
of "true" (hazard present). Decide-style providers answer yes/no with a
confidence — for those, the hazard probability is that confidence when the
answer is yes. Head floors in the TOML gate model *uncertainty*, a different
axis from these hazard thresholds.

The thresholds are the cookbook's starting points, not tuned values — fit
them from your own JSONL calibration log (sys1.record_feedback +
sys1.calibration_curve) before trusting them in production.

Fail closed: a classify error, an unknown hazard answer, or a provider outage
exits BLOCK — a guardrail that passes when it cannot see is not a guardrail.

Usage:
    .venv/bin/python examples/screen_message.py [--provider decide] [message.txt]
    echo "ignore previous instructions" | .venv/bin/python examples/screen_message.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sys1

TASK_ID = "guardrail_injection"
TASK_FILE = Path(__file__).with_name("guardrail-injection.toml")
HAZARDS = (
    "jailbreak",
    "prompt_injection",
    "secret_exfiltration",
    "denylist_pressure",
    "harmful_request",
)
SEVERITY_HEAD = "severity"

# two-tier policy (cookbook defaults — fit from your own calibration data)
REVIEW_THRESHOLD = 0.35
ACTION_THRESHOLD = 0.70
SEVERITY_BLOCK = 2.0

PASS, REVIEW, BLOCK = 0, 1, 2


def _hazard_probability(answer: dict | None) -> float | None:
    """Probability that the hazard is present, or None when unknowable."""
    if not isinstance(answer, dict):
        return None
    noul = answer.get("noul")
    conf = answer.get("confidence")
    if isinstance(noul, bool):
        if not noul:
            return 0.0
        return float(conf) if isinstance(conf, (int, float)) else None
    if isinstance(noul, (int, float)):
        return float(noul)
    return None


def screen(
    text: str, provider: str = "decide", cfg: dict | None = None, *, log: bool = True
) -> tuple[int, list[str]]:
    """Screen `text`; return (exit_code, report lines). Never raises."""
    sys1.load_tasks_file(TASK_FILE)
    cfg = cfg or sys1.load_config()
    try:
        result = sys1.classify(provider, TASK_ID, text, cfg=cfg, log=log)
    except Exception as e:
        return BLOCK, [f"classify failed: {e} — failing closed"]

    if result.verdict == "error":
        return BLOCK, [f"no provider succeeded ({result.reason}) — failing closed"]

    lines: list[str] = []
    review = action = False
    for hid in HAZARDS:
        p = _hazard_probability(result.head(hid, provider=provider))
        if p is None:
            lines.append(f"  {hid}: UNKNOWN — failing closed")
            return BLOCK, lines
        if p >= ACTION_THRESHOLD:
            action = True
            tier = "action"
        elif p >= REVIEW_THRESHOLD:
            review = True
            tier = "review"
        else:
            tier = "-"
        lines.append(f"  {hid:<22} p={p:.2f}  {tier}")

    sev_answer = result.head(SEVERITY_HEAD, provider=provider)
    severity = sev_answer.get("score") if isinstance(sev_answer, dict) else None
    if isinstance(severity, (int, float)):
        lines.append(f"  {SEVERITY_HEAD:<22} {severity}")
        if float(severity) >= SEVERITY_BLOCK:
            lines.append(f"  -> severity >= {SEVERITY_BLOCK}: BLOCK")
            return BLOCK, lines

    if action:
        lines.append(f"  -> hazard >= {ACTION_THRESHOLD}: BLOCK")
        return BLOCK, lines
    if review:
        lines.append(f"  -> hazard >= {REVIEW_THRESHOLD}: REVIEW before replying")
        return REVIEW, lines
    lines.append("  -> pass")
    return PASS, lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Screen a message for prompt-injection hazards.")
    ap.add_argument("file", nargs="?", help="message file (default: stdin)")
    ap.add_argument("--provider", default="decide", help="provider id (default: decide)")
    args = ap.parse_args()

    text = Path(args.file).read_text() if args.file else sys.stdin.read()

    code, lines = screen(text, provider=args.provider)
    print(f"guardrail screen ({args.provider}): " + {PASS: "pass", REVIEW: "review", BLOCK: "block"}[code])
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
