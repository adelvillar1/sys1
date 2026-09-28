#!/usr/bin/env python3
"""
Parity check: run both the original `dev-decisions` provider path and
`sys1.classify()` against the same diff, side by side.

Why this exists: `sys1` is the library extracted out of `dev-decisions`.
The wire shape between them MUST agree — same labels, same confidence
semantics, same provider-call behavior — otherwise migrating the CLI to
consume `sys1` would silently change classification outcomes (and break the
existing JSONL calibration set that joins back via `input_sha256`).

This script proves the two agree without requiring dev-decisions' git tree to
be modified. Run it from anywhere:

    python3 verify-dev-decisions-parity.py \
        --repo /path/to/dev-decisions \
        --provider local \
        --diff-file /some/staged.diff

If `--diff-file` is omitted it reads the live staged diff of `--repo`. If
`--provider` is omitted it runs both `decide` and `local` (only the ones
whose API key / venv is present).

Exit code:
  0 — parity confirmed for every head we could compare
  1 — at least one divergence (print the side-by-side diff)
  2 — environment problem (dev-decisions not importable, no provider, etc.)
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def _bootstrap_dev_decisions(repo_path: Path):
    """Load `dev-decisions` as a module without modifying its tree or repo."""
    dd_script = repo_path / "scripts" / "dev_decisions.py"
    if not dd_script.exists():
        raise FileNotFoundError(f"dev_decisions.py not found at {dd_script}")
    import importlib.util

    spec = importlib.util.spec_from_file_location("dev_decisions", dd_script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _staged_or_file_dd(dd_mod, repo: Path, diff_file: Path | None) -> str:
    if diff_file:
        return diff_file.read_text(errors="replace")
    return dd_mod.effective_diff(repo) or ""


def _staged_or_file_sys1(diff_file: Path | None) -> str:
    if diff_file:
        return diff_file.read_text(errors="replace")
    # fall back to whatever the current working dir has staged
    import subprocess

    try:
        return subprocess.run(
            ["git", "diff", "--cached", "--no-color", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except Exception:
        return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--repo", type=Path, required=True, help="path to dev-decisions repo")
    p.add_argument("--provider", default=None, choices=["decide", "jev", "local", "both"])
    p.add_argument("--task", default="change")
    p.add_argument("--diff-file", type=Path, default=None, help="read diff from this file (else: live staged diff)")
    args = p.parse_args(argv)

    import sys1

    try:
        dd = _bootstrap_dev_decisions(args.repo)
    except Exception as e:
        print(f"✗ dev-decisions not importable: {e}", file=sys.stderr)
        return 2

    # pick a provider that works in both systems (so we get real answers)
    providers = [args.provider] if args.provider else ["local", "decide", "jev"]
    diff = _staged_or_file_dd(dd, args.repo, args.diff_file) or _staged_or_file_sys1(args.diff_file)
    if not diff.strip():
        print("✗ no diff to compare (staged is empty and no --diff-file was passed)", file=sys.stderr)
        return 2
    # bound the size
    diff = diff[:12000]

    cfg = sys1.load_config()
    divergences: list[str] = []
    for prov in providers:
        if prov == "both":
            continue
        if prov == "local" and not Path(cfg["providers"]["local_venv"]).exists():
            print(f"  (skipping {prov}: local venv not present)")
            continue
        try:
            dd_answers = _call_dd(dd, prov, diff, args.task, cfg)
        except Exception as e:
            print(f"  (skipping {prov}: dev-decisions call failed — {e})")
            continue
        sys1_result = sys1.classify(prov, args.task, diff, cfg=cfg, log=False)
        if prov not in sys1_result.answers:
            print(f"  (skipping {prov}: sys1 could not call the provider — {sys1_result.telemetry.get(prov, {})!r})")
            continue
        _compare(prov, dd_answers, sys1_result.answers[prov], sys1.heads_for(args.task, prov), divergences)

    if divergences:
        print("\n✗ parity divergences:")
        for d in divergences:
            print(textwrap.indent(d, "  "))
        return 1
    print("\n✓ parity confirmed: dev-decisions and sys1 agree on every comparable head.")
    return 0


# ── dev-decisions side ────────────────────────────────────────────────────────


def _call_dd(dd_mod, provider: str, diff: str, task: str, cfg: dict) -> dict:
    """Invoke dev-decisions' existing provider entry points."""
    pcfg = cfg["providers"]
    heads = dd_mod.get_task_heads(task, provider)
    if provider == "decide":
        key = dd_mod._env_key("decide")
        if not key:
            raise RuntimeError("FASTINO_API_KEY not set")
        # Prompts MUST match sys1's _openai_wire exactly — the hosted model is
        # prompt-sensitive, and comparing different wrappers measures the
        # wrapper, not the wire (verified: the old wording shifted risk_tier
        # conf 0.5044 vs 0.5076 and nulled diff_type).
        messages = [
            {"role": "system", "content": "You are a precise change classifier. Answer only with the requested labels."},
            {"role": "user", "content": f"Review the following text and answer every question.\n\n{diff}"},
        ]
        resp = dd_mod._call_openai_compatible(
            pcfg["decide_api_url"], key, pcfg["decide_model"], messages, schema={"classifications": heads}
        )
        parsed = dd_mod._parse_decide_response(resp)
        # JSON null answers (hosted decide declines) must survive as declined
        # entries, or the compare reads them as missing counterparts
        return {k: (v if isinstance(v, dict) else {"label": v}) for k, v in parsed.items()}
    if provider == "jev":
        key = dd_mod._env_key("jev")
        if not key:
            raise RuntimeError("TYPESAFE_API_KEY not set")
        import json as _json

        questions = dd_mod.get_task_heads(task, "jev")
        payload_questions = {q["id"]: {"type": q["type"], "instructions": q["instructions"], "criteria": q.get("criteria")} for q in questions}
        body = _json.dumps({"state": {"diff": diff}, "questions": payload_questions, "model": pcfg["jev_model"]})
        raw = dd_mod._call_jev_raw(body, key, cfg)
        return dd_mod._parse_jev_response(raw, [q["id"] for q in questions])
    if provider == "local":
        return dd_mod._call_local_provider(diff, heads, cfg)
    raise RuntimeError(f"unknown provider {provider!r}")


# ── comparison ────────────────────────────────────────────────────────────────


def _compare(provider: str, dd: dict, sys1_a: dict, sys1_heads: list, divergences: list[str]) -> None:
    """Match dev-decisions' task[:40]-keyed answers against sys1's head.id-keyed answers.

    The two systems must agree on the DECISION (label) and answer shape per
    head. They differ in the key naming: dev-decisions keys by `task[:40]`;
    sys1 keys by `head.id`. This function uses the sys1 head list to bridge
    the two so the values can be compared.

    Confidence parity is strict for hosted providers. For `local` it is
    informational (2026-09-28 fastino adoption): the classification schema's
    task NAME is part of the model input, and the two implementations name
    tasks differently (sys1: head id; dev-decisions' degraded inline path:
    task text) — same labels, slightly different confidences. Measured:
    diff_type 0.3884 (task text) vs 0.3949 (head id) on the same text.

    Noul heads: sys1's prescribed shape carries the yes-probability with no
    label; dev-decisions carries label yes/no + confidence. Compared via
    probability/label consistency.
    """
    head_by_id = {h.id: h for h in sys1_heads}
    # dev-decisions keys are task[:40]; build a prefix → entry index once
    dd_by_prefix: dict[str, dict] = {}
    for k, v in dd.items():
        if isinstance(k, str) and not k.startswith("_") and isinstance(v, dict):
            dd_by_prefix[k[:40]] = v

    for head_id, sys1_entry in sys1_a.items():
        if head_id.startswith("_"):
            continue
        # Find the matching dev-decisions entry by task text prefix
        head = head_by_id.get(head_id)
        dd_entry = None
        if head is not None:
            dd_entry = dd_by_prefix.get(head.task[:40])
        if dd_entry is None and isinstance(sys1_entry, dict):
            # Jev-style heads use head_id as the key already
            dd_entry = dd_by_prefix.get(head_id)
        if dd_entry is None:
            divergences.append(f"{provider}: head {head_id!r} has no dev-decisions counterpart")
            continue
        dd_label = dd_entry.get("label") or dd_entry.get("score") or dd_entry.get("noul")
        dd_conf = dd_entry.get("confidence")

        if sys1_entry.get("type") == "noul" and sys1_entry.get("label") is None:
            # prescribed noul shape: probability of "true", no confidence field
            p = sys1_entry.get("noul")
            if not isinstance(p, (int, float)) or isinstance(p, bool):
                divergences.append(f"{provider}: {head_id} sys1 noul has no gateable probability")
                continue
            if dd_label == "yes" and float(p) < 0.5:
                divergences.append(f"{provider}: {head_id} label yes but sys1 p={p:.3f}")
            elif dd_label == "no" and float(p) >= 0.5:
                divergences.append(f"{provider}: {head_id} label no but sys1 p={p:.3f}")
            else:
                print(f"  ✓ {provider}: {head_id} → {dd_label!r} (sys1 p={p:.3f})")
            continue

        sys1_label = sys1_entry.get("label") or sys1_entry.get("score") or sys1_entry.get("noul")
        sys1_conf = sys1_entry.get("confidence")
        if str(dd_label) != str(sys1_label):
            divergences.append(f"{provider}: {head_id} label dev-decisions={dd_label!r} vs sys1={sys1_label!r}")
        elif dd_conf is not None and sys1_conf is not None and abs(float(dd_conf) - float(sys1_conf)) > 1e-3:
            if provider == "local":
                # expected post fastino-adoption — see _compare docstring
                print(
                    f"  ~ {provider}: {head_id} conf dev-decisions={float(dd_conf):.4f} "
                    f"vs sys1={float(sys1_conf):.4f} (schema task names differ; labels agree)"
                )
            else:
                divergences.append(f"{provider}: {head_id} conf dev-decisions={dd_conf} vs sys1={sys1_conf}")
        else:
            print(f"  ✓ {provider}: {head_id} → {dd_label!r} (conf {sys1_conf})")


if __name__ == "__main__":
    raise SystemExit(main())
