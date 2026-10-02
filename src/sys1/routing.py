"""
Deterministic chain routing for cascade_classify.

`route()` answers one question: given this task and this input, which provider
chain should the cascade walk? It is a policy function, not an adaptive
router — decision calls are low-volume and near-free at the head of the
chain, so routing quality lives in the confidence floors and the parity
harness, not in model scoring (contrast zcode-router-kit, whose quota-aware
selection targets high-volume coding models).

Signals, in application order:

  1. `providers.route.by_task[task.id]` — explicit per-task chain override,
     else `providers.route.default_chain` (hosted-only by design:
     local/modernbert are never auto-routed).
  2. input size — past `providers.route.long_input_chars` (default:
     `glide_max_chars`), providers whose documented bound cannot take the
     input move to the back of the chain.
  3. availability — providers that are not enabled+usable right now drop
     out (the existing flag/key machinery).
  4. task allow-list — `Task.providers` (e.g. safety's) is a hard filter,
     preserving chain order.

`route()` returns just the chain; `route_decision()` also returns the reason
written into the cascade's log record (`route_chain` / `route_reason` —
added keys only, no renames). `providers.route.enabled = false` bypasses
everything and returns the legacy `["decide", "jev"]` cascade default.

See docs/plans/2026-10-01-glide-provider-and-router.md.
"""

from __future__ import annotations

from . import providers as _providers
from .types import Task

_LEGACY_CHAIN = ["decide", "jev"]
_DEFAULT_CHAIN = ["glide", "drex", "jev"]

# Providers with a documented input bound the router must respect. GLiDE's
# 40,000-token rendered-prompt limit and Julia-1's 8,192-token window are the
# binding constraints on the default chain (drex takes 131,072 tokens; jev
# truncates via scan.max_diff_chars rather than failing). Keyed by provider id.
_SIZE_EXCLUDED = {"glide", "julia"}


def route(task: Task, text: str, cfg: dict) -> list[str]:
    """The provider chain for this task/input (see module docstring)."""
    chain, _ = route_decision(task, text, cfg)
    return chain


def route_decision(task: Task, text: str, cfg: dict) -> tuple[list[str], str]:
    """
    The routed chain plus the reason for the audit log.

    Reason precedence: `task` (a by_task override was used) > `long-input`
    (the size reorder applied) > `availability-filtered` (the filters
    changed the chain) > `default`.
    """
    rcfg = cfg.get("providers", {}).get("route") or {}
    if not rcfg.get("enabled", True):
        return list(_LEGACY_CHAIN), "default"

    by_task = rcfg.get("by_task") or {}
    override = _split_chain(by_task.get(task.id, ""))
    chain = override or _split_chain(rcfg.get("default_chain")) or list(_DEFAULT_CHAIN)
    reason = "task" if override else "default"

    # size gate: providers whose documented bound cannot take the input go last
    pcfg = cfg.get("providers", {})
    long_chars = rcfg.get("long_input_chars") or pcfg.get("glide_max_chars") or 100_000
    if len(text) > int(long_chars):
        excluded = [p for p in chain if p in _SIZE_EXCLUDED]
        if excluded:
            chain = [p for p in chain if p not in _SIZE_EXCLUDED] + excluded
            reason = "long-input"

    # task allow-list wins as a hard filter (e.g. _SAFETY excludes modernbert)
    if task.providers is not None:
        allowed = {str(p).strip().lower() for p in task.providers}
        chain = [p for p in chain if p in allowed]

    # availability: enabled AND usable right now (flag machinery, key, listener)
    available = set(_providers.available_providers(cfg))
    filtered = [p for p in chain if p in available]
    if len(filtered) != len(chain) and reason == "default":
        reason = "availability-filtered"
    return filtered, reason


def _split_chain(raw) -> list[str]:
    """A chain string ("glide,drex,jev"), list, or tuple -> clean id list."""
    if isinstance(raw, (list, tuple)):
        return [str(p).strip().lower() for p in raw if str(p).strip()]
    return [p.strip().lower() for p in str(raw or "").replace(" ", ",").split(",") if p.strip()]
