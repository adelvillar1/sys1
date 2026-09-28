# Examples

Drop-in recipes for the patterns that come up most. Everything here runs from
the repo root with the repo venv (`.venv/bin/python ...`) or after
`pip install -e .`. Provider prerequisites: `sys1 doctor` shows which
providers are usable right now — hosted `decide`/`jev` need their API key env
var set (see `CLAUDE.local.md`), `local` needs the uv-managed venv, and
`modernbert` needs the eval script. Every recipe accepts `--provider`.

## Recipes (ported from systemonemodels.org/recipes)

Each recipe keeps its vendor source so you can read the original. Threshold
numbers in the vendor examples are starting points, not tuned values — fit
them from your own JSONL calibration log (`sys1.record_feedback` +
`sys1.calibration_curve`) before trusting them in production.

| File | Pattern | Source | What to look at |
|------|---------|--------|-----------------|
| [`guardrail-injection.toml`](guardrail-injection.toml) + [`screen_message.py`](screen_message.py) | Prompt-injection screen / LLM guardrails | `/cookbooks/llm_guardrails` | Five hazard Nouls + severity Score fanned out in one call; the two-tier policy (review 0.35 / action 0.70 / severity-block 2.0) lives in the script, not the model — thresholds stay in your code. Fails closed on error. |
| [`citation-check.toml`](citation-check.toml) | Citation check | `/cookbooks/citation_check` | A support Noul at a high-stakes floor (0.85) plus a support-strength Score; a confident "unsupported" still clears the certainty floor and trips the review. |
| [`lead-scoring.py`](lead-scoring.py) | Composite scoring | `/patterns/composite-scoring` | Four Score heads normalized with `(score - min) / (max - min)` and blended with weights declared as code constants. |
| [`rerank-passages.py`](rerank-passages.py) | Passage re-rank | `/cookbooks/rerank_typesafe` | One Score head per passage, all generated and fanned out in a single call; sorts, cuts off, and returns nothing when the best passage is weak. |
| [`taxonomy-walk.py`](taxonomy-walk.py) | Hierarchical cascade | `/cookbooks/hierarchical_classification` | One Choice question per level (not per node), cumulative path probability with depth normalization, and a `head.floor` weak-edge bail that stops the walk at the last good node. |

```bash
# screen a message (fails closed: exit 0 pass / 1 review / 2 block)
.venv/bin/python examples/screen_message.py message.txt

# rank passages against a query
echo '{"query": "how do refunds work", "passages": ["...", "..."]}' \
    | .venv/bin/python examples/rerank-passages.py --top 3

# walk a taxonomy over an issue body
.venv/bin/python examples/taxonomy-walk.py issue.txt

# composite-score a lead
echo '{"title": "VP Eng, 400 seats", "description": "..."}' \
    | .venv/bin/python examples/lead-scoring.py
```

The citation recipe ships as a task file only (there's no policy to run —
the floor *is* the policy), so load it and classify:

```python
import sys1
sys1.load_tasks_file("examples/citation-check.toml")
r = sys1.classify("jev", "citation_check", f"CLAIM: {claim}\nPASSAGE: {passage}")
print(r.verdict, r.head("supported", "jev"))
```

## Custom tasks

| File | What it shows |
|------|---------------|
| [`custom-task.toml`](custom-task.toml) | A task file with two registered tasks (`security_review`, `doc_quality`) and per-provider head overrides. Load with `sys1.load_tasks_file(...)`. |
| [`git-hook-pre-push.sh`](git-hook-pre-push.sh) | A real git pre-push hook. Diffs the push range, calls `sys1.classify("decide", "pr_gate", ...)`, blocks on escalate. Warn-and-proceed on sys1 failure (gates that break when *the gate is broken* get skipped). |

## Other patterns (in the main [README](../README.md))

- `sys1.classify(["decide", "jev"], "issue_triage", body)` — both providers in one call; reconciliation via `sys1.provider_agreement(result.answers)`.
- `sys1.cascade_classify(["decide", "jev"], task, text)` — cheap first, escalate if low confidence.
- `sys1.register(Provider(id="mine", ...))` — add your own model.
- Per-head floors (`Head(..., floor=0.85)` or `floor = 0.85` in a task file) — risk-scale a single head inside one task; the precedence ladder and semantics notes are in [`docs/features/gating-and-recipes.md`](../docs/features/gating-and-recipes.md).
- `sys1.record_feedback(...)` + `sys1.calibration_curve(...)` — the feedback loop that turns your JSONL log into a per-bin accuracy table.
