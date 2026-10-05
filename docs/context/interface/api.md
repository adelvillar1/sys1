---
title: The Python API — classify, cascade, consistency, health
status: shipped
sources:
  - src/sys1/api.py
  - src/sys1/__init__.py
  - tests/test_api.py
  - tests/test_consistency.py
related:
  - architecture/gating.md
  - architecture/routing-and-cascade.md
  - foundation/charter.md
---

# The Python API

`api.py` composes the library; `__init__.py` re-exports the public surface (52 names in `__all__`,
version in `__version__`). The composition chain is always: task lookup → provider resolution →
per-provider call → interpretation → JSONL.

## `classify`

`sys1.classify(provider, task, text)` — provider is an id, an alias (`both`/`all`/`core`), or a
list; task is a registered name or a `Task` instance. `_resolve_providers` intersects the spec with
the task allow-list and live availability. Each provider call is wrapped in try/except: a broken
provider logs `{"error", "error_kind": "provider"}` telemetry and the rest continue (**fail open**).
When *no* provider succeeded the result is `verdict="error"`, and `_explicit_unavailable` names the
reason a single explicitly-requested provider is off (disabled flag / missing key / venv absent /
no listener) so `--provider clm` says *why*.

`classify` and `cascade_classify` both take an optional `images` list for visual inputs. Only
vision-capable providers see them — today that is `liquid` alone — so a text-only provider named
with `images=` silently drops them; name liquid explicitly (the docstring says so). The log record
gains `image_count` (a count, never the payload).

The result is a `ClassifyResult` (`types.py`): `answers` keyed provider → head_id, `telemetry` per
provider, `verdict`/`escalated`, `summary` lines, plus `head()`, `best_answer()`, and `as_dict()`
(the JSON-serializable form used by both the log record and the HTTP response).

## `cascade_classify` and `consistency_classify`

See [routing-and-cascade.md](../architecture/routing-and-cascade.md) for both — the chain walk with
escalation, and n-draw self-consistency with no/uncertain/yes bands.

## `health_report`

`api.health_report` returns `providers.provider_report(cfg)` — the flag-aware per-provider snapshot
behind the service `/v1/health` and `sys1 doctor`. `available` means enabled AND usable right now;
keys `id`/`name`/`available`/`reason` stay stable for dev-decisions consumers.

## Gotchas

- `_all_heads` unions the head sets actually called (de-duped by id) before `interpret`, so a
  provider-specific head escalates even when another provider doesn't ask it.
- `log=True` (default) appends the calibration record on every call, including each
  consistency draw.
