"""Mutation-harness tests: the mutator must produce the exact behavioral
change each operator claims, and no change when no site matches."""

from __future__ import annotations

import ast
import importlib.util
import textwrap
from pathlib import Path


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "mutate_module", Path(__file__).parent.parent / "scripts" / "mutate-module.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mut = _load_harness()
apply_mutation, generate_mutants, mutation_sites = (
    mut.apply_mutation, mut.generate_mutants, mut.mutation_sites)

SRC = textwrap.dedent('''\
    def pick(xs, limit):
        """Keep small even members."""
        out = []
        for x in xs:
            if x < limit and x % 2 == 0:
                out.append(x)
        if len(out) > 1:
            out = out[:1]
        return out


    def clamp(v, lo, hi):
        v = max(v, lo)
        return min(v, hi)
''')


def _site(desc_fragment: str, op: str | None = None) -> dict:
    sites = mutation_sites(ast.parse(SRC))
    for s in sites:
        if desc_fragment in s["description"] and (op is None or s["op"] == op):
            return s
    raise AssertionError(f"no site matching {desc_fragment!r} op={op}")


def _exec(mutant_src: str):
    ns: dict = {}
    exec(mutant_src, ns)
    return ns


class TestSiteEnumeration:
    def test_finds_each_operator_family(self):
        sites = mutation_sites(ast.parse(SRC))
        ops = {s["op"] for s in sites}
        assert ops == {"cmp", "bool", "const", "neg", "drop"}
        descs = " | ".join(s["description"] for s in sites)
        assert "Lt -> GtE" in descs
        assert "And -> Or" in descs
        assert "2 -> 3" in descs

    def test_generate_scopes_and_orders(self):
        muts = generate_mutants(SRC, ["pick"], 100)
        assert muts
        assert all(m["function"] == "pick" for m in muts)
        assert [m["id"] for m in muts] == [f"M{i + 1:03d}" for i in range(len(muts))]
        coords = [(m["lineno"], m["col"]) for m in muts]
        assert coords == sorted(coords)
        assert generate_mutants(SRC, ["pick"], 3)[2]["id"] == "M003"
        # clamp is excluded when not named
        assert all(m["function"] != "clamp" for m in muts)


class TestApplyMutation:
    def test_cmp_swap_changes_behavior(self):
        s = _site("Lt -> GtE")
        ns = _exec(apply_mutation(SRC, s["lineno"], s["col"], "cmp"))
        assert ns["pick"]([2, 3, 6], 5) == [6]  # was [2]: 2 < 5 flips to 2 >= 5

    def test_bool_flip_changes_behavior(self):
        s = _site("And -> Or")
        ns = _exec(apply_mutation(SRC, s["lineno"], s["col"], "bool"))
        assert ns["pick"]([3], 5) == [3]  # original excludes 3 (and-gate), or-gate lets it through

    def test_const_bump_changes_behavior(self):
        s = _site("2 -> 3")
        ns = _exec(apply_mutation(SRC, s["lineno"], s["col"], "const"))
        assert ns["pick"]([2, 3, 6], 5) == [3]  # odd-multiple filter (then truncation keeps the first)

    def test_neg_changes_behavior(self):
        s = max((x for x in mutation_sites(ast.parse(SRC)) if x["op"] == "neg"),
                key=lambda x: x["lineno"])
        ns = _exec(apply_mutation(SRC, s["lineno"], s["col"], "neg"))
        assert ns["pick"]([2, 4], 10) == [2, 4]  # truncation inverted away

    def test_drop_changes_behavior(self):
        drops = [x for x in mutation_sites(ast.parse(SRC)) if x["op"] == "drop"]
        s = drops[-1]  # clamp is the last function; its leading Assign is the last drop site
        ns = _exec(apply_mutation(SRC, s["lineno"], s["col"], "drop"))
        assert ns["clamp"](0, 3, 10) == 0  # lower clamp dropped, was 3

    def test_no_match_returns_none(self):
        assert apply_mutation(SRC, 99, 0, "cmp") is None
        assert apply_mutation(SRC, 99, 0, "drop") is None

    def test_mutant_differs_from_source(self):
        for s in mutation_sites(ast.parse(SRC)):
            out = apply_mutation(SRC, s["lineno"], s["col"], s["op"])
            if out is None:
                continue
            assert out != SRC, f"mutant identical to source at {s}"
