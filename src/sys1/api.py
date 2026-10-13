"""
Top-level public API.

`sys1.classify(...)` is the one function most callers need. It composes:
  task lookup → provider resolution → per-provider call → interpretation → JSONL.

The `cascade_classify` helper adds escalation: try a fast/cheap provider, and
only if it's low-confidence, ask the next one.
"""

from __future__ import annotations

import hashlib
import statistics
import time
from typing import Any

from . import providers as _providers
from .config import load_config
from .gating import answer_confidence, effective_floor, floors_for, interpret
from .routing import route_decision
from .tasks import get_task, heads_for
from .telemetry import confidence_histogram, log_record, sanitize_for_log
from .types import Answer, ClassifyResult, Task


def classify(
    provider: str | list[str] | None = None,
    task: str | Task = "change",
    text: str = "",
    cfg: dict | None = None,
    *,
    log: bool = True,
    input_sha256: str | None = None,
    extra_log_fields: dict[str, Any] | None = None,
    images: list | None = None,
) -> ClassifyResult:
    """
    Run one or more decision-model providers over `text` for a named task.

    `provider=None` (the default) lets the routing policy choose the chain:
    the per-task override or the default chain, size-gated and
    availability-filtered (see `routing.route_decision`). An explicit id,
    alias, or list bypasses routing entirely — that is the whole contract, so
    a consumer restating a chain in its call site is a consumer overriding
    policy, which is its own decision to own.

    Args:
        provider: provider id, "both"/"all" alias, or list of ids.
        task: a registered task name, or a `Task` instance.
        text: the payload to classify (diff, PR body, command string, ...).
        cfg: config dict; defaults to `load_config()`.
        log: when True (default), append a JSONL record (the calibration set).
        input_sha256: hash to log alongside the answers; defaults to the hash
            of `text` so feedback rows can join back.
        extra_log_fields: extra keys merged into the JSONL record.
        images: optional list of base64 image payloads (data URLs or
            {content_type, base64} objects) for vision-capable providers
            (liquid); text-only wires ignore them.

    Returns:
        `ClassifyResult` with per-provider answers, telemetry, and a verdict.
    """
    cfg = cfg or load_config()
    resolved_task = _resolve_task(task)
    resolved_providers = _resolve_providers(provider, cfg, resolved_task, text)

    answers: dict[str, dict[str, Answer]] = {}
    telemetry: dict[str, dict] = {}
    t0 = time.monotonic()

    for pid in resolved_providers:
        head_list = heads_for(resolved_task, pid)
        if not head_list:
            continue
        try:
            prov_telemetry: dict[str, Any] = {}
            result = _providers.call_provider(
                pid, text, head_list, cfg, prov_telemetry, images=images
            )
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
        _log_classify(result, cfg, text, input_sha256, extra_log_fields, images=images)

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
    images: list | None = None,
) -> ClassifyResult:
    """
    Walk a chain of providers in order. Stop as soon as one returns every head
    above the floor. If none clears the floor, the LAST provider's answers are
    what you get — the cascade records which ones were consulted in
    `providers_used`, and the per-provider telemetry in `telemetry`.

    Defaults to the routed chain (see routing.route): `glide,drex,jev` for
    short inputs — glide first (cheap), drex ahead of it once the input
    exceeds glide's bound, jev last. Explicit `provider_chain` arguments
    bypass routing entirely. The route decision rides into the log record as
    `route_chain` / `route_reason`.
    The first provider should be one whose confidence is well-calibrated for
    the task at hand — see gating.py for the per-provider floor notes.

    Floors: the explicit `confidence_floor` argument overrides the task's own
    floor; below that the ladder matches `interpret()` — per-provider config
    key > head `floor` > task > global > provider default. Noul answers with
    no explicit confidence gate on the derived certainty (Jev's 0-1 output),
    same as `interpret()`. `images` (optional) is forwarded like `classify()`:
    only vision-capable providers in the chain (liquid) can see them — name
    it explicitly when the input is visual, so the answer doesn't come from a
    text-only head of the chain.
    """
    cfg = cfg or load_config()
    resolved_task = _resolve_task(task)
    chain, route_reason = _chain_for(resolved_task, text, cfg, provider_chain)
    # explicit arg > task floor; the rest of the ladder resolves per head below
    floors = floors_for(
        cfg,
        task_floor=confidence_floor if confidence_floor is not None else resolved_task.confidence_floor,
    )

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
            prov_answers = _providers.call_provider(
                pid, text, head_list, cfg, tele, images=images
            )
            answers[pid] = prov_answers
            telemetry[pid] = tele
            providers_used.append(pid)
        except Exception as e:
            telemetry[pid] = {"error": str(e)[:300], "error_kind": "provider"}
            continue

        # did every head clear its floor on this provider?
        # Skip meta keys (_unmatched:*, _raw, etc.) — they aren't heads.
        real_answers = {k: v for k, v in prov_answers.items() if not k.startswith("_")}
        head_by_id = {h.id: h for h in head_list}
        conf_ok = True
        for head_id, a in real_answers.items():
            if not isinstance(a, dict):
                continue
            conf = answer_confidence(a)
            floor = effective_floor(cfg, floors, pid, head_by_id.get(head_id))
            # no gateable confidence (e.g. a bool noul with no explicit
            # confidence) cannot demonstrate the head cleared its floor
            if conf is None or conf < floor:
                conf_ok = False
                break
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
            floor_override=confidence_floor,
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
        _log_classify(
            result,
            cfg,
            text,
            extra_log_fields={
                "cascade": True,
                "chain": chain,
                "winner": winner,
                "route_chain": chain,
                "route_reason": route_reason,
            },
        )
    return result


def consistency_classify(
    provider: str,
    task: str | Task,
    text: str,
    *,
    n: int = 5,
    cfg: dict | None = None,
    log: bool = True,
    low: float = 0.30,
    high: float = 0.70,
) -> dict:
    """
    Self-consistency: re-ask the same task `n` times and band the per-head
    signal — the official /cookbooks/consistency_noul_cookbook pattern.

    Per head the signal is the raw noul probability P(true), or the winner's
    max probability for choice/score heads (read from `probabilities`, falling
    back to the answer's explicit confidence). Draw shapes:

    - probabilistic noul (Jev, local): the noul value itself
    - Decide-shaped boolean noul (hosted decide): P(true) estimated as the
      boolean's confidence when true, `1 - confidence` when false — hosted
      decide's confidence is the winning label's max probability
    - choice/score: winner's max probability, else the explicit confidence

    Draws with no gateable number (declined, bool noul without confidence)
    count as samples but score as None — the cookbook's "counted but not
    scored".

    `band` maps the mean over usable samples: below `low` -> "no", above
    `high` -> "yes", inclusive between -> "uncertain" (route to review).
    The bounds ship as ILLUSTRATIVE starting points from the cookbook — fit
    your own from the JSONL calibration stream (`record_feedback` +
    `calibration_curve`); published thresholds do not transfer across
    workloads.

    Each draw is an ordinary classify() call, so with log=True the JSONL log
    gains n regular records (same shape, joined by input_sha256) — no new
    record contract. Cost scales linearly with `n`: output tokens are free,
    state tokens are not.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    cfg = cfg or load_config()
    resolved_task = _resolve_task(task)
    head_list = heads_for(resolved_task, provider)

    samples: dict[str, list[float | None]] = {h.id: [] for h in head_list}
    for _ in range(n):
        result = classify(provider, resolved_task, text, cfg=cfg, log=log)
        answers = result.answers.get(provider, {})
        for h in head_list:
            samples[h.id].append(_consistency_signal(answers.get(h.id)))

    heads_out: dict[str, dict] = {}
    for h in head_list:
        draws = samples[h.id]
        usable = [s for s in draws if s is not None]
        mean = statistics.mean(usable) if usable else None
        stdev = statistics.stdev(usable) if len(usable) >= 2 else None
        if mean is None:
            band = None
        elif mean < low:
            band = "no"
        elif mean > high:
            band = "yes"
        else:
            band = "uncertain"
        heads_out[h.id] = {
            "kind": h.kind,
            "samples": draws,
            "mean": mean,
            "stdev": stdev,
            "band": band,
        }

    return {
        "task": resolved_task.id,
        "provider": provider,
        "n": n,
        "bounds": {"low": low, "high": high},
        "heads": heads_out,
    }


def _consistency_signal(entry: Answer | None) -> float | None:
    """The per-draw number consistency bands: raw P(true) for noul, the
    winner's max probability for choice/score, else the explicit confidence.
    None when the draw carries no gateable number."""
    if not isinstance(entry, dict) or entry.get("_declined"):
        return None
    if entry.get("type") == "noul" or "noul" in entry:
        noul = entry.get("noul")
        if isinstance(noul, bool):
            # Decide-shaped boolean noul: confidence is the winning label's
            # max probability, so P(true) is conf when true, 1 - conf when false
            conf = entry.get("confidence")
            if isinstance(conf, (int, float)) and not isinstance(conf, bool):
                return float(conf) if noul else 1.0 - float(conf)
            return None
        if isinstance(noul, (int, float)):
            return float(noul)
        return None
    probs = entry.get("probabilities")
    if isinstance(probs, dict) and probs:
        vals = [
            float(v)
            for v in probs.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if vals:
            return max(vals)
    return answer_confidence(entry)


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolve_task(task: str | Task) -> Task:
    if isinstance(task, Task):
        return task
    t = get_task(task)
    if t is None:
        raise KeyError(f"unknown task {task!r}")
    return t


def _chain_for(
    task: Task, text: str, cfg: dict, provider_chain: list[str] | None
) -> tuple[list[str], str]:
    """The cascade's chain: explicit arguments bypass routing entirely."""
    if provider_chain is not None:
        return list(provider_chain), "explicit"
    return route_decision(task, text, cfg)


def _resolve_providers(spec: str | list[str] | None, cfg: dict, task: Task, text: str = "") -> list[str]:
    if spec is None:
        # An unset provider routes per policy: the task's chain from
        # `providers.route`, size-gated and availability-filtered. The text
        # matters — the size gate reorders providers whose documented input
        # bound cannot take this payload.
        chain, _reason = route_decision(task, text, cfg)
        return chain
    raw = _providers.resolve_providers(spec)
    if task.providers is not None:
        raw = [p for p in raw if p in task.providers]
    available = _providers.available_providers(cfg)
    return [p for p in raw if p in available]


def _explicit_unavailable(spec: str | list[str] | None, cfg: dict) -> str:
    """When nothing resolved, explain an explicitly-named provider's state.

    Fan-out specs ("all", "both", "core") stay generic; a single named provider
    reports its real availability reason (disabled by flag / missing key / venv
    absent / no listener) so `--provider clm` says *why* rather than a bare
    "no-providers". An unset spec stays generic: routing decided, and nothing
    was available to route to.
    """
    if spec is None:
        return "no-providers"
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
    images: list | None = None,
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
    if images:
        # count only — image content is never logged (same line as redact_fields)
        payload["image_count"] = len(images)
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
