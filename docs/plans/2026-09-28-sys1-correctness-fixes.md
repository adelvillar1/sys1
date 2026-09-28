---
status: active
created: 2026-09-28
updated: 2026-09-28
slug: sys1-correctness-fixes
---

# Plan: sys1 correctness fixes

## Context

The 2026-09-28 in-depth review of sys1 confirmed 6 defects, 2 of which are breaking in production use:

1. `noul` answers from non-Jev providers are discarded — always escalates, poisoning `safety`, `api_drift`, `commit_audit`, `docs_drift`, and `deps_risk` whenever decide/local/candidates serve those heads.
2. `/v1/calibration` is broken for any task with more than one head — `_answers_by_provider` returns lists instead of dicts, so the histogram is permanently empty and `provider_agreement` raises `AttributeError` (HTTP 500) on tasks with 6+ heads.
3. `block_on_classification` never produces a `block` verdict — `interpret()` only returns `pass`/`escalate`, so the CLI exit code is always 1, never 2.
4. `Task.confidence_floor` is dead config — declared and parsed from task files, but never read by `gating._floors_for`.
5. Cascade floor check counts meta keys (`_unmatched:*`) as heads, so cascades over-escalate whenever a provider returns any extra key.
6. `structured_ok` is always `True` on local/modernbert parse failure — the JSON-decode failure sets it to `False`, then the function unconditionally overwrites it with `True` three lines later.

Additionally, 34 ruff errors remain (E741 ambiguous `l` loop vars, F401 unused imports, B905 `zip()` without `strict=`, SIM117 combinable `with` statements).

</add-when-implemented>

## Approach

One patch set, ordered by dependency:

**Patch A — parsers + api (defects 1, 5)**
- In `_as_answer()` (`parsers.py`), when `kind == "noul"` and `value.get("noul") is None`, fall back to `value.get("label")`. Coerce `"yes"`/`"no"` strings to `True`/`False` when the head is `kind=noul` so Jev-shape and Decide-shape answers converge.
- In `cascade_classify` (`api.py`), filter out keys starting with `_` before running `conf_ok`/`labels_ok` checks — same guard `interpret()` already uses.

**Patch B — service (defect 2)**
- In `_answers_by_provider` (`service/main.py`), flatten the per-head answer lists into a single dict before returning, matching the shape `confidence_histogram` and `provider_agreement` expect.
- Fix `"records": len(answers)` to count actual rows, not head ids.

**Patch C — gating + api (defects 3, 4)**
- Add a `Task.confidence_floor` lookup in `_floors_for()` (`gating.py`) so per-task floors override the global floor.
- Add a `block` verdict to `interpret()` when `cfg["classify"]["block_on_classification"]` is true and the result would otherwise be `escalate`.

**Patch D — providers (defect 6)**
- In both `_gliner_wire` and `_modernbert_classify` (`providers.py`), remove the unconditional `telemetry["structured_ok"] = True` that follows the JSON-decode failure branch.

**Patch E — lint**
- Run `ruff check . --fix` to auto-resolve 13 fixable errors (F401, SIM117, I001, UP035, RUF100).
- Manually fix the 8 E741 ambiguous `l` loop vars in `tasks.py`/`types.py` by renaming to `label_str`.
- Manually fix B905 in `make_score` by adding `strict=True` to the `zip()`.

High-level steps:

1. Patch A: parsers + api (defects 1, 5) — one commit
2. Patch B: service calibration aggregation (defect 2) — one commit
3. Patch C: gating block verdict + Task.confidence_floor (defects 3, 4) — one commit
4. Patch D: providers structured_ok telemetry (defect 6) — one commit
5. Patch E: lint cleanup — one commit
6. Run full test suite + add regression tests for each defect
7. Update `docs/STATE-SNAPSHOT.md` and `CLAUDE.md` "Today's state"

## Acceptance criteria

These are the contract. Each one is a verifiable assertion. The work is "done" when every box is checked.

- [ ] A `noul` head served by decide/local/candidates with a `{"label": "no", "confidence": 0.93}` answer produces `verdict: "pass"` (not escalate) when confidence ≥ floor.
- [ ] `safety` task with `providers = ["decide"]` passes when the provider returns `destructive=no, reversible=yes` with confidence ≥ 0.7.
- [ ] `/v1/calibration` returns HTTP 200 with a non-empty `providers[pid].histogram` array when the log contains classify records for a 2-head task.
- [ ] `/v1/calibration` returns HTTP 200 (not 500) when the log contains records for a task with 6+ heads.
- [ ] `classify()` with `block_on_classification=True` and a below-floor answer returns `verdict: "block"`.
- [ ] `final_exit_code(escalated=True, cfg with block_on_classification=True)` returns `2`.
- [ ] A `Task` with `confidence_floor=0.99` and a 0.80 answer returns `verdict: "escalate"`.
- [ ] `cascade_classify` stops after the first provider when all real heads clear the floor, even if the provider also returned `_unmatched:*` junk keys.
- [ ] Local/modernbert providers with malformed subprocess output log `structured_ok: False, error_kind: "json"`.
- [ ] `ruff check .` exits 0 (34 errors resolved).
- [ ] Full test suite passes (`pytest -q`).
- [ ] No existing test is broken by the changes.

## Files to be touched

**Library core:**
- `src/sys1/parsers.py` — `_as_answer`: noul fallback to `label`, coerce yes/no strings to bool
- `src/sys1/api.py` — `cascade_classify`: skip `_`-prefixed keys in floor check
- `src/sys1/gating.py` — `interpret`: emit `"block"` verdict when configured; `_floors_for`: read `Task.confidence_floor`
- `src/sys1/providers.py` — `_gliner_wire`, `_modernbert_classify`: fix `structured_ok` on parse failure

**Service:**
- `service/main.py` — `_answers_by_provider`: flatten lists to dicts; fix records count

**Tests:**
- `tests/test_parsers.py` — add noul-from-label regression tests for decide/local/modernbert shapes
- `tests/test_gating.py` — add block verdict test, Task.confidence_floor test
- `tests/test_api.py` — add cascade-with-junk-keys test
- `tests/test_providers.py` — add structured_ok=False on malformed subprocess output
- `tests/test_service.py` — add calibration endpoint tests for 2-head and 6-head tasks with real histogram/agreement assertions

**Documentation:**
- `docs/STATE-SNAPSHOT.md` — update coverage, ruff count, defect count
- `CLAUDE.md` — update "Today's state"

## Out of scope

Things people might assume are part of this work but aren't:

- Adding new providers or wire families.
- Changing the JSONL log schema (only fixing bugs in existing keys).
- Tuning confidence floor defaults — the existing `_DEFAULT_FLOORS` values are left as-is.
- Migrating the dashboard to a framework — the HTML/JS stays as-is.
- Addressing the latent `task[:40]` prefix-collision ambiguity (zero collisions in shipped tasks; defer to a future plan if a user reports one).

## Verification

How to confirm each criterion is met.

**Criterion 1: noul normalization**
```bash
.venv/bin/python -c "
import sys1
from sys1.types import Provider, make_noul
noul = make_noul('PII?', id='pii')
prov = Provider(id='p', name='p', classify=lambda t,h,c,te: {'pii': {'label':'no','confidence':0.93}}, is_available=lambda c: True)
sys1.register(prov)
r = sys1.classify('p', sys1.Task(id='t', heads=[noul]), 'x', cfg=sys1.load_config(), log=False)
print(r.verdict, r.escalated)
assert r.verdict == 'pass', f'got {r.verdict}'
"
```
Expected: `pass False`

**Criterion 2: safety task passes with decide/local**
Same command as criterion 1 but with `task='safety'`, provider returns `destructive=no, reversible=yes`.

**Criterion 3: calibration histogram non-empty**
```bash
# Start service, run classify, hit /v1/calibration
.venv/bin/python -m uvicorn service.main:app --port 8400 &
curl -s http://127.0.0.1:8400/v1/calibration?days=1 | python -m json.tool
```
Expected: `providers[pid].histogram` contains non-zero buckets.

**Criterion 4: calibration 200 on 6+ heads**
Same as criterion 3 but with a 6-head task. Expected: HTTP 200, no `AttributeError` in server logs.

**Criterion 5: block verdict**
```bash
.venv/bin/python -c "
import sys1
from sys1.types import Provider, make_choice
cfg = sys1.load_config()
cfg['classify']['block_on_classification'] = True
p = Provider(id='p', name='p', classify=lambda t,h,c,te: {'h': {'label':'a','confidence':0.2}}, is_available=lambda c: True)
sys1.register(p)
r = sys1.classify('p', sys1.Task(id='t', heads=[make_choice('q',['a','b'], id='h')]), 'x', cfg=cfg, log=False)
print(r.verdict)
assert r.verdict == 'block'
"
```
Expected: `block`

**Criterion 6: exit code 2**
```bash
echo "x" | sys1 classify --provider fake --task safety --no-log; echo "exit=$?"
```
With `block_on_classification=True` and a below-floor answer. Expected: `exit=2`.

**Criterion 7: Task.confidence_floor**
```bash
.venv/bin/python -c "
import sys1
from sys1.types import Provider, make_choice
t = sys1.Task(id='t', heads=[make_choice('q',['a','b'], id='h')], confidence_floor=0.99)
p = Provider(id='p', name='p', classify=lambda t,h,c,te: {'h': {'label':'a','confidence':0.80}}, is_available=lambda c: True)
sys1.register(p)
r = sys1.classify('p', t, 'x', cfg=sys1.load_config(), log=False)
print(r.verdict)
assert r.verdict == 'escalate'
"
```
Expected: `escalate`

**Criterion 8: cascade ignores junk keys**
```bash
.venv/bin/python -c "
import sys1
from sys1.types import Provider, make_choice
p = Provider(id='p', name='p',
  classify=lambda t,h,c,te: {'h': {'label':'a','confidence':0.95},
                              '_unmatched:extra': {'_raw':'junk','confidence':0.0}},
  is_available=lambda c: True)
sys1.register(p)
r = sys1.cascade_classify(['p','p'], sys1.Task(id='t', heads=[make_choice('q',['a','b'], id='h')]),
                           'x', cfg=sys1.load_config(), confidence_floor=0.7, log=False)
print(r.providers_used)
assert r.providers_used == ['p']
"
```
Expected: `['p']`

**Criterion 9: structured_ok on malformed subprocess**
```bash
.venv/bin/python -c "
import sys1, subprocess
from sys1 import providers
# patch subprocess.run to return non-JSON stdout
orig = subprocess.run
subprocess.run = lambda *a, **k: type('P', (), {'returncode':0, 'stdout':'not json', 'stderr':''})()
tele = {}
try:
    providers._gliner_wire('local')('x', [sys1.make_choice('q',['a','b'], id='h')], sys1.load_config(), tele)
except Exception:
    pass
print(tele.get('structured_ok'), tele.get('error_kind'))
assert tele.get('structured_ok') is False
assert tele.get('error_kind') == 'json'
subprocess.run = orig
"
```
Expected: `False json`

**Criterion 10: ruff clean**
```bash
cd /Users/alejandrodelvillar/Projects/sys1
.venv/bin/ruff check . --output-format=concise
```
Expected: no output (exit 0).

**Criterion 11: full test suite passes**
```bash
.venv/bin/python -m pytest -q
```
Expected: `126 passed` (or higher count after adding regression tests).

**Criterion 12: no existing test broken**
Same as criterion 11; the assertion is that the count does not decrease.

## Linked artifacts

Documents that need updating when this work is done. The recap workflow will prompt for each.

- `docs/STATE-SNAPSHOT.md` — update coverage, ruff count, defect count
- `CLAUDE.md` — update "Today's state"
- `TECHNICAL-DOCUMENTATION.md` section 5 "Provider Registry & Wire Shapes" — document noul normalization and structured_ok telemetry semantics
- `FUNCTIONAL-SPECIFICATIONS.md` section 1 "Classification Behavior" — document noul fallback behavior; section 3 "Gating & Exit Codes" — document block verdict semantics
- `docs/features/provider-gating.md` — create with the per-provider confidence floor rationale and the noul normalization rule

## Risks

Things that could go wrong.

- **noul coercion from yes/no strings could mask a real provider bug.** If a provider returns `{"label": "maybe"}` for a noul head, we coerce it to `None` instead of surfacing the bad output. Mitigation: only coerce the exact strings `"yes"` and `"no"` (case-insensitive); anything else stays `None` → escalate.
- **Flattening `_answers_by_provider` could lose per-answer metadata.** The service currently stores `input_sha256` per answer. Mitigation: preserve `input_sha256` as a top-level field on each flattened entry (the `_answers_by_provider` function already does this).
- **Adding `block` to `interpret()` could break consumers that pattern-match on `"pass" | "escalate"`.** Mitigation: `block` is only emitted when the config flag is on; consumers that don't set the flag see no change. Document the new verdict value in the linked artifacts.

## Dependencies

- None — this is a self-contained correctness pass on existing abstractions.

## Notes

- The `noul`-from-label fallback mirrors what `_jev_entry` already does implicitly: it reads `a.get("choice")` for `kind="choice"`. We're making the same shape accommodation explicit for all wire families.
- `Task.confidence_floor` was wired into the TOML/JSON loader (`tasks.py:527`) but never connected to the gating layer. Wiring it into `_floors_for` is the minimal fix; deleting the field would be a larger API break for anyone using `load_tasks_file`.
- The calibration endpoint bug (defect 2) is the highest-severity because it produces HTTP 500s on real traffic — any task with 6+ heads (including `change` if a user adds a head) crashes the dashboard.
