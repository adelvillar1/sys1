#!/usr/bin/env python3
"""Test-surface probe: sys1 layers over a test suite, graded by mutation ground truth.

Layers (all judged blind to the kill matrix):
  A  behavior inventory   per (function, candidate behavior) noul — candidates
                          derived from the function docstring and the names of
                          the tests that exercise it.
  B  tautology gate       per test choice contract/mixed/tautological, judged
                          from the test source against the function source.
  C  redundancy pairs     per same-function test pair noul "same behavior".

Ground truth from scripts/mutate-module.py's kill matrix: per-test kill sets.
  B  zero kills = tautological in practice (survivors excluded — presumed
     equivalent mutants, not tautologies)
  C  Jaccard overlap of kill sets (>= 0.5 = same behavior in practice)

Usage:
    .venv/bin/python scripts/probe-test-surface.py \
        --kill-matrix ~/.local/share/sys1/probes/kill-matrix-plansurface-*.json \
        --module src/sys1/plansurface.py --tests tests/test_plansurface.py
"""

from __future__ import annotations

import argparse
import ast
import datetime
import itertools
import json
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

import sys1
from sys1.telemetry import record_feedback
from sys1.types import Task, make_choice, make_noul

from probe_lib import (
    chunked, first_answers, load_module_by_path, sha8, span_source,
)

DEFAULT_OUT_DIR = Path.home() / ".local/share/sys1/probes"
PAIRS_PER_REQUEST = 14
MAX_CANDIDATES = 6
STOP = {"test", "tests", "assert", "asserts", "with", "that", "this", "from", "into", "when", "then"}


# ── blind-side extraction ────────────────────────────────────────────────────


def extract_functions(module_src: str, names: list[str]) -> dict[str, str]:
    tree = ast.parse(module_src)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            out[node.name] = span_source(module_src, node.lineno, node.end_lineno or node.lineno)
    return out


def extract_tests(test_src: str) -> dict[str, str]:
    """Test id -> source segment (Class.method flattened to method)."""
    tree = ast.parse(test_src)
    lines = test_src.splitlines(keepends=True)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test_"):
                    out[sub.name] = span_source(test_src, sub.lineno, sub.end_lineno or sub.lineno)
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            out[node.name] = span_source(test_src, node.lineno, node.end_lineno or node.lineno)
    return out


def map_tests_to_functions(tests: dict[str, str], functions: dict[str, str]) -> dict[str, list[str]]:
    """A test targets a function when the function's name appears in its body
    (or in fixture helper names it calls). Loose, and that is fine at probe
    granularity: the layers judge, this mapping only routes candidates."""
    out: dict[str, list[str]] = {}
    for tid, tsrc in tests.items():
        hits = []
        for fname in functions:
            probe = fname.lstrip("_")
            if re.search(rf"\b{re.escape(probe)}\b", tsrc):
                hits.append(fname)
        out[tid] = hits
    return out


def behavior_candidates(func_src: str, test_ids: list[str]) -> list[str]:
    doc = ast.get_docstring(ast.parse(func_src)) or ""
    cands = [s.strip() for s in re.split(r"[.;\n]", doc) if len(s.strip()) > 12]
    for tid in test_ids:
        words = [w for w in re.split(r"[_]+", tid.replace("test_", "")) if len(w) > 2 and w not in STOP]
        if words:
            cands.append(" ".join(words))
    seen, out = set(), []
    for c in cands:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out[:MAX_CANDIDATES]


# ── layer task builders (same flat shape as plan-surface) ────────────────────


def build_behavior_task(cands: list[dict]) -> Task:
    heads = [make_noul(
        f"Is \"{c['behavior']}\" a distinct observable behavior of function {c['function']}? "
        f"Function source:\n{c['func_src'][:2000]}",
        id=f"b{c['index']}_is_behavior") for c in cands]
    return Task(id="test_behavior", heads=heads,
                description="Behavior-inventory judgment per (function, candidate) pair")


def build_tautology_task(items: list[dict]) -> Task:
    heads = [make_choice(
        f"Does test {it['test']} assert the CONTRACT of the code under test, or restate its "
        f"implementation (a tautological / change-detector assertion)? Test source:\n"
        f"{it['test_src'][:1600]}\nCode under test:\n{it['func_src'][:1600]}",
        ["contract", "mixed", "tautological"],
        id=f"t{it['index']}_oracle",
        descriptions={
            "contract": "The expected values come from the specification/domain, not from mirroring the implementation.",
            "mixed": "Some assertions are contractual, some restate the implementation.",
            "tautological": "The assertions restate what the implementation does; a different correct implementation would fail them.",
        }) for it in items]
    return Task(id="test_tautology", heads=heads,
                description="Oracle-quality (tautology) judgment per test")


def build_redundancy_task(pairs: list[dict]) -> Task:
    heads = [make_noul(
        f"Do tests {p['a']} and {p['b']} of function {p['function']} exercise the SAME behavior "
        f"(one could be deleted without losing coverage)?\nTest A:\n{p['a_src'][:1200]}\n"
        f"Test B:\n{p['b_src'][:1200]}\nFunction:\n{p['func_src'][:1200]}",
        id=f"r{p['index']}_same") for p in pairs]
    return Task(id="test_redundancy", heads=heads,
                description="Redundancy judgment per same-function test pair")


# ── running a task ───────────────────────────────────────────────────────────


def run_task(task: Task, state: str, provider: str, cfg: dict) -> dict:
    chain = [p.strip() for p in provider.split(",") if p.strip()]
    result = sys1.classify(chain, task, state, cfg=cfg, log=True, input_sha256=sha8(task.id + state))
    pid, answers = first_answers(result, chain)
    return {"provider": pid, "answers": answers, "telemetry": result.telemetry.get(pid, {}),
            "sha256": sha8(task.id + state)}


def run_chunked(build, items: list[dict], state_line, provider: str, cfg: dict,
                per_request: int = PAIRS_PER_REQUEST) -> dict:
    answers: dict = {}
    sha_by_head: dict[str, str] = {}
    pid, tel = "", {}
    for chunk in chunked(items, per_request):
        task = build(chunk)
        state = "\n".join(state_line(it) for it in chunk)
        res = run_task(task, state, provider, cfg)
        if res["provider"] and not pid:
            pid, tel = res["provider"], res["telemetry"]
        answers.update(res["answers"])
        for h in task.heads:
            sha_by_head[h.id] = res["sha256"]
    return {"provider": pid, "answers": answers, "telemetry": tel, "sha_by_head": sha_by_head}


# ── ground-truth helpers ─────────────────────────────────────────────────────


def kill_sets(matrix: dict, test_ids: list[str]) -> dict[str, set[str]]:
    """test id -> set of mutant ids it kills (from per-test attribution)."""
    out: dict[str, set[str]] = {tid: set() for tid in test_ids}
    for m in matrix["mutants"]:
        for tid in m.get("killed_by", []):
            if tid in out:
                out[tid].add(m["id"])
    return out


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def handcheck(matrix: dict, module_file: Path, test_file: Path, n: int = 3) -> list[dict]:
    """Re-run up to n killed mutants and confirm the kill reproduces."""
    pytest_bin = shutil.which("pytest") or str(Path(sys.executable).parent / "pytest")
    mut = load_module_by_path("mutate_module", Path(__file__).parent / "mutate-module.py")
    killed = [m for m in matrix["mutants"] if m.get("killed")][:n]
    checks = []
    pkg = module_file.parent.name
    with tempfile.TemporaryDirectory(prefix="handcheck_") as td_s:
        td = Path(td_s)
        pkg_dir = td / pkg
        for m in killed:
            src = mut.apply_mutation(module_file.read_text(), m["lineno"], m["col"], m["op"])
            if src is None:
                checks.append({"mutant": m["id"], "reproduced": False, "note": "regen failed"})
                continue
            if pkg_dir.exists():
                shutil.rmtree(pkg_dir)
            shutil.copytree(module_file.parent, pkg_dir, ignore=shutil.ignore_patterns("__pycache__"))
            (pkg_dir / module_file.name).write_text(src)
            env = {"PYTHONPATH": str(td), "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()),
                   "SYS1_LOG_DIR": str(td / "logs")}
            proc = subprocess.run([pytest_bin, "-q", "--no-header", "-p", "no:cacheprovider",
                                   str(test_file), "--rootdir", str(td)],
                                  capture_output=True, text=True, timeout=120, env=env)
            checks.append({"mutant": m["id"], "reproduced": proc.returncode != 0,
                           "description": m["description"]})
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade sys1 test-surface layers against mutation ground truth")
    ap.add_argument("--kill-matrix", required=True)
    ap.add_argument("--module", required=True)
    ap.add_argument("--tests", required=True)
    ap.add_argument("--provider", default="jev")
    ap.add_argument("--jaccard", type=float, default=0.5, help="ground-truth redundancy cut")
    ap.add_argument("--redundant-noul", type=float, default=0.7, help="layer redundancy cut")
    ap.add_argument("--skip-handcheck", action="store_true")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    module_file = Path(args.module).resolve()
    test_file = Path(args.tests).resolve()
    matrix = json.loads(Path(args.kill_matrix).expanduser().read_text())
    cfg = sys1.load_config()

    module_src = module_file.read_text()
    test_src = test_file.read_text()
    functions = extract_functions(module_src, [m["function"] for m in matrix["mutants"]])
    tests = extract_tests(test_src)
    routes = map_tests_to_functions(tests, functions)
    kills = kill_sets(matrix, list(tests))

    print(f"[probe] {len(matrix['mutants'])} mutants ({matrix['summary']['n_killed']} killed, "
          f"{matrix['summary']['n_survived']} survived); {len(tests)} tests")

    # ── layer A: behavior inventory ──────────────────────────────────────────
    cands: list[dict] = []
    for fname, fsrc in functions.items():
        tids = [t for t in tests if fname in routes.get(t, [])]
        for b in behavior_candidates(fsrc, tids):
            cands.append({"index": len(cands), "function": fname, "behavior": b, "func_src": fsrc})
    beh = run_chunked(build_behavior_task, cands,
                      lambda it: f"{it['index']}: function {it['function']} — candidate behavior: {it['behavior']}",
                      args.provider, cfg)
    print(f"[probe] layer A: {len(cands)} candidates judged (provider={beh['provider']})")

    # ── layer B: tautology ───────────────────────────────────────────────────
    items = []
    for tid, tsrc in tests.items():
        targets = routes.get(tid, [])
        fsrc = functions[targets[0]] if targets else module_src[:1600]
        items.append({"index": len(items), "test": tid, "test_src": tsrc, "func_src": fsrc})
    taut = run_chunked(build_tautology_task, items,
                       lambda it: f"{it['index']}: test {it['test']}",
                       args.provider, cfg)
    print(f"[probe] layer B: {len(items)} tests judged (provider={taut['provider']})")

    # ── layer C: redundancy pairs ────────────────────────────────────────────
    by_func: dict[str, list[str]] = {}
    for tid, targets in routes.items():
        for f in targets:
            by_func.setdefault(f, []).append(tid)
    pairs = []
    for fname, tids in by_func.items():
        for a, b in itertools.combinations(sorted(tids), 2):
            pairs.append({"index": len(pairs), "function": fname, "a": a, "b": b,
                          "a_src": tests[a], "b_src": tests[b], "func_src": functions[fname]})
    red = run_chunked(build_redundancy_task, pairs,
                      lambda it: f"{it['index']}: tests {it['a']} vs {it['b']} of {it['function']}",
                      args.provider, cfg) if pairs else \
        {"provider": "", "answers": {}, "sha_by_head": {}}
    print(f"[probe] layer C: {len(pairs)} pairs judged (provider={red['provider']})")

    # ── grading ──────────────────────────────────────────────────────────────
    def noul(ans: dict) -> float | None:
        v = ans.get("noul")
        return float(v) if isinstance(v, (int, float)) else None

    # Layer B vs kill counts
    b_rows = []
    for it in items:
        a = taut["answers"].get(f"t{it['index']}_oracle") or {}
        kc = len(kills.get(it["test"], set()))
        b_rows.append({"test": it["test"], "verdict": a.get("label"),
                       "confidence": a.get("confidence"), "kills": kc})
    flagged_zero = sum(1 for r in b_rows if r["verdict"] == "tautological" and r["kills"] == 0)
    flagged_but_kills = sum(1 for r in b_rows if r["verdict"] == "tautological" and r["kills"] > 0)
    contract_but_zero = sum(1 for r in b_rows if r["verdict"] == "contract" and r["kills"] == 0)
    zero_kill_tests = [r["test"] for r in b_rows if r["kills"] == 0]

    # Layer C vs Jaccard
    c_rows = []
    for p in pairs:
        a = red["answers"].get(f"r{p['index']}_same") or {}
        p_ = noul(a)
        jac = jaccard(kills.get(p["a"], set()), kills.get(p["b"], set()))
        c_rows.append({"pair": f"{p['a']} + {p['b']}", "function": p["function"],
                       "noul": round(p_, 3) if p_ is not None else None,
                       "jaccard": round(jac, 3)})
    tp = sum(1 for r in c_rows if (r["noul"] or 0) >= args.redundant_noul and r["jaccard"] >= args.jaccard)
    fp = sum(1 for r in c_rows if (r["noul"] or 0) >= args.redundant_noul and r["jaccard"] < args.jaccard)
    fn = sum(1 for r in c_rows if (r["noul"] or 0) < args.redundant_noul and r["jaccard"] >= args.jaccard)

    # Layer A: judged behaviors whose function's tests kill mutants = carried claims
    a_rows = []
    for c in cands:
        a = beh["answers"].get(f"b{c['index']}_is_behavior") or {}
        p_ = noul(a)
        tids = [t for t in tests if c["function"] in routes.get(t, [])]
        kills_hit = sum(len(kills.get(t, set())) for t in tids)
        a_rows.append({"function": c["function"], "behavior": c["behavior"],
                       "noul": round(p_, 3) if p_ is not None else None,
                       "function_kills": kills_hit})
    a_yes = sum(1 for r in a_rows if (r["noul"] or 0) >= 0.5)

    # feedback rows
    n_feedback = 0
    for it in items:
        a = taut["answers"].get(f"t{it['index']}_oracle") or {}
        if a:
            actual = "tautological" if len(kills.get(it["test"], set())) == 0 else "contract"
            record_feedback(input_sha256=taut["sha_by_head"].get(f"t{it['index']}_oracle", taut.get("sha256", "")),
                            task="test_tautology", head_id=f"t{it['index']}_oracle",
                            predicted_label=a.get("label"), predicted_confidence=a.get("confidence"),
                            provider=taut["provider"], actual_label=actual, escalated=False, cfg=cfg)
            n_feedback += 1
    for p, row in zip(pairs, c_rows):
        a = red["answers"].get(f"r{p['index']}_same") or {}
        if a and noul(a) is not None:
            record_feedback(input_sha256=red["sha_by_head"].get(f"r{p['index']}_same", ""),
                            task="test_redundancy", head_id=f"r{p['index']}_same",
                            predicted_label="yes" if (noul(a) or 0) >= 0.5 else "no",
                            predicted_confidence=noul(a), provider=red["provider"],
                            actual_label="yes" if row["jaccard"] >= args.jaccard else "no",
                            escalated=False, cfg=cfg)
            n_feedback += 1
    for c, row in zip(cands, a_rows):
        a = beh["answers"].get(f"b{c['index']}_is_behavior") or {}
        if a and noul(a) is not None:
            record_feedback(input_sha256=beh["sha_by_head"].get(f"b{c['index']}_is_behavior", ""),
                            task="test_behavior", head_id=f"b{c['index']}_is_behavior",
                            predicted_label="yes" if (noul(a) or 0) >= 0.5 else "no",
                            predicted_confidence=noul(a), provider=beh["provider"],
                            actual_label="yes" if row["function_kills"] > 0 else "no",
                            escalated=False, cfg=cfg)
            n_feedback += 1

    checks = [] if args.skip_handcheck else handcheck(matrix, module_file, test_file)

    # ── report ───────────────────────────────────────────────────────────────
    L: list[str] = []
    L.append(f"# test-surface probe: {module_file.name}")
    L.append("")
    L.append(f"- provider: {args.provider} (A={beh['provider']}, B={taut['provider']}, C={red['provider']})")
    L.append(f"- mutation: {matrix['summary']['n_killed']}/{matrix['summary']['n_mutants']} killed "
             f"(score {matrix['summary']['mutation_score']}), {matrix['summary']['n_survived']} survived "
             f"(presumed equivalent, excluded from tautology ground truth)")
    L.append(f"- tests: {len(tests)}, kill-attributed: {sum(1 for k in kills.values() if k)}")
    if checks:
        L.append(f"- hand-check: {sum(1 for c in checks if c['reproduced'])}/{len(checks)} kills reproduced "
                 f"({', '.join(c['mutant'] for c in checks)})")
    L.append("")
    L.append("## Layer B: tautology gate vs kill counts")
    L.append("")
    L.append("| test | layer verdict | conf | mutants killed |")
    L.append("|------|---------------|------|----------------|")
    for r in b_rows:
        L.append(f"| {r['test']} | {r['verdict']} | {r['confidence']} | {r['kills']} |")
    L.append("")
    L.append(f"flagged tautological AND zero kills: **{flagged_zero}**; "
             f"flagged tautological but kills mutants (layer wrong or equivalent survivors): **{flagged_but_kills}**; "
             f"judged contract with zero kills (real tautology misses): **{contract_but_zero}**")
    L.append(f"zero-kill tests: {', '.join(zero_kill_tests) or '(none)'}")
    L.append("")
    L.append("## Layer C: redundancy vs kill-set Jaccard")
    L.append("")
    L.append("| pair | function | noul | jaccard |")
    L.append("|------|----------|------|---------|")
    for r in c_rows:
        L.append(f"| {r['pair']} | {r['function']} | {r['noul']} | {r['jaccard']} |")
    L.append("")
    prec = round(tp / (tp + fp), 2) if (tp + fp) else None
    rec = round(tp / (tp + fn), 2) if (tp + fn) else None
    L.append(f"at noul>={args.redundant_noul} vs jaccard>={args.jaccard}: "
             f"precision {prec} (tp={tp}, fp={fp}), recall {rec} (fn={fn})")
    L.append("")
    L.append("## Layer A: behavior inventory")
    L.append("")
    L.append("| function | candidate behavior | noul | function's tests kill N mutants |")
    L.append("|----------|--------------------|------|--------------------------------|")
    for r in a_rows:
        L.append(f"| {r['function']} | {r['behavior'][:80]} | {r['noul']} | {r['function_kills']} |")
    L.append("")
    L.append(f"judged behaviors: {a_yes}/{len(a_rows)}")
    L.append("")
    L.append(f"feedback rows written: {n_feedback}")
    L.append("")

    out_dir = Path(args.out_dir).expanduser() if args.out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = out_dir / f"test-surface-{module_file.stem}-{stamp}.md"
    report_path.write_text("\n".join(L))
    print(f"[probe] layer B: tautological&zero-kill={flagged_zero}, flagged-but-kills={flagged_but_kills}, "
          f"contract-but-zero={contract_but_zero}")
    print(f"[probe] layer C: tp={tp} fp={fp} fn={fn} (noul>={args.redundant_noul}, jaccard>={args.jaccard})")
    print(f"[probe] feedback rows: {n_feedback}")
    print(f"[probe] report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
