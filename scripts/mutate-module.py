#!/usr/bin/env python3
"""Mutation-testing harness for the test-surface probe — stdlib only.

Generates mutants of selected functions in a module by AST rewriting
(comparison swaps, boolean-connective flips, constant tweaks, condition
negation, statement removal), runs the target test file against each mutant,
and records which tests kill which mutants:

  pass 1  mutant vs the full test file   -> kill = any test fails (fast triage)
  pass 2  per-test attribution           -> killed_by: [test ids]

Pass 2 is what the probe's layer metrics need (redundancy = kill-set
overlap, tautology = zero kills); it multiplies runtime by the test count,
so pass 1 prunes first. Surviving mutants are recorded but NOT attributed —
presumed behavior-equivalent until a human says otherwise.

The real tree is never modified: the actual sys1 package is copied into a
temp dir and only the target module file is overwritten with the mutant
source, so relative imports keep working.

Output: kill-matrix JSON + summary into --out-dir (default
~/.local/share/sys1/probes/).

Usage:
    .venv/bin/python scripts/mutate-module.py \
        --module src/sys1/plansurface.py \
        --functions parse_criteria,kendall_tau,_topo_order \
        --tests tests/test_plansurface.py --per-test --budget 120
"""

from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_OUT_DIR = Path.home() / ".local/share/sys1/probes"

CMP_SWAPS = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}
BOOL_SWAPS = {ast.And: ast.Or, ast.Or: ast.And}


# ── mutation sites ────────────────────────────────────────────────────────────


def _const_tweak(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    return None


def _drop_ok(parent: ast.AST, stmt: ast.AST) -> bool:
    """First body statement, not a docstring, and the body stays non-empty."""
    body = getattr(parent, "body", None)
    if not isinstance(body, list) or len(body) <= 1 or body[0] is not stmt:
        return False
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return False
    return not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))


def mutation_sites(tree: ast.AST) -> list[dict]:
    """Every eligible site, in source order. kind + locating (lineno, col)."""
    sites: list[dict] = []

    def line_col(node: ast.AST) -> tuple[int, int]:
        return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))

    for node in ast.walk(tree):
        ln, col = line_col(node)
        if isinstance(node, ast.Compare) and node.ops and type(node.ops[0]) in CMP_SWAPS:
            sites.append({"lineno": ln, "col": col, "op": "cmp",
                          "description": f"{type(node.ops[0]).__name__} -> {CMP_SWAPS[type(node.ops[0])].__name__}"})
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOL_SWAPS:
            sites.append({"lineno": ln, "col": col, "op": "bool",
                          "description": f"{type(node.op).__name__} -> {BOOL_SWAPS[type(node.op)].__name__}"})
        elif isinstance(node, ast.Constant) and _const_tweak(node.value) is not None:
            sites.append({"lineno": ln, "col": col, "op": "const",
                          "description": f"{node.value!r} -> {_const_tweak(node.value)!r}"})
        elif isinstance(node, (ast.If, ast.While)):
            sites.append({"lineno": ln, "col": col, "op": "neg", "description": "negate condition"})
        elif isinstance(node, ast.comprehension) and node.ifs:
            sites.append({"lineno": ln, "col": line_col(node.ifs[0]), "op": "neg",
                          "description": "negate comprehension filter"})
    # statement removal needs parents — a second pass with a parent map
    parents: dict[int, tuple[ast.AST, ast.AST]] = {}

    def walk_pairs(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = (node, child)
            walk_pairs(child)

    walk_pairs(tree)
    for _pid, (parent, stmt) in parents.items():
        if isinstance(stmt, ast.stmt) and _drop_ok(parent, stmt):
            ln, col = parents[id(parent)][1].lineno, 0  # attribute to the owner
            sites.append({"lineno": getattr(stmt, "lineno", ln), "col": getattr(stmt, "col_offset", 0),
                          "op": "drop",
                          "description": f"drop first statement ({type(stmt).__name__}) of {type(parent).__name__}"})
    sites.sort(key=lambda s: (s["lineno"], s["col"], s["op"]))
    return sites


def generate_mutants(source: str, functions: list[str], budget: int) -> list[dict]:
    tree = ast.parse(source)
    spans: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            spans[node.name] = (node.lineno, node.end_lineno or node.lineno)
    out: list[dict] = []
    for site in mutation_sites(tree):
        fname = next((f for f, (a, b) in spans.items() if a <= site["lineno"] <= b), None)
        if fname is None:
            continue
        out.append({"id": f"M{len(out) + 1:03d}", "function": fname, **site})
        if len(out) >= budget:
            break
    return out


# ── applying one mutation ────────────────────────────────────────────────────


def apply_mutation(source: str, lineno: int, col: int, op: str) -> str | None:
    """Return the full mutated source, or None if the site cannot be applied."""
    tree = ast.parse(source)
    matched = False

    if op == "drop":
        target = None
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) == lineno and getattr(node, "col_offset", None) == col:
                target = node
                break
        if target is None:
            return None
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and len(body) > 1 and body[0] is target and _drop_ok(node, target):
                node.body = body[1:]
                matched = True
                break
        if not matched:
            return None
    else:
        class OneShot(ast.NodeTransformer):
            def visit_Compare(self, node: ast.Compare):
                self.generic_visit(node)
                nonlocal matched
                if not matched and op == "cmp" and type(node.ops[0]) in CMP_SWAPS \
                        and (node.lineno, node.col_offset) == (lineno, col):
                    matched = True
                    node.ops = [CMP_SWAPS[type(node.ops[0])]()]
                return node

            def visit_BoolOp(self, node: ast.BoolOp):
                self.generic_visit(node)
                nonlocal matched
                if not matched and op == "bool" and type(node.op) in BOOL_SWAPS \
                        and (node.lineno, node.col_offset) == (lineno, col):
                    matched = True
                    node.op = BOOL_SWAPS[type(node.op)]()
                return node

            def visit_Constant(self, node: ast.Constant):
                self.generic_visit(node)
                nonlocal matched
                if not matched and op == "const" and _const_tweak(node.value) is not None \
                        and (node.lineno, node.col_offset) == (lineno, col):
                    matched = True
                    node.value = _const_tweak(node.value)
                return node

            def visit_If(self, node: ast.If):
                self.generic_visit(node)
                nonlocal matched
                if not matched and op == "neg" and (node.lineno, node.col_offset) == (lineno, col):
                    matched = True
                    node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
                return node

            def visit_While(self, node: ast.While):
                self.generic_visit(node)
                nonlocal matched
                if not matched and op == "neg" and (node.lineno, node.col_offset) == (lineno, col):
                    matched = True
                    node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
                return node

            def visit_comprehension(self, node: ast.comprehension):
                self.generic_visit(node)
                nonlocal matched
                if not matched and op == "neg" and node.ifs \
                        and (node.ifs[0].lineno, node.ifs[0].col_offset) == (lineno, col):
                    matched = True
                    node.ifs = [ast.UnaryOp(op=ast.Not(), operand=node.ifs[0]), *node.ifs[1:]]
                return node

        tree = OneShot().visit(tree)
        if not matched:
            return None

    try:
        out = ast.unparse(ast.fix_missing_locations(tree))
        return out if out != source else None
    except Exception:
        return None


# ── running the suite against a mutant ───────────────────────────────────────


def _materialize(td: Path, package_dir: Path, module_file: Path, mutant_src: str | None) -> dict:
    """Copy the real package into td and swap in the mutant (or original)."""
    if package_dir.exists():
        shutil.rmtree(package_dir)
    shutil.copytree(module_file.parent, package_dir,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (package_dir / "__init__.py").touch(exist_ok=True)
    if mutant_src is not None:
        (package_dir / module_file.name).write_text(mutant_src)
    return {"PYTHONPATH": str(td), "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()), "SYS1_LOG_DIR": str(Path(td) / "logs")}


def run_suite(pytest_bin: str, test_file: Path, td: Path, package_dir: Path,
              module_file: Path, mutant_src: str | None, *, only: str | None = None,
              timeout: int = 60) -> tuple[bool, list[str]]:
    env = _materialize(td, package_dir, module_file, mutant_src)
    cmd = [pytest_bin, "-q", "--no-header", "-p", "no:cacheprovider",
           str(test_file), "--rootdir", str(td)]
    if only:
        cmd += ["-k", only]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, ["<timeout>"]
    failing: list[str] = []
    for line in proc.stdout.splitlines():
        s = line.strip()
        if s.startswith(("FAILED", "ERROR")):
            tail = s.split(" ", 1)[1] if " " in s else s
            failing.append(tail.rsplit(" - ", 1)[0])
    return proc.returncode == 0, failing or (["<unknown-failure>"] if proc.returncode else [])


def list_test_ids(test_file: Path) -> list[str]:
    """Test ids via AST, Class.method flattened to method name."""
    tree = ast.parse(test_file.read_text())
    ids: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test_"):
                    ids.append(sub.name)
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            ids.append(node.name)
    return ids


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="AST mutation harness (stdlib only)")
    ap.add_argument("--module", required=True)
    ap.add_argument("--functions", required=True, help="comma-separated function names")
    ap.add_argument("--tests", required=True)
    ap.add_argument("--budget", type=int, default=120)
    ap.add_argument("--no-per-test", action="store_true")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    pytest_bin = shutil.which("pytest") or str(Path(sys.executable).parent / "pytest")
    module_file = Path(args.module).resolve()
    test_file = Path(args.tests).resolve()
    source = module_file.read_text()
    functions = [f.strip() for f in args.functions.split(",") if f.strip()]
    pkg_name = module_file.parent.name

    with tempfile.TemporaryDirectory(prefix="mut_") as td_s:
        td = Path(td_s)
        package_dir = td / pkg_name

        ok, failing = run_suite(pytest_bin, test_file, td, package_dir, module_file, None,
                                timeout=args.timeout)
        if not ok:
            print(f"error: baseline suite fails against unmutated module: {failing}", file=sys.stderr)
            return 1
        print(f"[mut] baseline passes against unmutated {module_file.name}")

        test_ids = list_test_ids(test_file)
        print(f"[mut] {len(test_ids)} tests: {', '.join(test_ids)}")

        sites = generate_mutants(source, functions, args.budget)
        print(f"[mut] {len(sites)} sites in {functions} (budget {args.budget})")

        mutants: list[dict] = []
        for site in sites:
            src = apply_mutation(source, site["lineno"], site["col"], site["op"])
            if src is None:
                continue
            passed, failing = run_suite(pytest_bin, test_file, td, package_dir,
                                        module_file, src, timeout=args.timeout)
            killed = not passed
            mutants.append({**site, "killed": killed, "src_sha8": _sha8(src),
                            "failing": failing if killed else []})
            print(f"[mut] {site['id']} {site['function']}:{site['lineno']} {site['description']:38s} -> "
                  f"{'KILLED ' if killed else 'SURVIVE'} {failing if killed else ''}")

        killed = [m for m in mutants if m["killed"]]
        if not args.no_per_test:
            print(f"[mut] pass 2: per-test attribution over {len(killed)} killed mutants x {len(test_ids)} tests")
            for m in killed:
                src = apply_mutation(source, m["lineno"], m["col"], m["op"])
                if src is None:
                    m["killed_by"] = ["<regen-failed>"]
                    continue
                m["killed_by"] = []
                for tid in test_ids:
                    passed, _ = run_suite(pytest_bin, test_file, td, package_dir,
                                          module_file, src, only=tid, timeout=args.timeout)
                    if not passed:
                        m["killed_by"].append(tid)
                print(f"[mut] {m['id']} killed_by: {', '.join(m['killed_by']) or '(none)'}")

        for m in mutants:
            m.setdefault("killed_by", [])
        summary = {
            "module": str(module_file), "functions": functions, "tests": str(test_file),
            "n_mutants": len(mutants), "n_killed": len(killed),
            "n_survived": len(mutants) - len(killed),
            "mutation_score": round(len(killed) / len(mutants), 3) if mutants else None,
            "per_test": not args.no_per_test, "test_ids": test_ids,
        }
        out_dir = Path(args.out_dir).expanduser() if args.out_dir else DEFAULT_OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = out_dir / f"kill-matrix-{pkg_name}-{stamp}.json"
        out_path.write_text(json.dumps({"summary": summary, "mutants": mutants}, indent=2))
        print(f"[mut] score {summary['n_killed']}/{summary['n_mutants']} "
              f"(survived {summary['n_survived']}) -> {out_path}")
        survivors = [m for m in mutants if not m["killed"]]
        if survivors:
            print(f"[mut] survivors for manual triage ({len(survivors)}):")
            for m in survivors:
                print(f"  {m['id']} {m['function']}:{m['lineno']} {m['description']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
