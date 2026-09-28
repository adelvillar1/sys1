"""
Top-level public API.

`sys1.classify(...)` is the one function most callers need. It composes:
  task lookup → provider resolution → per-provider call → interpretation → JSONL.

The `cascade_classify` helper adds escalation: try a fast/cheap provider, and
only if it's low-confidence, ask the next one.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from . import providers as _providers
from .config import load_config
from .gating import interpret
from .tasks import get_task, heads_for
from .telemetry import confidence_histogram, log_record, sanitize_for_log
from .types import Answer, ClassifyResult, Task


def classify(
    provider: str | list[str] = "decide",
    task: str | Task = "change",
    text: str = "",
    cfg: dict | None = None,
    *,
    log: bool = True,
    input_sha256: str | None = None,
    extra_log_fields: dict[str, Any] | None = None,
) -> ClassifyResult:
    """
    Run one or more decision-model providers over `text` for a named task.

    Args:
        provider: provider id, "both"/"all" alias, or list of ids.
        task: a registered task name, or a `Task` instance.
        text: the payload to classify (diff, PR body, command string, ...).
        cfg: config dict; defaults to `load_config()`.
        log: when True (default), append a JSONL record (the calibration set).
        input_sha256: hash to log alongside the answers; defaults to the hash
            of `text` so feedback rows can join back.

    Returns:
        `ClassifyResult` with per-provider answers, telemetry, and a verdict.
    """
    cfg = cfg or load_config()
    resolved_task = _resolve_task(task)
    resolved_providers = _resolve_providers(provider, cfg, resolved_task)

    answers: dict[str, dict[str, Answer]] = {}
    telemetry: dict[str, dict] = {}
    t0 = time.monotonic()

    for pid in resolved_providers:
        head_list = heads_for(resolved_task, pid)
        if not head_list:
            continue
        try:
            prov_telemetry: dict[str, Any] = {}
            result = _providers.call_provider(pid, text, head_list, cfg, prov_telemetry)
            answers[pid] = result
            telemetry[pid] = prov_telemetry
        except Exception as e:  # fail-open: a broken provider never breaks the gate
            telemetry[pid] = {"error": str(e)[:300], "error_kind": "provider"}
            continue

    elapsed = int((time.monotonic() - t0) * 1000)

    if not answers:
        reason = (
            telemetry.get(resolved_providers[0], {}).get("error", "no-provider-succeeded")
            if resolved_providers
            else _explicit_unavailable(provider, cfg)
        )
        return ClassifyResult(
            task=resolved_task.id,
            provider=provider if isinstance(provider, str) else ",".join(provider),
            providers_used=[],
            answers={},
            telemetry=telemetry,
            verdict="error",
            escalated=True,
            latency_ms=elapsed,
            reason=reason,
            summary=["No provider succeeded — unclassified."],
        )

    verdict, escalated, summary = interpret(
        answers, cfg, heads=_all_heads(resolved_task, resolved_providers), task=resolved_task
    )

    result = ClassifyResult(
        task=resolved_task.id,
        provider=provider if isinstance(provider, str) else ",".join(provider),
        providers_used=list(answers),
        answers=answers,
        telemetry=telemetry,
        verdict=verdict,
        escalated=escalated,
        latency_ms=elapsed,
        summary=summary,
    )

    if log:
        _log_classify(result, cfg, text, input_sha256, extra_log_fields)

    return result


# ── cascade: try a chain of providers, escalate on low confidence ─────────────


def cascade_classify(
    provider_chain: list[str] | None = None,
    task: str | Task = "change",
    text: str = "",
    cfg: dict | None = None,
    *,
    confidence_floor: float | None = None,
    log: bool = True,
) -> ClassifyResult:
    """
    Walk a chain of providers in order. Stop as soon as one returns every head
    above the floor. If none clears the floor, the LAST provider's answers are
    what you get — the cascade records which ones were consulted in
    `providers_used`, and the per-provider telemetry in `telemetry`.

    Defaults to `["decide", "jev"]` (cheap hosted first, strong hosted second).
    The first provider should be one whose confidence is well-calibrated for
    the task at hand — see gating.py for the per-provider floor notes.
    """
    cfg = cfg or load_config()
    chain = provider_chain or ["decide", "jev"]
    resolved_task = _resolve_task(task)
    floor = float(confidence_floor if confidence_floor is not None else cfg["classify"]["confidence_floor"])

    t0 = time.monotonic()
    answers: dict[str, dict[str, Answer]] = {}
    telemetry: dict[str, dict] = {}
    providers_used: list[str] = []
    winner: str | None = None

    for pid in chain:
        head_list = heads_for(resolved_task, pid)
        if not head_list:
            continue
        try:
            tele: dict[str, Any] = {}
            prov_answers = _providers.call_provider(pid, text, head_list, cfg, tele)
            answers[pid] = prov_answers
            telemetry[pid] = tele
            providers_used.append(pid)
        except Exception as e:
            telemetry[pid] = {"error": str(e)[:300], "error_kind": "provider"}
            continue

        # did every head clear the floor on this provider?
        # Skip meta keys (_unmatched:*, _raw, etc.) — they aren't heads.
        real_answers = {k: v for k, v in prov_answers.items() if not k.startswith("_")}
        conf_ok = all(
            float(a.get("confidence") or 0) >= floor
            for a in real_answers.values()
            if isinstance(a, dict)
        )
        labels_ok = all(
            (a.get("label") is not None or a.get("score") is not None or a.get("noul") is not None)
            for a in real_answers.values()
            if isinstance(a, dict)
        )
        if conf_ok and labels_ok:
            winner = pid
            break

    elapsed = int((time.monotonic() - t0) * 1000)

    # The verdict is driven by the clearing provider alone (zcode-router-kit's
    # reconciliation pattern). If nothing cleared, fall back to the last one
    # we actually got answers from — its low-confidence answers are what the
    # gate should escalate on.
    verdict_provider = winner or (providers_used[-1] if providers_used else None)
    summary: list[str]
    if verdict_provider:
        verdict_payload = {verdict_provider: answers[verdict_provider]}
        verdict, escalated, summary = interpret(
            verdict_payload,
            cfg,
            heads=heads_for(resolved_task, verdict_provider),
            task=resolved_task,
        )
    else:
        verdict, escalated, summary = "error", True, ["cascade: no provider succeeded"]

    result = ClassifyResult(
        task=resolved_task.id,
        provider="cascade[" + ",".join(chain) + "]",
        providers_used=providers_used,
        answers=answers,
        telemetry=telemetry,
        verdict=verdict,  # type: ignore[arg-type]
        escalated=escalated,
        latency_ms=elapsed,
        summary=summary,
    )
    if log:
        _log_classify(result, cfg, text, extra_log_fields={"cascade": True, "chain": chain, "winner": winner})
    return result


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolve_task(task: str | Task) -> Task:
    if isinstance(task, Task):
        return task
    t = get_task(task)
    if t is None:
        raise KeyError(f"unknown task {task!r}")
    return t


def _resolve_providers(spec: str | list[str], cfg: dict, task: Task) -> list[str]:
    raw = _providers.resolve_providers(spec)
    if task.providers is not None:
        raw = [p for p in raw if p in task.providers]
    available = _providers.available_providers(cfg)
    return [p for p in raw if p in available]


def _explicit_unavailable(spec: str | list[str], cfg: dict) -> str:
    """When nothing resolved, explain an explicitly-named provider's state.

    Fan-out specs ("all", "both", "core") stay generic; a single named provider
    reports its real availability reason (disabled by flag / missing key / venv
    absent / no listener) so `--provider clm` says *why* rather than a bare
    "no-providers".
    """
    names = [spec] if isinstance(spec, str) else list(spec)
    single = names[0].strip().lower() if len(names) == 1 and names[0] else None
    if single and single in _providers.REGISTRY and single not in ("all", "both", "core", "optin", "candidates"):
        return f"{single} unavailable: {_providers.availability_reason(single, cfg)}"
    return "no-providers"


def _all_heads(task: Task, providers: list[str]) -> list:
    """Union of every head across the providers we actually called, de-duped by id."""
    seen: dict[str, Any] = {}
    for pid in providers:
        for h in heads_for(task, pid):
            seen.setdefault(h.id, h)
    return list(seen.values())


def _log_classify(
    result: ClassifyResult,
    cfg: dict,
    text: str,
    input_sha256: str | None = None,
    extra_log_fields: dict[str, Any] | None = None,
) -> None:
    payload = {
        "op": "classify",
        "task": result.task,
        "provider": result.provider,
        "providers_used": list(result.providers_used),
        "input_chars": len(text),
        "input_sha256": input_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        "answers": sanitize_for_log(
            result.answers,
            cfg.get("log", {}).get("redact_fields") or sanitize_for_log.__defaults__[0],  # type: ignore[index]
        ),
        "verdict": result.verdict,
        "escalated": result.escalated,
        "latency_ms": result.latency_ms,
        "telemetry": result.telemetry,
        "confidence_histogram": confidence_histogram(result.answers),
    }
    if extra_log_fields:
        payload.update(extra_log_fields)
    log_record(payload, cfg=cfg)


def health_report(cfg: dict | None = None) -> dict:
    """
    Per-provider snapshot for the HTTP `/health` and CLI doctor.

    Flag-aware: `available` means enabled AND usable right now. The report also
    carries `enabled` / `configured` / `tier` / `wire` / `config` so the control
    dashboard and doctor can explain *why* a provider is off, not just that it
    is. Keys `id` / `name` / `available` / `reason` stay present for consumers
    (dev-decisions) that read them directly.
    """
    return _providers.provider_report(cfg)
