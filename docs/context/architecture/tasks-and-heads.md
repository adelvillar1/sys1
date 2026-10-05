---
title: Tasks and heads — the question registry and the Head dataclass
status: shipped
sources:
  - src/sys1/types.py
  - src/sys1/tasks.py
  - tests/test_recipe_pack.py
related:
  - architecture/providers-and-flags.md
  - architecture/gating.md
---

# Tasks and heads

`types.py` defines the vocabulary; `tasks.py` owns the named question sets. A `Head` is one question
(kind `choice`, `noul`, or `score`); a `Task` is a named collection of heads plus optional
escalation policy (`providers` allow-list, `confidence_floor`) and per-provider head overrides.

## Heads

`Head.id` is either explicit or derived deterministically via `head_id_from_task` (sha1 prefix).
Constructors: `make_choice` (labels, optional rubric `descriptions`, `multi_label`), `make_noul`
(yes/no with `descriptions` defaulting to derived sentences), `make_score` (levels `score_min`..
`score_max` rendered as string labels; a `legend` must match the level count exactly or it fails
loudly). Every head can carry a per-head `floor` — see
[gating.md](gating.md) for where it lands in the precedence ladder.

Wire mappers on `Head`: `to_decide_head` (flat task+labels; descriptions ignored on hosted Decide),
`to_jev_head` (instructions/criteria; score criteria is an **ordered level array**, positions are
the scale — verified against the TypeSafe docs), `to_drex_head` (noul criteria keys must be
`true`/`false`; any other key is ignored server-side), and `wire_head_for` dispatch.

## Tasks

Nine tasks are pre-registered: eight mirror dev-decisions' `_TASK_BUILDERS` (`change`,
`commit_audit`, `deps_risk`, `docs_drift`, `api_drift`, `pr_gate`, `issue_triage`, `safety`),
and `vision_qc` is sys1's own — the liquid-only artifact-QC task (`matches_brief` choice;
`garbled_text` true=defect, `on_palette`/`legible_small` false=defect nouls). Its
`providers=["liquid"]` is the same allow-list mechanism as `safety`, inverted: no text-only
provider may answer heads that only pixels can settle. `provider_heads`
exists because providers ask different sets for the same task (`change`: jev gets breaking/PII
nouls, decide/local get two choice heads). `heads_for(task, provider)` picks the set; **drex,
glide, julia, and liquid inherit the `jev` set** wherever one is defined.

`detect_task_from_text` is the ported heuristic for arbitrary diffs (lockfiles → deps_risk, docs
paths → docs_drift, else the default task).

## External task files

`load_tasks_file` registers tasks from TOML/JSON; `task_from_spec` is the pure spec→Task builder
(no IO, no registration) and is also used by the HTTP service for inline `task_spec` requests so
callers (e.g. the zcode-router judge) can send dynamic heads. `_head_from_spec` maps the
`kind: noul|score|choice` spec dicts, including per-head `floor`.

## Gotchas

- Results dict keys are always `head.id` — the legacy dev-decisions `task[:40]` truncation is gone.
- `Task.providers` is a hard filter (e.g. `safety` excludes modernbert); routing and
  `_resolve_providers` both honor it.
- A missing provider answer becomes `declined(reason)` with `confidence: 0.0` so gating treats it
  as escalate-worthy — see `types.declined`.
