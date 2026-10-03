"""
Plan-surface: layered sys1 precompute for task decomposition.

A drafted plan's acceptance criteria are judged over the repo surface by two
sys1 layers, and plain code assembles the answers into a judgment graph:

  Layer 1  criterion -> surface mapping.  One choice head per criterion over
          a numbered module inventory (semantic-find shape: ids in the state,
          criterion text rides in the head question) plus an existence noul
          per criterion. Mapping reads the choice probabilities, so it works
          whether or not the provider honors multi-label semantics.
  Layer 2  pairwise dependency.  One choice head per unordered criterion
          pair: "cI_first" / "cJ_first" / "independent". Directional by
          construction, one head per pair, chunked across requests at the
          pr_gate_fanout sizing (glide times out on larger multi-head
          requests; deps default-route to jev for the same reason).
  Layer 3  assembly, no model.  Threshold the edges, topological order with
          weakest-edge cycle breaks, path-pattern risk flags. Edges inside
          the threshold band are reported as uncertain — never silently
          dropped; that is the over-serialization guard.

`reconcile` grades a surface against what actually happened: the commit
sequence after the plan was written is ground truth for both order (Kendall
tau) and edge direction, swept across thresholds. Everything here is pure
except `load_commits` (subprocess git) and the `run_*` wrappers around
`sys1.classify`, so the whole graph layer is testable offline.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

from .api import classify
from .config import load_config
from .routing import route_decision
from .types import Task, make_choice, make_noul

MAX_CRITERIA = 12
MAX_MODULES = 60
PAIRS_PER_REQUEST = 14  # pr_gate_fanout rode 19 heads on jev; glide timed out. Stay under.
MAP_FLOOR = 0.3   # recall-oriented: top-3 modules above this floor; consumers apply precision
MAP_TOP = 3
# Probe (2026-10-02, control-tower corpus): direction precision 1.0 at t >= 0.8
# vs 0.5-0.67 at 0.65 — keep the band for the soft middle, default the cut high.
DEP_THRESHOLD = 0.8
DEP_BAND = 0.15  # [thr - band, thr) => uncertain, reported not dropped

RISK_PATTERNS = ("migrat", "schema", "auth", "secret", "payment", "config", "deploy", "delete")

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "docs",
    "coverage", ".next", ".cache", "tmp", "logs", "infographic", "fixtures", "tests",
}
CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".toml", ".yaml",
    ".yml", ".sql", ".sh", ".rb", ".go", ".rs", ".java", ".kt", ".swift", ".css",
    ".scss", ".html", ".vue", ".svelte",
}
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "then",
    "should", "must", "been", "have", "will", "each", "them", "they", "their",
    "than", "more", "over", "under", "between", "within", "without", "also",
}


# ── mechanical parsing ────────────────────────────────────────────────────────


def parse_criteria(plan_text: str) -> list[str]:
    """Checkbox criteria from a draft-feature-plan markdown body."""
    out: list[str] = []
    for line in plan_text.splitlines():
        m = re.match(r"^\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*$", line)
        if m:
            out.append(m.group(1))
    return out[:MAX_CRITERIA]


def inventory_surface(repo_root: str | Path, max_modules: int = MAX_MODULES) -> list[dict]:
    """
    Numbered code-surface inventory: top-level dirs (with one level of
    expansion into busy ones) plus top-level code files. Mechanical; the
    sys1 layer only ever sees ids in the state and picks ids back.
    """
    root = Path(repo_root)

    def code_stats(dirpath: Path) -> tuple[int, Counter]:
        n, exts = 0, Counter()
        for _walk_dir, dirnames, filenames in os.walk(dirpath):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                ext = Path(fn).suffix.lower()
                if ext in CODE_EXTS:
                    n += 1
                    exts[ext] += 1
        return n, exts

    entries: list[tuple[str, int, Counter]] = []
    for child in sorted(root.iterdir(), key=str):
        if child.name in SKIP_DIRS or child.name.startswith("."):
            continue
        if child.is_dir():
            n, exts = code_stats(child)
            if not n:
                continue
            entries.append((child.name, n, exts))
            if n > 12:  # busy top-level dir: surface its immediate subdirs too
                for sub in sorted(child.iterdir(), key=str):
                    if sub.name in SKIP_DIRS or sub.name.startswith(".") or not sub.is_dir():
                        continue
                    sn, sexts = code_stats(sub)
                    if sn:
                        entries.append((f"{child.name}/{sub.name}", sn, sexts))
        elif child.suffix.lower() in CODE_EXTS:
            entries.append((child.name, 1, Counter([child.suffix.lower()])))

    return [
        {
            "id": f"m{i + 1:03d}",
            "path": path,
            "n_files": n,
            "langs": ",".join(ext for ext, _ in exts.most_common(3)),
        }
        for i, (path, n, exts) in enumerate(entries[:max_modules])
    ]


def _words(text: str) -> set[str]:
    return {
        w for w in re.split(r"[^a-z0-9]+", text.lower())
        if len(w) >= 4 and w not in _STOPWORDS
    }


# ── layer 1: criterion -> surface mapping ────────────────────────────────────


def build_map_task(criteria: list[str], modules: list[dict]) -> tuple[Task, str]:
    """One request: per-criterion choice over module ids + existence noul."""
    mod_ids = [m["id"] for m in modules]
    heads = []
    for i, c in enumerate(criteria):
        heads.append(make_choice(
            f"Which modules of the repository does acceptance criterion C{i} touch? Criterion: {c}",
            mod_ids,
            id=f"c{i}_modules",
            multi_label=True,
        ))
        heads.append(make_noul(
            f"Does any module in the inventory answer acceptance criterion C{i}? Criterion: {c}",
            id=f"c{i}_any",
        ))
    lines = [f"Repository module inventory ({len(modules)} entries, ids m001..m{len(modules):03d}):"]
    lines += [f"{m['id']} = {m['path']} ({m['n_files']} code files: {m['langs']})" for m in modules]
    state = "\n".join(lines)
    task = Task(
        id="plan_surface_map",
        heads=heads,
        description="Criterion -> repository-surface mapping (choice + existence noul per criterion)",
    )
    return task, state


# ── layer 2: pairwise dependency ─────────────────────────────────────────────


def dep_pair_heads(criteria: list[str]) -> list:
    heads = []
    for i in range(len(criteria)):
        for j in range(i + 1, len(criteria)):
            heads.append(make_choice(
                f"Implementation ordering for two acceptance criteria of one plan: "
                f"C{i} \"{criteria[i]}\" and C{j} \"{criteria[j]}\". "
                f"Which must exist in the codebase first?",
                [f"c{i}_first", f"c{j}_first", "independent"],
                id=f"p_{i}_{j}",
                descriptions={
                    f"c{i}_first": f"C{i} is a prerequisite of C{j}: C{j} builds on C{i}.",
                    f"c{j}_first": f"C{j} is a prerequisite of C{i}: C{i} builds on C{j}.",
                    "independent": "No required ordering between them.",
                },
            ))
    return heads


def build_dep_tasks(criteria: list[str], per_request: int = PAIRS_PER_REQUEST) -> tuple[list[Task], str]:
    heads = dep_pair_heads(criteria)
    listing = "\n".join(f"C{i}: {c}" for i, c in enumerate(criteria))
    state = f"A plan with {len(criteria)} acceptance criteria:\n{listing}"
    chunks = [heads[k:k + per_request] for k in range(0, len(heads), per_request)]
    tasks = [
        Task(id="plan_deps", heads=chunk, description="Pairwise criterion ordering (one choice per unordered pair)")
        for chunk in chunks
    ]
    return tasks, state


# ── probe-only layer: commit -> criterion attribution ─────────────────────────


def build_attribution_task(commits: list[dict], criteria: list[str],
                           per_request: int = PAIRS_PER_REQUEST) -> tuple[list[Task], str]:
    """
    Which criterion does each commit implement? One choice head per commit
    over the criterion ids (+ "none"). Probe-side ground truth for repos
    whose implementation concentrates in shared files, where path overlap
    cannot separate criteria.
    """
    crit_ids = [f"C{i}" for i in range(len(criteria))]
    heads = []
    for k, c in enumerate(commits):
        files = ", ".join(c.get("files", [])[:8]) or "(no files listed)"
        heads.append(make_choice(
            f"Which acceptance criterion does commit {k} (sha {c['sha']}) implement? "
            f"Message: \"{c.get('message', '')}\". Files touched: {files}. "
            "Pick 'none' if it implements none of them or is docs/chore.",
            [*crit_ids, "none"],
            id=f"a{k}_crit",
            descriptions={cid: criteria[i] for i, cid in enumerate(crit_ids)} | {"none": "None of the criteria."},
        ))
    parts = [f"A plan with {len(criteria)} acceptance criteria:"]
    parts += [f"C{i}: {c}" for i, c in enumerate(criteria)]
    state = "\n".join(parts)
    chunks = [heads[i:i + per_request] for i in range(0, len(heads), per_request)]
    tasks = [
        Task(id="plan_attribute", heads=chunk, description="Commit -> criterion attribution")
        for chunk in chunks
    ]
    return tasks, state


# ── run wrappers (the only network-touching code) ────────────────────────────


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chain(provider: str | list[str]) -> list[str]:
    if isinstance(provider, list):
        return list(provider)
    return [p.strip() for p in provider.split(",") if p.strip()]


def _resolve_chain(provider: str | list[str], task: Task, state: str,
                   cfg: dict) -> list[str]:
    """"auto" routes through sys1 routing (per-task config overrides apply);
    anything else is an explicit chain spec."""
    if isinstance(provider, str) and provider.strip() == "auto":
        chain, _reason = route_decision(task, state, cfg)
        return chain
    return _chain(provider)


def _first_answers(result, chain: list[str]) -> tuple[str, dict]:
    for pid in chain:
        got = result.answers.get(pid) or {}
        if got:
            return pid, got
    return (chain[0] if chain else ""), {}


def run_map(criteria: list[str], modules: list[dict], provider: str | list[str] = "auto",
            cfg: dict | None = None) -> dict:
    task, state = build_map_task(criteria, modules)
    cfg = cfg or load_config()
    chain = _resolve_chain(provider, task, state, cfg)
    result = classify(chain, task, state, cfg=cfg, log=True, input_sha256=_sha("map:" + state))
    pid, answers = _first_answers(result, chain)
    return {"provider": pid, "answers": answers, "telemetry": result.telemetry.get(pid, {}),
            "sha256": _sha("map:" + state), "chain": chain}


def run_deps(criteria: list[str], provider: str | list[str] = "auto",
             cfg: dict | None = None, per_request: int = PAIRS_PER_REQUEST) -> dict:
    tasks, state = build_dep_tasks(criteria, per_request)
    cfg = cfg or load_config()
    chain = _resolve_chain(provider, tasks[0], state, cfg) if tasks else []
    answers: dict = {}
    shas: list[str] = []
    sha_by_head: dict[str, str] = {}
    pid_used, telemetry = "", {}
    for t_i, task in enumerate(tasks):
        sha = _sha(f"deps{t_i}:{state}")
        shas.append(sha)
        for h in task.heads:
            sha_by_head[h.id] = sha
        result = classify(chain, task, state, cfg=cfg, log=True, input_sha256=sha)
        pid, got = _first_answers(result, chain)
        if pid and not pid_used:
            pid_used, telemetry = pid, result.telemetry.get(pid, {})
        answers.update(got)
    return {"provider": pid_used, "answers": answers, "telemetry": telemetry,
            "sha256": shas[0] if shas else _sha("deps:" + state), "sha256s": shas,
            "sha_by_head": sha_by_head, "chain": chain}


def run_attribution(commits: list[dict], criteria: list[str], provider: str | list[str] = "auto",
                    cfg: dict | None = None, per_request: int = PAIRS_PER_REQUEST) -> dict:
    tasks, state = build_attribution_task(commits, criteria, per_request)
    cfg = cfg or load_config()
    chain = _resolve_chain(provider, tasks[0], state, cfg) if tasks else []
    answers: dict = {}
    sha_by_head: dict[str, str] = {}
    pid_used, telemetry = "", {}
    for t_i, task in enumerate(tasks):
        sha = _sha(f"attr{t_i}:{state}")
        for h in task.heads:
            sha_by_head[h.id] = sha
        result = classify(chain, task, state, cfg=cfg, log=True, input_sha256=sha)
        pid, got = _first_answers(result, chain)
        if pid and not pid_used:
            pid_used, telemetry = pid, result.telemetry.get(pid, {})
        answers.update(got)
    return {"provider": pid_used, "answers": answers, "telemetry": telemetry,
            "sha256": _sha("attr:" + state), "sha_by_head": sha_by_head, "chain": chain}


# ── layer 3: assembly (no model) ─────────────────────────────────────────────


def _topo_order(edges: list[dict], n: int) -> tuple[list[str], list[dict]]:
    live = [dict(e) for e in edges]
    breaks: list[dict] = []
    order: list[int] = []
    while True:
        indeg = {i: 0 for i in range(n)}
        adj: dict[int, list[int]] = defaultdict(list)
        for e in live:
            a, b = int(e["before"][1:]), int(e["after"][1:])
            adj[a].append(b)
            indeg[b] += 1
        remaining = set(range(n))
        local: list[int] = []
        while True:
            ready = sorted(i for i in remaining if indeg[i] == 0)
            if not ready:
                break
            cur = ready[0]
            remaining.discard(cur)
            local.append(cur)
            for nxt in adj[cur]:
                if nxt in remaining:
                    indeg[nxt] -= 1
        if not remaining:
            order = local
            break
        cyc = [e for e in live if int(e["before"][1:]) in remaining and int(e["after"][1:]) in remaining]
        if not cyc:  # defensive: shouldn't happen (Kahn leftover implies a cycle)
            order = local + sorted(remaining)
            break
        weakest = min(cyc, key=lambda e: e["p"])
        live.remove(weakest)
        breaks.append({"dropped": weakest, "reason": "cycle: weakest edge removed"})
    return [f"C{i}" for i in order], breaks


def assemble(map_res: dict, dep_res: dict, criteria: list[str], modules: list[dict], *,
             map_floor: float = MAP_FLOOR, map_top: int = MAP_TOP,
             dep_threshold: float = DEP_THRESHOLD, band: float = DEP_BAND) -> dict:
    mod_by_id = {m["id"]: m for m in modules}
    map_answers, dep_answers = map_res["answers"], dep_res["answers"]

    nodes: list[dict] = []
    for i, c in enumerate(criteria):
        probs = (map_answers.get(f"c{i}_modules") or {}).get("probabilities") or {}
        ranked = sorted(
            ((mid, float(p)) for mid, p in probs.items() if mid in mod_by_id),
            key=lambda t: -t[1],
        )
        picked = [(mid, p) for mid, p in ranked if p >= map_floor][:map_top]
        risks = sorted({
            pat for mid, _ in picked
            for pat in RISK_PATTERNS if pat in mod_by_id[mid]["path"].lower()
        })
        any_p = (map_answers.get(f"c{i}_any") or {}).get("noul")
        nodes.append({
            "id": f"C{i}",
            "text": c,
            "modules": [{"id": mid, "path": mod_by_id[mid]["path"], "p": round(p, 3)} for mid, p in picked],
            "risks": risks,
            "touches_any": round(float(any_p), 3) if isinstance(any_p, (int, float)) else None,
        })

    edges: list[dict] = []
    uncertain: list[dict] = []
    pair_probs: dict[str, dict[str, float]] = {}
    for head_id, ans in dep_answers.items():
        m = re.match(r"^p_(\d+)_(\d+)$", head_id)
        if not m or not isinstance(ans, dict):
            continue
        i, j = int(m.group(1)), int(m.group(2))
        probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}
        if not probs and ans.get("label"):
            probs = {str(ans["label"]): float(ans.get("confidence") or 0.0)}
        if not probs:
            continue
        pair_probs[f"{i},{j}"] = {k: round(v, 3) for k, v in probs.items()}
        top_label, top_p = max(probs.items(), key=lambda kv: kv[1])
        if top_label == "independent":
            if top_p < dep_threshold:
                uncertain.append({"pair": [f"C{i}", f"C{j}"], "reading": "unresolved",
                                  "probs": pair_probs[f"{i},{j}"]})
            continue
        if top_p >= dep_threshold:
            a, b = (i, j) if top_label == f"c{i}_first" else (j, i)
            edges.append({"before": f"C{a}", "after": f"C{b}", "p": round(top_p, 3), "head": head_id})
        elif top_p >= dep_threshold - band:
            uncertain.append({"pair": [f"C{i}", f"C{j}"], "reading": top_label,
                              "probs": pair_probs[f"{i},{j}"]})

    order, cycle_breaks = _topo_order(edges, len(criteria))
    return {
        "schema": "plan-surface/v1",
        "criteria": nodes,
        "edges": edges,
        "uncertain": uncertain,
        "order": order,
        "cycle_breaks": cycle_breaks,
        "thresholds": {"map_floor": map_floor, "map_top": map_top,
                       "dep": dep_threshold, "band": band},
        "meta": {
            "provider_map": map_res.get("provider"),
            "provider_deps": dep_res.get("provider"),
            "sha_map": map_res.get("sha256"),
            "sha_deps": dep_res.get("sha256"),
            "dep_probs": pair_probs,
        },
    }


# ── grading: the commit sequence is ground truth ─────────────────────────────


def load_commits(repo_root: str | Path, since: str | None = None,
                 max_commits: int = 400) -> list[dict]:
    cmd = ["git", "-C", str(repo_root), "log", f"--max-count={max_commits}",
           "--date=iso-strict", "--pretty=format:===%h|%ad|%s", "--name-only"]
    if since:
        cmd.append(f"--since={since}")
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    commits: list[dict] = []
    cur: dict | None = None
    for line in out.splitlines():
        if line.startswith("==="):
            sha, ts, subj = line[3:].split("|", 2)
            cur = {"sha": sha, "ts": ts, "message": subj, "files": []}
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    commits.reverse()  # oldest first
    return commits


def kendall_tau(a: list[str], b: list[str]) -> float | None:
    """Tau between two orderings, over the criteria present in both."""
    common = [x for x in a if x in b]
    n = len(common)
    if n < 2:
        return None
    rank_b = {x: i for i, x in enumerate(b)}
    ra = [rank_b[x] for x in common]
    conc = disc = 0
    for x in range(n):
        for y in range(x + 1, n):
            s = (ra[x] - ra[y]) * (x - y)  # x, y are the ranks in `a`
            if s > 0:
                conc += 1
            elif s < 0:
                disc += 1
    return (conc - disc) / (n * (n - 1) / 2)


def _edges_at(dep_probs: dict[str, dict[str, float]], thr: float) -> dict[str, tuple[str, str, float]]:
    out: dict[str, tuple[str, str, float]] = {}
    for key, probs in dep_probs.items():
        i, j = key.split(",")
        if not probs:
            continue
        label, p = max(probs.items(), key=lambda kv: kv[1])
        if label == "independent" or p < thr:
            continue
        a, b = (i, j) if label == f"c{i}_first" else (j, i)
        out[key] = (f"C{a}", f"C{b}", p)
    return out


def reconcile(artifact: dict, commits: list[dict], *,
              attributed: dict[str, str] | None = None,
              skip_prefixes: tuple[str, ...] = ("docs", "chore")) -> dict:
    """
    Grade a surface against the commit sequence. `commits` is the
    `load_commits` shape (oldest first). Docs/chore commits are skipped by
    message prefix. Each remaining commit maps to a criterion: the optional
    `attributed` layer (sha -> criterion id, from build_attribution_task)
    wins outright; otherwise path overlap with the criterion's mapped modules
    plus a light message-word bonus decides.
    """
    nodes = artifact["criteria"]
    mod_paths = {n["id"]: [m["path"] for m in n["modules"]] for n in nodes}
    crit_words = {n["id"]: _words(n["text"]) for n in nodes}
    crit_ids = {n["id"] for n in nodes}

    used: list[dict] = []
    skipped = 0
    for c in sorted(commits, key=lambda x: x.get("ts") or ""):
        first = (c.get("message") or "").split("(")[0].split(":")[0].strip().lower()
        if first in skip_prefixes:
            skipped += 1
            continue
        best, best_score, source = None, 0.0, "score"
        layer_pick = (attributed or {}).get(c["sha"])
        if layer_pick in crit_ids:
            best, best_score, source = layer_pick, 10.0, "layer"
        if best is None:
            for n in nodes:
                overlap = 0
                for f in c["files"]:
                    for p in mod_paths[n["id"]]:
                        if f == p:
                            overlap += 3  # exact file match beats a broad dir pick
                        elif f.startswith(p + "/"):
                            overlap += 1
                w = sum(1 for t in _words(c.get("message") or "") if t in crit_words[n["id"]])
                score = overlap + w / 10.0
                if score > best_score:
                    best, best_score = n["id"], score
        if best is not None and best_score > 0:
            used.append({"sha": c["sha"], "message": c["message"], "criterion": best,
                         "score": round(best_score, 2), "source": source})

    first_idx: dict[str, int] = {}
    for k, c in enumerate(used):
        first_idx.setdefault(c["criterion"], k)
    actual_order = sorted(first_idx, key=lambda nid: first_idx[nid])
    pos = {nid: i for i, nid in enumerate(actual_order)}

    tau = kendall_tau(artifact["order"], actual_order)

    dep_probs = artifact["meta"].get("dep_probs", {})

    def metrics_at(thr: float) -> dict:
        edges = _edges_at(dep_probs, thr)
        judged = [e for e in edges.values() if e[0] in pos and e[1] in pos]
        ok = sum(1 for before, after, _ in judged if pos[before] < pos[after])
        transitions = []
        for x, y in pairwise(used):
            if x["criterion"] != y["criterion"]:
                transitions.append((x["criterion"], y["criterion"]))
        covered = sum(
            1 for a, b in transitions
            if any({a, b} == {e[0], e[1]} for e in edges.values())
        )
        return {
            "threshold": thr,
            "n_edges": len(edges),
            "direction_precision": round(ok / len(judged), 3) if judged else None,
            "adjacent_coverage": round(covered / len(transitions), 3) if transitions else None,
        }

    sweep = [metrics_at(round(0.50 + 0.05 * k, 2)) for k in range(9)]
    full = metrics_at(artifact["thresholds"]["dep"])
    implemented = set(first_idx)
    return {
        "commits_used": len(used),
        "commits_skipped_prefix": skipped,
        "mapping": used,
        "actual_order": actual_order,
        "predicted_order": artifact["order"],
        "criteria_unimplemented": [n["id"] for n in nodes if n["id"] not in implemented],
        "tau": round(tau, 3) if tau is not None else None,
        "at_threshold": full,
        "sweep": sweep,
    }


# ── grading with calibration rows ────────────────────────────────────────────


def _actual_module(node: dict, files: list[str]) -> str | None:
    """Module of the criterion whose mapped paths take the most commit files."""
    counts: dict[str, int] = {}
    for m in node.get("modules", []):
        p = m["path"]
        counts[m["id"]] = sum(1 for f in files if f == p or f.startswith(p + "/"))
    best = max(counts.items(), key=lambda kv: kv[1]) if counts else None
    return best[0] if best and best[1] > 0 else None


def grade_and_record(artifact: dict, commits: list[dict], map_res: dict, dep_res: dict,
                     *, attributed: dict[str, str] | None = None, cfg: dict | None = None) -> dict:
    """
    Reconcile a surface against the commit sequence AND write the per-head
    feedback rows (plan_surface_map / plan_deps) to the calibration log.
    `commits` is the load_commits shape (oldest first). Shared by the
    probe script and the dev-decisions plan-reconcile command.
    """
    from .telemetry import record_feedback

    rec = reconcile(artifact, commits, attributed=attributed)
    commit_index = {c["sha"]: c for c in commits}
    for c in rec["mapping"]:
        c.setdefault("files", commit_index.get(c["sha"], {}).get("files", []))

    provider_map = artifact["meta"].get("provider_map") or map_res.get("provider", "")
    provider_deps = artifact["meta"].get("provider_deps") or dep_res.get("provider", "")
    by_criterion: dict[str, list[dict]] = {}
    implemented: set[str] = set()
    for c in rec["mapping"]:
        by_criterion.setdefault(c["criterion"], []).append(c)
        implemented.add(c["criterion"])
    n = 0

    for node in artifact["criteria"]:
        i = int(node["id"][1:])
        ans_mod = map_res["answers"].get(f"c{i}_modules") or {}
        if ans_mod:
            files = [f for c in by_criterion.get(node["id"], []) for f in c.get("files", [])]
            if files:
                actual = _actual_module(node, files)
                if actual:
                    record_feedback(
                        input_sha256=map_res["sha256"], task="plan_surface_map",
                        head_id=f"c{i}_modules", predicted_label=ans_mod.get("label"),
                        predicted_confidence=ans_mod.get("confidence"), provider=provider_map,
                        actual_label=actual, escalated=False, cfg=cfg)
                    n += 1
        ans_any = map_res["answers"].get(f"c{i}_any") or {}
        p_any = ans_any.get("noul") if isinstance(ans_any.get("noul"), (int, float)) else None
        if p_any is not None:
            yes = p_any >= 0.5
            record_feedback(
                input_sha256=map_res["sha256"], task="plan_surface_map",
                head_id=f"c{i}_any", predicted_label="yes" if yes else "no",
                predicted_confidence=round(p_any if yes else 1 - p_any, 3),
                provider=provider_map,
                actual_label="yes" if node["id"] in implemented else "no",
                escalated=False, cfg=cfg)
            n += 1

    pos = {nid: k for k, nid in enumerate(rec["actual_order"])}
    for head_id, ans in dep_res["answers"].items():
        m = re.match(r"^p_(\d+)_(\d+)$", head_id)
        if not m or not isinstance(ans, dict):
            continue
        i, j = int(m.group(1)), int(m.group(2))
        ci, cj = f"C{i}", f"C{j}"
        if ci not in pos or cj not in pos:
            continue  # no ground truth while one side never landed
        sha = dep_res.get("sha_by_head", {}).get(head_id, dep_res.get("sha256", ""))
        record_feedback(
            input_sha256=sha, task="plan_deps", head_id=head_id,
            predicted_label=ans.get("label"), predicted_confidence=ans.get("confidence"),
            provider=provider_deps,
            actual_label=f"c{i}_first" if pos[ci] < pos[cj] else f"c{j}_first",
            escalated=False, cfg=cfg)
        n += 1

    return {"reconcile": rec, "feedback_rows": n}
