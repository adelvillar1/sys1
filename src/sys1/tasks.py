"""
Task registry.

A Task is a named collection of heads plus (optional) per-provider overrides
for the cases where one provider asks a different set than another (the
existing dev-decisions "change" task: Jev asks 4 questions, Decide/local ask 2).

Eight tasks are pre-registered, mirroring `dev-decisions._TASK_BUILDERS`.
Add your own with `register_task(Task(...))` or load from a TOML/JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import Head, Task, make_choice, make_noul

# Common label sets (kept in one place so all tasks agree).
DIFF_TYPE_LABELS = ["feat", "fix", "refactor", "docs", "test", "chore", "deps", "style"]
RISK_LABELS = ["low", "medium", "high"]
BREAKING_LABELS = ["yes", "no"]
PR_LABELS = ["bug", "feature", "refactor", "docs", "chore", "test", "ci"]
ISSUE_KINDS = ["bug", "feature", "docs", "question", "chore"]
ISSUE_PRIORITIES = ["low", "medium", "high", "critical"]


# ── pre-registered tasks (parity with dev-decisions 0.2.0) ───────────────────

_CHANGE = Task(
    id="change",
    description="Default diff classifier: type + risk (Decide/local) and breaking/PII noul (Jev).",
    heads=[
        make_choice(
            "What type of change is this diff? Choose exactly one category.",
            DIFF_TYPE_LABELS,
            id="diff_type",
            descriptions={label: f"A {label} change." for label in DIFF_TYPE_LABELS},
        ),
        make_choice(
            "What is the risk level of this change? low = safe, routine; medium = some behavior "
            "change; high = affects public APIs, data, auth, or invariants.",
            RISK_LABELS,
            id="risk_tier",
            descriptions={label: f"Risk level {label}." for label in RISK_LABELS},
        ),
    ],
    provider_heads={
        "jev": [
            make_choice(
                "What type of change is this diff? Choose exactly one category.",
                DIFF_TYPE_LABELS,
                id="diff_type",
                descriptions={label: f"A {label} change." for label in DIFF_TYPE_LABELS},
            ),
            make_choice(
                "What is the risk level? low = safe routine; medium = some behavior change; "
                "high = public API, data, auth, or invariant change.",
                RISK_LABELS,
                id="risk_tier",
                descriptions={label: f"Risk level {label}." for label in RISK_LABELS},
            ),
            make_noul(
                "Does this diff introduce a breaking change — removes a public API, changes a "
                "response schema, or requires migration?",
                id="breaking_change",
                descriptions={"yes": "Yes, this is a breaking change.", "no": "No breaking change."},
            ),
            make_noul(
                "Does the diff add or modify code that handles personal data (PII, student "
                "records, health, financial)?",
                id="personal_data",
                descriptions={"yes": "Yes, personal data is involved.", "no": "No personal data."},
            ),
        ],
    },
)

_COMMIT_AUDIT = Task(
    id="commit_audit",
    description="Does the commit message accurately describe the diff?",
    heads=[
        make_choice(
            "How accurately does the commit message describe the diff? accurate = fully describes; "
            "partial = partially describes; misleading = contradicts or omits major changes.",
            ["accurate", "partial", "misleading"],
            id="message_accuracy",
            descriptions={
                "accurate": "Fully describes the diff.",
                "partial": "Partially describes the diff.",
                "misleading": "Contradicts or omits major changes.",
            },
        ),
    ],
    provider_heads={
        "decide": [
            make_choice(
                "How accurately does the commit message describe the diff? accurate = fully "
                "describes; partial = partially describes; misleading = contradicts or omits major "
                "changes.",
                ["accurate", "partial", "misleading"],
                id="message_accuracy",
            ),
            make_noul(
                "Does the commit message mention every major functional change in the diff?",
                id="message_complete",
            ),
        ],
        "jev": [
            make_choice(
                "How accurately does the commit message describe the diff?",
                ["accurate", "partial", "misleading"],
                id="message_accuracy",
                descriptions={
                    "accurate": "Fully describes the diff.",
                    "partial": "Partially describes the diff.",
                    "misleading": "Contradicts or omits major changes.",
                },
            ),
            make_noul(
                "Does the commit message mention every major functional change in the diff?",
                id="message_complete",
                descriptions={
                    "yes": "Yes, all major changes are mentioned.",
                    "no": "No, major changes are missing.",
                },
            ),
        ],
        "local": [
            make_choice(
                "How accurately does the commit message describe the diff?",
                ["accurate", "partial", "misleading"],
                id="message_accuracy",
            ),
        ],
    },
)

_DEPS_RISK = Task(
    id="deps_risk",
    description="Classify dependency-update bump level and risk.",
    heads=[
        make_choice(
            "What is the semver bump level of this dependency update? patch = bug fix; minor = new "
            "feature, backward-compatible; major = breaking change.",
            ["patch", "minor", "major"],
            id="bump_level",
            descriptions={
                "patch": "Patch-level bug fix.",
                "minor": "Minor version, new feature, backward-compatible.",
                "major": "Major version, breaking change.",
            },
        ),
        make_choice(
            "What is the risk level? low = patch or minor in low-traffic path; medium = minor in "
            "core path or major in low-traffic; high = major in core path or affects auth/data.",
            RISK_LABELS,
            id="risk_tier",
            descriptions={"low": "Low risk.", "medium": "Medium risk.", "high": "High risk."},
        ),
    ],
    provider_heads={
        "decide": [
            make_choice(
                "What is the semver bump level of this dependency update?",
                ["patch", "minor", "major"],
                id="bump_level",
            ),
            make_choice(
                "What is the risk level?",
                RISK_LABELS,
                id="risk_tier",
            ),
            make_noul("Does this dependency update introduce a breaking change?", id="breaking_change"),
        ],
        "jev": [
            make_choice(
                "What is the semver bump level? patch = bug fix; minor = new feature, "
                "backward-compatible; major = breaking change.",
                ["patch", "minor", "major"],
                id="bump_level",
                descriptions={
                    "patch": "Patch-level bug fix.",
                    "minor": "Minor version, new feature, backward-compatible.",
                    "major": "Major version, breaking change.",
                },
            ),
            make_choice(
                "What is the risk level?",
                RISK_LABELS,
                id="risk_tier",
                descriptions={"low": "Low risk.", "medium": "Medium risk.", "high": "High risk."},
            ),
            make_noul(
                "Does this dependency update introduce a breaking change?",
                id="breaking_change",
                descriptions={"yes": "Yes, breaking change.", "no": "No breaking change."},
            ),
        ],
        "local": [
            make_choice(
                "What is the semver bump level of this dependency update?",
                ["patch", "minor", "major"],
                id="bump_level",
            ),
            make_choice(
                "What is the risk level?",
                RISK_LABELS,
                id="risk_tier",
            ),
        ],
    },
)

_DOCS_DRIFT = Task(
    id="docs_drift",
    description="Detect docs drift: code changes without doc updates (or vice versa).",
    heads=[
        make_choice(
            "Does this diff include code changes (not just docs)?",
            ["code-only", "docs-only", "mixed"],
            id="change_mix",
            descriptions={
                "code-only": "Only code changes.",
                "docs-only": "Only documentation changes.",
                "mixed": "Both code and docs changes.",
            },
        ),
    ],
    provider_heads={
        "decide": [
            make_choice(
                "Does this diff include code changes (not just docs)?",
                ["code-only", "docs-only", "mixed"],
                id="change_mix",
            ),
            make_noul(
                "Is there documentation drift — code changed without corresponding doc updates, or "
                "docs updated without code changes?",
                id="docs_drift_flag",
            ),
        ],
        "jev": [
            make_choice(
                "Does this diff include code changes (not just docs)?",
                ["code-only", "docs-only", "mixed"],
                id="change_mix",
                descriptions={
                    "code-only": "Only code changes.",
                    "docs-only": "Only documentation changes.",
                    "mixed": "Both code and docs changes.",
                },
            ),
            make_noul(
                "Is there documentation drift — code changed without corresponding doc updates?",
                id="docs_drift_flag",
                descriptions={"yes": "Yes, docs drift detected.", "no": "No docs drift."},
            ),
        ],
        "local": [
            make_choice(
                "Does this diff include code changes (not just docs)?",
                ["code-only", "docs-only", "mixed"],
                id="change_mix",
            ),
            make_choice(
                "Is there documentation drift?",
                ["no-drift", "drift"],
                id="docs_drift_flag",
            ),
        ],
    },
)

_API_DRIFT = Task(
    id="api_drift",
    description="Detect public API contract drift / breaking changes.",
    heads=[
        make_noul(
            "Does this diff modify any public API surface (routes, schemas, exported functions, "
            "types, interfaces, RPC methods)?",
            id="public_api_modified",
        ),
    ],
    provider_heads={
        "decide": [
            make_noul(
                "Does this diff modify any public API surface (routes, schemas, exported "
                "functions, types, interfaces, RPC methods)?",
                id="public_api_modified",
            ),
            make_noul(
                "Does this diff introduce a breaking change to the public API?",
                id="breaking_change",
            ),
            make_choice(
                "If there is a breaking change, how severe is it?",
                ["high", "medium", "low", "none"],
                id="severity",
                descriptions={
                    "high": "High — requires immediate migration.",
                    "medium": "Medium — requires migration but has deprecation path.",
                    "low": "Low — backward-compatible.",
                    "none": "None.",
                },
            ),
        ],
        "jev": [
            make_noul(
                "Does this diff modify any public API surface (routes, schemas, exported "
                "functions, types, interfaces, RPC methods)?",
                id="public_api_modified",
                descriptions={"yes": "Yes, public API modified.", "no": "No public API modified."},
            ),
            make_noul(
                "Does this diff introduce a breaking change to the public API?",
                id="breaking_change",
                descriptions={"yes": "Yes, breaking change.", "no": "No breaking change."},
            ),
            make_choice(
                "If there is a breaking change, how severe is it?",
                ["high", "medium", "low", "none"],
                id="severity",
                descriptions={
                    "high": "High — requires immediate migration.",
                    "medium": "Medium — requires migration but has deprecation path.",
                    "low": "Low — backward-compatible.",
                    "none": "None.",
                },
            ),
        ],
        "local": [
            make_noul(
                "Does this diff modify any public API surface (routes, schemas, exported "
                "functions, types, interfaces, RPC methods)?",
                id="public_api_modified",
            ),
            make_noul(
                "Does this diff introduce a breaking change?",
                id="breaking_change",
            ),
        ],
    },
)

_PR_GATE = Task(
    id="pr_gate",
    description="PR quality gate: change type, risk, suggested labels.",
    heads=[
        make_choice(
            "What type of change is this PR?",
            DIFF_TYPE_LABELS,
            id="diff_type",
        ),
        make_choice(
            "What is the risk level?",
            RISK_LABELS,
            id="risk_tier",
        ),
        make_choice(
            "Which labels should be applied?",
            PR_LABELS,
            id="suggested_labels",
            multi_label=True,
        ),
    ],
    provider_heads={
        "jev": [
            make_choice(
                "What type of change is this PR?",
                DIFF_TYPE_LABELS,
                id="diff_type",
                descriptions={label: f"A {label} change." for label in DIFF_TYPE_LABELS},
            ),
            make_choice(
                "What is the risk level?",
                RISK_LABELS,
                id="risk_tier",
                descriptions={label: f"Risk level {label}." for label in RISK_LABELS},
            ),
            make_choice(
                "Which labels should be applied?",
                PR_LABELS,
                id="suggested_labels",
                descriptions={label: f"Apply {label} label." for label in PR_LABELS},
                multi_label=True,
            ),
        ],
    },
)

_ISSUE_TRIAGE = Task(
    id="issue_triage",
    description="Issue triage: kind and priority.",
    heads=[
        make_choice("What kind of issue is this?", ISSUE_KINDS, id="kind"),
        make_choice("What priority should this issue have?", ISSUE_PRIORITIES, id="priority"),
    ],
    provider_heads={
        "jev": [
            make_choice(
                "What kind of issue is this?",
                ISSUE_KINDS,
                id="kind",
                descriptions={k: f"This is a {k}." for k in ISSUE_KINDS},
            ),
            make_choice(
                "What priority should this issue have?",
                ISSUE_PRIORITIES,
                id="priority",
                descriptions={p: f"Priority is {p}." for p in ISSUE_PRIORITIES},
            ),
        ],
    },
)

_SAFETY = Task(
    id="safety",
    description="Destructive-command safety check.",
    heads=[
        make_noul(
            "Does this command perform a destructive operation (force push, rm -rf, DROP/TRUNCATE, "
            "destructive migration against production, --no-verify bypass)?",
            id="destructive",
        ),
        make_noul("If destructive, is it reversible?", id="reversible"),
    ],
    providers=["decide", "jev", "local"],  # no modernbert (no training set)
    provider_heads={
        "jev": [
            make_noul(
                "Does this command perform a destructive operation (force push, rm -rf, "
                "DROP/TRUNCATE, destructive migration against production, --no-verify bypass)?",
                id="destructive",
                descriptions={"yes": "Yes, this is destructive.", "no": "Not destructive."},
            ),
            make_noul(
                "If destructive, is it reversible?",
                id="reversible",
                descriptions={"yes": "Yes, reversible.", "no": "No, irreversible."},
            ),
        ],
    },
)


# ── registry + detection ─────────────────────────────────────────────────────

_TASKS: dict[str, Task] = {}


def register_task(task: Task) -> Task:
    _TASKS[task.id] = task
    return task


for _t in (_CHANGE, _COMMIT_AUDIT, _DEPS_RISK, _DOCS_DRIFT, _API_DRIFT, _PR_GATE, _ISSUE_TRIAGE, _SAFETY):
    register_task(_t)


def get_task(name: str) -> Task | None:
    return _TASKS.get(name)


def list_tasks() -> list[str]:
    return sorted(_TASKS)


def heads_for(task: Task | str, provider: str) -> list[Head]:
    """Pick the head set for a given provider, falling back to the task default.

    Drex and GLiDE have no per-task overrides of their own: both are Jev-class
    (same noul/choice/score primitives, same calibrated-probability semantics),
    so they inherit the `jev` head set wherever one is defined. Tasks that
    never gave jev its own heads fall through to the task default for all three.
    """
    t = _TASKS.get(task) if isinstance(task, str) else task
    if t is None:
        raise KeyError(f"unknown task {task!r}; known: {sorted(_TASKS)}")
    if provider in ("drex", "glide", "julia") and provider not in t.provider_heads:
        provider = "jev"
    return t.provider_heads.get(provider, t.heads)


# Heuristic: which task to run for an arbitrary diff (ported from dev-decisions)
def detect_task_from_text(text: str, default: str = "change") -> Task | None:
    lowered = text.lower()
    dep_patterns = [
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "requirements.txt",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
        "gemfile",
        "pom.xml",
    ]
    if any(p in lowered for p in dep_patterns):
        return _TASKS.get("deps_risk")
    doc_patterns = [".md", ".rst", ".txt", "docs/", "documentation/"]
    if any(p in lowered for p in doc_patterns):
        return _TASKS.get("docs_drift")
    return _TASKS.get(default)


# ── external task file loading (TOML or JSON) ────────────────────────────────


def task_from_spec(spec: dict) -> Task:
    """
    Build a Task from a raw spec dict (the `[[tasks]]` shape in TOML/JSON task
    files). Pure — no file IO, no registration. `load_tasks_file` uses this
    for every task in a file; the HTTP service uses it for inline `task_spec`
    requests so callers (e.g. the zcode-router judge) can send dynamic heads.
    """
    head_objs = [_head_from_spec(spec_dict) for spec_dict in spec.get("heads", [])]
    provider_heads = {
        prov: [_head_from_spec(h) for h in heads] for prov, heads in (spec.get("provider_heads") or {}).items()
    }
    return Task(
        id=spec["id"],
        heads=head_objs,
        description=spec.get("description", ""),
        providers=spec.get("providers"),
        confidence_floor=spec.get("confidence_floor"),
        metadata=spec.get("metadata", {}),
        provider_heads=provider_heads,
    )


def load_tasks_file(path: str | Path) -> list[Task]:
    """Parse a TOML or JSON task file and register each task. Returns the parsed list."""
    p = Path(path).expanduser()
    data: dict[str, Any]
    if p.suffix == ".toml":
        import tomllib

        with open(p, "rb") as f:
            data = tomllib.load(f)
    else:
        with open(p) as f:
            data = json.load(f)
    tasks: list[Task] = []
    for spec in data.get("tasks", []):
        t = task_from_spec(spec)
        register_task(t)
        tasks.append(t)
    return tasks


def _head_from_spec(spec: dict) -> Head:
    kind = spec.get("kind", "choice")
    floor = spec.get("floor")
    if kind == "noul":
        return make_noul(
            spec["task"],
            id=spec.get("id"),
            descriptions=spec.get("descriptions"),
            floor=floor,
        )
    if kind == "score":
        from .types import make_score

        return make_score(
            spec["task"],
            id=spec.get("id"),
            score_min=spec.get("score_min", 1),
            score_max=spec.get("score_max", 5),
            legend=spec.get("legend"),
            floor=floor,
        )
    return make_choice(
        spec["task"],
        spec.get("labels", []),
        id=spec.get("id"),
        descriptions=spec.get("descriptions"),
        multi_label=spec.get("multi_label", False),
        floor=floor,
    )
