---
title: Routing and cascade — deterministic chains, escalation on low confidence
status: shipped
sources:
  - src/sys1/routing.py
  - src/sys1/api.py
  - tests/test_routing.py
related:
  - architecture/providers-and-flags.md
  - architecture/gating.md
---

# Routing and cascade

`routing.py` answers one question for `cascade_classify`: given this task and this input, which
provider chain should the cascade walk? It is a **deterministic policy function, not an adaptive
router** — decision calls are low-volume and near-free at the head of the chain, so routing quality
lives in the confidence floors, not in model scoring.

## `route_decision` signals, in application order

1. `providers.route.by_task[task.id]` — explicit per-task chain override; else
   `providers.route.default_chain` (`glide,drex,jev` — hosted-only by design; local/modernbert are
   never auto-routed).
2. **Size gate**: past `providers.route.long_input_chars` (default `glide_max_chars`), providers in
   `_SIZE_EXCLUDED` (glide, the 40K-token bound; julia, the 12K bound) move to the back of the
   chain; drex takes 131K tokens, liquid's 150K-char bound has 65,536-token headroom, jev
   truncates instead of failing.
3. Availability — not enabled+usable right now drops out (the flag machinery).
4. `Task.providers` allow-list — a hard filter that preserves chain order.

`providers.route.enabled = false` bypasses everything and returns the legacy `["decide", "jev"]`
chain. The returned reason (`task` > `long-input` > `availability-filtered` > `default`) rides into
the log record as `route_reason`.

## `cascade_classify` semantics

`api.cascade_classify` walks the chain in order and **stops at the first provider whose every head
clears its floor** (`answer_confidence` + `effective_floor`, same helpers as `interpret`). Providers
that error are recorded in telemetry and skipped. If none clears, the LAST provider that returned
answers is the verdict provider — its low-confidence answers are what the gate should escalate on.

The verdict is computed from the clearing provider **alone** (the zcode-router-kit reconciliation
pattern), not by merging providers. Log record extras: `cascade: true`, `chain`, `winner`,
`route_chain`, `route_reason` — added keys only, no renames.

## Vision inputs in a cascade

`cascade_classify` accepts the same optional `images` list as `classify()` and forwards it to every
chain member. Only vision-capable providers (liquid) can see them, so a chain of text-only models
silently drops them — the docstring tells callers to name liquid explicitly (or put it first) when
the input is visual. The log record carries `image_count`, never the payload.

## Gotchas

- An explicit `provider_chain` argument bypasses routing entirely (reason `explicit`).
- A head with no gateable confidence (bool noul, missing number) cannot demonstrate the floor was
  cleared — the cascade moves on (`conf is None` counts as not-ok in the walk).
- `consistency_classify` (same module) is a different pattern: self-consistency over n draws of one
  provider, banded no/uncertain/yes per the cookbook bounds (`low`, `high`). Each draw is an
  ordinary classify() and logs its own record, joined by `input_sha256`.
