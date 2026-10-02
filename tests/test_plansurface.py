"""plan-surface tests: parsing, builders, assembly, reconcile, stub wiring.

The graph layer is pure code, so most of this file needs no provider at all.
The wiring tests (run_map / run_deps through a registered stub) follow the
test_recipe_pack pattern: the stub answers by head id from a fixed table, so
a failure means mis-wired heads, not model disagreement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sys1
from sys1.types import Provider
from sys1.plansurface import (
    assemble,
    build_attribution_task,
    grade_and_record,
    build_dep_tasks,
    build_map_task,
    inventory_surface,
    kendall_tau,
    parse_criteria,
    reconcile,
    run_deps,
    run_map,
)

PLAN_MD = """\
---
status: active
created: 2026-10-01
---

# A plan

## Acceptance criteria

- [ ] Core engine persists snapshots to the store
- [ ] Web dashboard renders the snapshot timeline
- [x] CLI flag parses arguments

Some prose that is not a criterion.
"""


class TestParseCriteria:
    def test_parses_checkboxes_ignoring_prose(self):
        criteria = parse_criteria(PLAN_MD)
        assert len(criteria) == 3
        assert criteria[0] == "Core engine persists snapshots to the store"
        assert criteria[2] == "CLI flag parses arguments"

    def test_caps_at_max(self):
        md = "\n".join(f"- [ ] criterion number {i} does things" for i in range(20))
        assert len(parse_criteria(md)) == 12


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src" / "sys1").mkdir(parents=True)
    for i in range(4):
        (tmp_path / "src" / "sys1" / f"mod{i}.py").write_text("x = 1\n")
    (tmp_path / "webapp" / "node_modules").mkdir(parents=True)
    (tmp_path / "webapp" / "node_modules" / "dep.js").write_text("x")
    (tmp_path / "webapp" / "src").mkdir(parents=True)
    (tmp_path / "webapp" / "src" / "index.ts").write_text("export {}\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("notes\n")
    (tmp_path / "app.py").write_text("print(1)\n")
    (tmp_path / "README.md").write_text("readme\n")
    big = tmp_path / "big"
    (big / "sub1").mkdir(parents=True)
    for i in range(15):
        (big / f"f{i}.py").write_text("x\n")
    (big / "sub1" / "inner.py").write_text("x\n")
    return tmp_path


class TestInventory:
    def test_numbers_and_filters(self, repo):
        mods = inventory_surface(repo)
        ids = [m["id"] for m in mods]
        assert ids == [f"m{i:03d}" for i in range(1, len(mods) + 1)]
        paths = [m["path"] for m in mods]
        assert "src" in paths and "webapp" in paths and "app.py" in paths
        assert "webapp/src" not in paths  # webapp has 1 code file: below the expansion threshold
        assert "docs" not in paths and "webapp/node_modules" not in paths
        assert "README.md" not in paths

    def test_busy_dir_expands(self, repo):
        mods = inventory_surface(repo)
        paths = [m["path"] for m in mods]
        assert "big" in paths and "big/sub1" in paths

    def test_cap(self, repo):
        assert len(inventory_surface(repo, max_modules=3)) == 3


class TestBuildMapTask:
    def test_heads_and_state(self):
        criteria = parse_criteria(PLAN_MD)
        modules = [{"id": "m001", "path": "src", "n_files": 4, "langs": "py"},
                   {"id": "m002", "path": "webapp/src", "n_files": 1, "langs": "ts"}]
        task, state = build_map_task(criteria, modules)
        assert task.id == "plan_surface_map"
        assert task.head_ids() == [
            "c0_modules", "c0_any", "c1_modules", "c1_any", "c2_modules", "c2_any",
        ]
        choice = task.find("c0_modules")
        assert choice.multi_label is True
        assert choice.labels == ["m001", "m002"]
        assert criteria[0] in choice.task  # criterion rides in the question
        assert "m001 = src" in state


class TestBuildDepTasks:
    def test_pairs_and_chunking(self):
        criteria = [f"criterion {i}" for i in range(5)]
        tasks, state = build_dep_tasks(criteria)
        assert len(tasks) == 1  # 10 pairs fit in one 14-head chunk
        assert len(tasks[0].heads) == 10
        assert "C4: criterion 4" in state

        criteria9 = [f"criterion {i}" for i in range(9)]  # 36 pairs -> 14/14/8
        tasks9, _ = build_dep_tasks(criteria9)
        assert [len(t.heads) for t in tasks9] == [14, 14, 8]

    def test_head_shape(self):
        criteria = ["first thing", "second thing", "third thing"]
        tasks, _ = build_dep_tasks(criteria)
        head = tasks[0].find("p_0_2")
        assert head is not None
        assert head.labels == ["c0_first", "c2_first", "independent"]
        assert "second thing" not in head.task  # only the pair's criteria ride along
        assert all(len(h.id) for h in tasks[0].heads)  # ids unique by construction
        assert len({h.id for h in tasks[0].heads}) == 3  # 3 pairs for 3 criteria


class TestAssemble:
    def _deps(self, overrides: dict[str, dict]) -> dict:
        return {"provider": "stub", "answers": overrides, "telemetry": {}, "sha256": "x"}

    def test_edges_thresholds_and_risks(self):
        criteria = ["engine store", "dashboard render", "docs pages"]
        modules = [
            {"id": "m001", "path": "src/core", "n_files": 4, "langs": "py"},
            {"id": "m002", "path": "db/migrations", "n_files": 2, "langs": "sql"},
            {"id": "m003", "path": "web", "n_files": 9, "langs": "ts"},
        ]
        map_res = {"provider": "stub", "answers": {
            "c0_modules": {"probabilities": {"m001": 0.9, "m002": 0.7, "m003": 0.2}},
            "c0_any": {"noul": 0.95},
            "c1_modules": {"probabilities": {"m003": 0.8}},
            "c1_any": {"noul": 0.9},
            "c2_modules": {"probabilities": {}},
            "c2_any": {"noul": 0.1},
        }, "telemetry": {}, "sha256": "x"}
        dep_res = self._deps({
            "p_0_1": {"probabilities": {"c0_first": 0.85, "c1_first": 0.05, "independent": 0.1}},
            "p_0_2": {"probabilities": {"c0_first": 0.1, "c1_first": 0.1, "independent": 0.8}},
            "p_1_2": {"probabilities": {"c1_first": 0.7, "c2_first": 0.2, "independent": 0.1}},
        })
        art = assemble(map_res, dep_res, criteria, modules)

        c0 = art["criteria"][0]
        assert [m["id"] for m in c0["modules"]] == ["m001", "m002"]  # m003 below map threshold
        assert c0["risks"] == ["migrat"]
        assert c0["touches_any"] == 0.95

        assert art["edges"] == [
            {"before": "C0", "after": "C1", "p": 0.85, "head": "p_0_1"},
        ]
        assert len(art["uncertain"]) == 1  # p_1_2 at 0.7 is in the band, reported not dropped
        assert art["uncertain"][0]["pair"] == ["C1", "C2"]
        assert art["order"] == ["C0", "C1", "C2"]
        assert art["meta"]["dep_probs"]["0,2"]["independent"] == 0.8

    def test_cycle_breaks_on_weakest_edge(self):
        criteria = ["a", "b", "c"]
        dep_res = self._deps({
            "p_0_1": {"probabilities": {"c0_first": 0.9, "c1_first": 0.05, "independent": 0.05}},
            "p_1_2": {"probabilities": {"c1_first": 0.9, "c2_first": 0.05, "independent": 0.05}},
            "p_0_2": {"probabilities": {"c2_first": 0.82, "c0_first": 0.13, "independent": 0.05}},
        })
        art = assemble({"provider": "s", "answers": {}, "telemetry": {}, "sha256": "x"},
                       dep_res, criteria, [])
        assert art["cycle_breaks"], "C2->C0 at 0.82 (weakest cycle edge) must be dropped"
        assert art["cycle_breaks"][0]["dropped"]["p"] == 0.82
        assert art["order"] == ["C0", "C1", "C2"]


def _artifact(order, dep_probs):
    return {
        "schema": "plan-surface/v1",
        "criteria": [
            {"id": "C0", "text": "engine store", "modules": [{"id": "m001", "path": "src/core", "p": 0.9}], "risks": [], "touches_any": 0.9},
            {"id": "C1", "text": "web render", "modules": [{"id": "m002", "path": "web", "p": 0.9}], "risks": [], "touches_any": 0.9},
            {"id": "C2", "text": "docs pages", "modules": [], "risks": [], "touches_any": 0.2},
        ],
        "edges": [], "uncertain": [], "order": order, "cycle_breaks": [],
        "thresholds": {"map_floor": 0.3, "map_top": 3, "dep": 0.8, "band": 0.15},
        "meta": {"dep_probs": dep_probs},
    }


COMMITS = [
    {"sha": "a1", "ts": "2026-10-01T10:00:00+00:00", "message": "feat: engine store core", "files": ["src/core/store.py"]},
    {"sha": "a2", "ts": "2026-10-01T11:00:00+00:00", "message": "feat: web render page", "files": ["web/page.tsx"]},
    {"sha": "a3", "ts": "2026-10-01T12:00:00+00:00", "message": "docs: record plan", "files": ["docs/plans/x.md"]},
    {"sha": "a4", "ts": "2026-10-01T13:00:00+00:00", "message": "feat: web render polish", "files": ["web/more.tsx"]},
]


class TestAttribution:
    def test_heads_and_chunking(self):
        commits = [{"sha": f"s{i}", "message": f"commit {i}", "files": ["a.py"]} for i in range(20)]
        criteria = ["alpha work", "beta work"]
        tasks, state = build_attribution_task(commits, criteria)
        assert [len(t.heads) for t in tasks] == [14, 6]
        head = tasks[0].heads[0]
        assert head.labels == ["C0", "C1", "none"]
        assert "alpha work" in head.descriptions["C0"]
        assert "C0: alpha work" in state

    def test_layer_pick_overrides_score(self):
        art = _artifact(["C0", "C1"], {
            "0,1": {"c0_first": 0.9, "c1_first": 0.05, "independent": 0.05},
        })
        # scoring alone would map both web commits to C1; the layer disagrees
        rec = reconcile(art, COMMITS, attributed={"a2": "C0", "a4": "C0"})
        by_sha = {c["sha"]: c for c in rec["mapping"]}
        assert by_sha["a2"]["source"] == "layer" and by_sha["a2"]["criterion"] == "C0"
        assert by_sha["a4"]["source"] == "layer" and by_sha["a4"]["criterion"] == "C0"
        assert by_sha["a1"]["source"] == "score"

    def test_none_and_unknown_picks_fall_through(self):
        art = _artifact(["C0", "C1"], {})
        rec = reconcile(art, COMMITS, attributed={"a1": "none", "a2": "C9"})
        by_sha = {c["sha"]: c for c in rec["mapping"]}
        assert by_sha["a1"]["source"] == "score"
        assert by_sha["a2"]["source"] == "score"


class TestReconcile:
    def test_agrees(self):
        art = _artifact(["C0", "C1"], {
            "0,1": {"c0_first": 0.9, "c1_first": 0.05, "independent": 0.05},
            "1,2": {"c1_first": 0.7, "c2_first": 0.2, "independent": 0.1},
        })
        rec = reconcile(art, COMMITS)
        assert rec["commits_used"] == 3  # docs commit skipped
        assert rec["actual_order"] == ["C0", "C1"]
        assert rec["tau"] == 1.0
        assert rec["at_threshold"]["direction_precision"] == 1.0
        assert rec["at_threshold"]["adjacent_coverage"] == 1.0  # C0->C1 transition covered
        assert rec["criteria_unimplemented"] == ["C2"]

    def test_disagrees_on_order(self):
        art = _artifact(["C1", "C0"], {
            "0,1": {"c1_first": 0.9, "c0_first": 0.05, "independent": 0.05},
        })
        rec = reconcile(art, COMMITS)
        assert rec["tau"] == -1.0
        assert rec["at_threshold"]["direction_precision"] == 0.0

    def test_threshold_sweep_moves_coverage(self):
        dep_probs = {
            "0,1": {"c0_first": 0.9, "c1_first": 0.05, "independent": 0.05},
            "0,2": {"c0_first": 0.55, "c2_first": 0.3, "independent": 0.15},
        }
        rec = reconcile(_artifact(["C0", "C1"], dep_probs), COMMITS)
        at_50 = next(s for s in rec["sweep"] if s["threshold"] == 0.5)
        at_90 = next(s for s in rec["sweep"] if s["threshold"] == 0.9)
        assert at_50["n_edges"] > at_90["n_edges"]

    def test_tau_handles_disjoint(self):
        assert kendall_tau(["C0"], ["C0"]) is None
        assert kendall_tau(["C0", "C1"], ["C1", "C0"]) == -1.0


# ── wiring through sys1.classify with a stub provider ────────────────────────

STUB = "ps_stub"
STUB_ANSWERS: dict[str, dict] = {
    "c0_modules": {"type": "choice", "label": "m001", "confidence": 0.9,
                   "probabilities": {"m001": 0.9, "m002": 0.6}},
    "c0_any": {"type": "noul", "noul": 0.92},
    "c1_modules": {"type": "choice", "label": "m002", "confidence": 0.8,
                   "probabilities": {"m001": 0.3, "m002": 0.8}},
    "c1_any": {"type": "noul", "noul": 0.85},
    "p_0_1": {"type": "choice", "label": "c0_first", "confidence": 0.85,
              "probabilities": {"c0_first": 0.85, "c1_first": 0.05, "independent": 0.1}},
}


def _stub_classify(text, heads, cfg, tele):
    return {h.id: dict(STUB_ANSWERS[h.id]) for h in heads if h.id in STUB_ANSWERS}


@pytest.fixture
def ps_stub(cfg):
    provider = Provider(id=STUB, name="plan-surface stub", classify=_stub_classify,
                        is_available=lambda c: True)
    sys1.register(provider)
    yield provider
    sys1.REGISTRY.pop(STUB, None)


class TestRunWiring:
    def test_map_and_deps_round_trip(self, cfg, ps_stub):
        criteria = parse_criteria(PLAN_MD)[:2]
        modules = [{"id": "m001", "path": "src", "n_files": 4, "langs": "py"},
                   {"id": "m002", "path": "web", "n_files": 2, "langs": "ts"}]

        map_res = run_map(criteria, modules, provider=STUB, cfg=cfg)
        assert map_res["provider"] == STUB
        assert "c0_modules" in map_res["answers"]

        dep_res = run_deps(criteria, provider=STUB, cfg=cfg)
        assert dep_res["answers"]["p_0_1"]["label"] == "c0_first"
        assert len(dep_res["sha256s"]) == 1

        art = assemble(map_res, dep_res, criteria, modules)
        assert art["order"] == ["C0", "C1"]
        assert art["edges"][0]["before"] == "C0"
        assert art["meta"]["provider_map"] == STUB

    def test_map_falls_back_through_chain(self, cfg, ps_stub):
        """A dead provider in the chain falls through to the stub."""
        criteria = parse_criteria(PLAN_MD)[:2]
        modules = [{"id": "m001", "path": "src", "n_files": 4, "langs": "py"}]
        map_res = run_map(criteria, modules, provider=["nope_missing", STUB], cfg=cfg)
        assert map_res["provider"] == STUB
        assert map_res["answers"]


class TestGradeAndRecord:
    def test_writes_feedback_rows(self, cfg, ps_stub):
        import json
        from sys1.plansurface import run_map

        criteria = parse_criteria(PLAN_MD)[:2]
        modules = [{"id": "m001", "path": "src", "n_files": 4, "langs": "py"}]
        map_res = run_map(criteria, modules, provider=STUB, cfg=cfg)
        dep_res = run_deps(criteria, provider=STUB, cfg=cfg)
        art = assemble(map_res, dep_res, criteria, modules)

        commits = [
            {"sha": "b1", "ts": "2026-10-01T10:00:00+00:00", "message": "feat: src work",
             "files": ["src/store.py"]},
            {"sha": "b2", "ts": "2026-10-01T11:00:00+00:00", "message": "feat: web work",
             "files": ["web/page.tsx"]},
        ]
        graded = grade_and_record(art, commits, map_res, dep_res, cfg=cfg)
        assert graded["feedback_rows"] >= 2  # c0_any + c1_any at minimum; deps pairs when both land
        rec = graded["reconcile"]
        assert rec["actual_order"]

        # rows landed in the pinned log dir under op=feedback
        log_files = list(Path(cfg["classify"]["log_dir"]).rglob("events.jsonl"))
        rows = []
        for f in log_files:
            rows += [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        fb = [r for r in rows if r.get("op") == "feedback" and r.get("task") in ("plan_surface_map", "plan_deps")]
        assert len(fb) == graded["feedback_rows"]
        assert all("head_id" in r and "actual" in r for r in fb)

    def test_deps_ground_truth_skips_unimplemented(self, cfg, ps_stub):
        # only one criterion implemented -> its dep pair has no ground truth
        import json
        from sys1.plansurface import run_map

        criteria = parse_criteria(PLAN_MD)[:2]
        modules = [{"id": "m001", "path": "src", "n_files": 4, "langs": "py"}]
        map_res = run_map(criteria, modules, provider=STUB, cfg=cfg)
        dep_res = run_deps(criteria, provider=STUB, cfg=cfg)
        art = assemble(map_res, dep_res, criteria, modules)
        commits = [{"sha": "b1", "ts": "2026-10-01T10:00:00+00:00", "message": "feat: src work",
                    "files": ["src/store.py"]}]
        graded = grade_and_record(art, commits, map_res, dep_res, cfg=cfg)
        assert graded["reconcile"]["criteria_unimplemented"]
        log_files = list(Path(cfg["classify"]["log_dir"]).rglob("events.jsonl"))
        rows = []
        for f in log_files:
            rows += [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        assert all(r.get("task") != "plan_deps" for r in rows if r.get("op") == "feedback")
