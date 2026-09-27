# Examples

Drop-in recipes for the patterns that come up most.

| File | What it shows |
|------|---------------|
| [`custom-task.toml`](custom-task.toml) | A task file with two registered tasks (`security_review`, `doc_quality`) and per-provider head overrides. Load with `sys1.load_tasks_file(...)`. |
| [`git-hook-pre-push.sh`](git-hook-pre-push.sh) | A real git pre-push hook. Diffs the push range, calls `sys1.classify("decide", "pr_gate", ...)`, blocks on escalate. Warn-and-proceed on sys1 failure (gates that break when *the gate is broken* get skipped). |

Both are executable as-is from the repo root after `pip install -e .` or
`PYTHONPATH=src`.

## Other patterns (in the main [README](../README.md))

- `sys1.classify(["decide", "jev"], "issue_triage", body)` — both providers in one call; reconciliation via `sys1.provider_agreement(result.answers)`.
- `sys1.cascade_classify(["decide", "jev"], task, text)` — cheap first, escalate if low confidence.
- `sys1.register(Provider(id="mine", ...))` — add your own model.
- `sys1.record_feedback(...)` + `sys1.calibration_curve(...)` — the feedback loop that turns your JSONL log into a per-bin accuracy table.
