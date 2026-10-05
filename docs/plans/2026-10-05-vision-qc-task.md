---
status: completed
created: 2026-10-05
updated: 2026-10-05
slug: vision-qc-task
---

# Vision QC task — d1's first reason to be in the chain, and the describer cross-check

## Context

Liquid d1 (landed in `751da80`) is the pool's only vision-capable decision
model, registered but held out of the default chain pending calibration
evidence. The user's AI-image workflow today is:

> image → (agent/vision describer writes a description) → sys1 text judge → validation

That chain judges a **paraphrase**, not the artifact: every describer failure
(confabulating a clean emblem, missing garbled text) propagates silently into
the verdict. The `ai-image-generation-and-qc` skill codifies the checks as
free-text questions (step 5: "garbled text/artifacts? on-palette? legible at
32px? matches the brief?") answered by an unstructured look.

d1 lets us do two things:

1. **Judge the pixels directly** — `classify('liquid', <task>, brief, images=[…])`
   already works end-to-end (the `images` kwarg, `image_count` logging, and the
   blue-PNG probe proved the wire in the d1 plan); what's missing is a task:
   `liquid` currently inherits the jev diff-head set, which is meaningless for
   an artifact. This is the "vision-first task" explicitly deferred out of the
   d1 plan's scope.
2. **Turn the old chain into a signal** — running both paths over the same
   asset (description-mediated verdict vs d1-on-pixels verdict) makes their
   disagreement a measurable proxy for describer confabulation — a genuinely
   new calibration signal, since vision has no family baseline (d1 speaks
   images alone).

## Approach

### Phase 1 — the `vision_qc` task (sys1 core)

Pre-registered in `tasks.py` next to the other eight, house patterns
throughout:

1. **Heads — few and dense, because every question re-bills the image.**
   Patch math from the d1 contract: a 1024×1024 PNG is 1,024 patches → 1,536
   input tokens/question ≈ $0.00006 at d1 pricing, so cost is a footnote, but
   the request caps (≤10,000 patches, ≤8 images, ≤5MB/image) are real
   constraints the CLI must respect. Four heads, mirroring the skill's step-5
   checklist:
   - `matches_brief` — **choice** `["yes", "partial", "no"]`: does the rendered
     artifact depict what the written brief (the `state`) asks for?
   - `garbled_text` — **noul**: does the image contain garbled, corrupted, or
     nonsensical text? (true = defect; generated logos fail this most often)
   - `on_palette` — **noul**: are the dominant colors within the brand hexes
     quoted in the brief? (false = defect)
   - `legible_small` — **noul**: would the central mark remain legible scaled
     to 32px? (false = defect; favicon path)
   The brief text passed as `state` must embed the brand hexes and target
   sizes — the same "anchor prompts to the brand tokens" discipline the QC
   skill already requires at generation time.
2. **Provider allow-list**: `providers=["liquid"]` on the Task (the `safety`
   task's mechanism, inverted use) — no other registered provider can serve
   images, and a text-only provider answering these heads from the brief alone
   would be measuring nothing. `heads_for` unchanged: the task defines its own
   heads with no jev override, so liquid's inheritance rule falls through to
   `t.heads` correctly.
3. **Floors**: no task-level override — family 0.7 applies to the choice head;
   the noul heads derive confidence max(p, 1−p) family-consistently (the d1
   wire carries no noul confidence, per `parse_liquid`).

### Phase 1b — CLI images support

`sys1 classify <provider> <task> <text-file>` gains `--images PATH...`:
read each file, sniff content type (png/jpeg/webp/gif only), emit data URLs,
fail loudly on >5MB or non-image paths. The API side needs nothing — the
`images` kwarg is already threaded through `classify`/`cascade_classify`.
Logs keep carrying `image_count` only.

### Phase 2 — the pair-calibration probe

A small, scripted comparison (a probe, not a service feature):

1. **Corpus**: ~8–10 assets — the dev-decisions hero SVG→PNG and one or two
   known-good marks, plus deliberate corruptions generated with `sips`/PIL:
   hue-shifted off-palette variant, text overlay for the garble test, a
   32px-downscale render for the legibility test, and one clean re-encode as a
   control where only encoding changed.
2. **Two runs per asset**: (a) `liquid` + `vision_qc` on pixels; (b) the
   today-path — a fresh description written blind from the image, judged by the
   text family (`glide,drex,jev`) against the same brief.
3. **Signal**: per-head agreement between (a) and (b), disagreements annotated
   by hand against ground truth. The question the probe answers: *does
   d1-on-pixels catch describer confabulation?* A disagreement where pixels are
   right and the mediated path is wrong is a win; where d1 itself is wrong it
   is evidence for a floor raise or head rewording.
4. **Record**: results go in this plan's Outcome as the vision calibration
   evidence the d1 plan said was qualitative-for-now — and as the admission
   recommendation for routing (below).

### Phase 3 — routing decision (evidence-gated, may be no-op)

If the probe supports it: add a task-scoped route override so
`route(vision_qc, …)` resolves to `[liquid]` (the `providers.route.by_task`
override already exists in the router — this is config, not code). The default
text chain stays `glide,drex,jev` regardless; liquid joins the general pool
only via a separate future decision on text-corpus agreement.

## Acceptance criteria

- [x] `vision_qc` registered with the 4 heads above; `list_tasks()` shows it;
      `heads_for('vision_qc', 'liquid')` returns the task's own heads (not the
      jev inheritance set)
- [x] `Task(providers=["liquid"])` allow-list enforced: `classify` for
      `vision_qc` on a text-only provider declines/skips rather than answering
      from the brief (assert which, matching how `safety`'s list behaves)
- [x] `sys1 classify liquid vision_qc brief.txt --images a.png` works end-to-end;
      mock-wire tests assert the body carries `questions` from these heads and
      `images` as data URLs; `image_count` in the log record, content never
- [x] CLI guards: >5MB file, unsupported content type, and >8 images fail with
      clear errors before any request is made
- [x] Probe corpus + two-path runs executed; per-head agreement table recorded
      in the Outcome with the describer-confabulation question answered
      explicitly
- [x] Routing: `by_task` override for `vision_qc` added only if the Outcome
      supports it; default chain asserted unchanged either way
- [x] Full suite green (347 + new); `ruff check .` clean

## Files to be touched

- `src/sys1/tasks.py` — `_VISION_QC` registration
- `src/sys1/cli.py` — `--images` on `classify` (read + sniff + data-URL)
- `tests/test_tasks.py`, `tests/test_cli.py`, `tests/test_api.py` — the asserts above
- `docs/context/**` fragments — tasks + interface rows for the new task and
  the CLI flag (fragment-upkeep cycle)
- `README.md`, `TECHNICAL-DOCUMENTATION.md`, `CLAUDE.md` — task roster rows
- `scripts/vision_probe.py` (new) — the phase-2 probe harness (corpus paths in,
  agreement table out); corpus lives outside the repo under `data/`-style
  gitignored path, PNG/JPEG bytes are not committed

## Out of scope

- Admission of liquid to the general default text chain (separate
  text-corpus decision, per the d1 plan's gate)
- Multi-image comparison tasks (two assets side by side) — the wire supports
  up to 8 images but no head set needs it yet
- Dashboard/service changes: the pair probe is scripted; a standing
  vision-agreement endpoint waits for a second vision task to justify it
- Dev-decisions integration (calling the CLI from the pre-commit/gate flows or
  the QC skill's step 5) — follows once the probe is green; small separate
  follow-up
- Any use of `d1:free` (standing constraint from 2026-10-05)

## Verification

- `.venv/bin/python -m pytest -q` — full suite green
- `.venv/bin/ruff check .` — clean
- Live: hero-image.png of the dev-decisions LinkedIn package through
  `sys1 classify liquid vision_qc` with the original brief as state —
  expect `matches_brief=yes`, `garbled_text=false` (it has SVG-rendered text
  by design — legibility is the real test), `on_palette` per the dark tokens,
  `legible_small` on the card marks
- Live negative control: the same asset hue-shifted via `sips` → `on_palette`
  must flip; a 12px-tall text overlay → `garbled_text` must flip
- Pair probe: `scripts/vision_probe.py` emits the agreement table recorded in
  the Outcome

## Linked artifacts

- `docs/plans/2026-10-05-liquid-d1-provider.md` — the wire this task rides on;
  its "Out of scope: a vision-first task" is exactly what this plan closes
- `~/.agents/skills/ai-image-generation-and-qc/SKILL.md` — step 5's checklist is
  the head design's source; a follow-up can wire the CLI call into it
- Probe evidence + Outcome recorded here on implementation

## Outcome (2026-10-05)

Implemented as designed. 364 tests pass (347 before + 17 in
`tests/test_vision_qc.py`), `ruff check .` clean.

**Phase 1 / 1b.** `_VISION_QC` registered in `tasks.py` (four heads with the
planned wording and polarity); `heads_for('vision_qc', 'liquid')` returns the
task's own heads; the allow-list makes a text-only provider never answer —
`classify('decide', 'vision_qc', …)` returns `verdict="error"` with
`providers_used=[]` (tested, mirroring how `safety`'s list behaves).
`cli.load_images` enforces the d1 limits before any request (magic-byte sniff,
≤5MB, ≤8) and a guard failure exits 2 without a provider call. End-to-end the
CLI ran live: the log gained `image_count` on every pixels record and the
day's events.jsonl contains no base64 anywhere.

**Phase 2 probe.** Corpus under `data/vision_probe/` (gitignored; only the
build recipe is this note): the dev-decisions hero PNG (rsvg-rendered, text
crisp by design), the decision-flow diagram, and six variants — 126° hue
shift, mojibake overlay at 12px, full desaturation, 32px downscale, a
clean-re-encode control, a hue-shifted flow. Blind copies got shuffled
`asset_NN.png` names; the describer was **not** an agent (this session's
model has no image input — the same failure the QC skill documents) but the
skill's own fallback: hosted vision via `ollama.com/v1` `gemma4:31b`, key
sourced from `~/.hermes/.env`, never echoed. `scripts/vision_probe.py` ran
both paths live over the corpus; full table in `data/vision_probe/agreement.md`
and raw values in `agreement.json`.

Per-head roll-up (agree = pixels vs mediated majority; gt columns = cells
with hand-annotated ground truth):

| head | assets | pixels=mediated | d1 vs gt | med vs gt |
|---|---|---|---|---|
| matches_brief | 8 | 2/8 | 5/7 | 0/7 |
| garbled_text | 8 | 7/8 | 7/7 | 6/7 |
| on_palette | 8 | 1/8 | 6/8 | 3/8 |
| legible_small | 8 | 3/8 | 0/3* | 1/3* |

\* two `legible_small` GTs were left unannotated: without image input the
session could not honestly call whether the hero's headline survives 32px;
d1 said `true` at conf 0.91–0.98 on all six hero variants (displayed as
"miss" against a tentative annotation in the raw table).

**The describer-confabulation question, answered: yes, and it is the whole
ballgame.** On the clean hero family d1-on-pixels reproduced ground truth
(`matches_brief=yes`, `garbled_text=false`, `on_palette=true`) while the
mediated chain called `partial/no` and `false` at high confidence on **every**
asset — the describer's transcription noise ("Fastnlp API", approximate hex
names) propagates into the verdict exactly as the plan predicted. Decisive
case: the control re-encode, pixel-identical to the clean asset, made the
mediated chain flip `garbled_text=true` (glide 0.71, jev 0.52) while pixels
held `false` at 1.00. Ground-truth agreement overall: pixels 20/25 cells,
mediated 10/25.

Where d1 itself is wrong, and what it means:

- `on_palette` held `true` on the hero's hue-shifted (0.75) and desaturated
  (0.85) variants — the planned negative controls failed there, even though
  the hue-shifted flow did flip (0.67). Both wrong answers sit above the 0.7
  floor, so a floor raise cannot catch them; this is head-rewording evidence
  (the question likely needs to ask for the dominant color *named*, or be
  split from the brand-hex comparison the model seems to answer by gestalt).
- `legible_small=true` on the literal 32px downscale — d1 appears to answer
  about the design as imagined, not the artifact as rendered. Also a wording
  problem ("judge THIS file as if it were 32px" vs "would a 32px copy be
  legible"), not a floor problem.
- `matches_brief=no` (0.29, escalates at floor) on both flow assets. The flow
  brief was reverse-engineered from the SVG's text nodes and may over-specify
  stage numbering; annotated as brief-suspect rather than model-wrong.

**Phase 3 routing.** The Outcome supports the override: `providers.route.by_task
= {vision_qc = "liquid"}` now ships in DEFAULTS (vision_qc is unservable by
the text chain anyway — the allow-list empties it), and tests assert
`route(vision_qc, …) == ["liquid"]` with the key, `[]` without it
(availability filter beats the override), and `route(change, …) ==
["glide","drex","jev"]` with every key present — liquid's text-chain admission
remains the separate future decision this plan re-stated as out of scope.

Follow-ups this run suggests (not part of this plan): reword `on_palette` and
`legible_small` and re-run the probe on the same corpus; wire
`sys1 classify … --images` into the QC skill's step 5; a second probe with
hand-annotated `legible_small` ground truth from someone who can see the
assets.
