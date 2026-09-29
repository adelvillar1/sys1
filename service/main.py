"""
sys1 HTTP service.

A thin FastAPI wrapper exposing the same `classify()` / `cascade_classify()` /
`health_report()` / `list_tasks()` API as the library, for callers in any
language. The library owns the logic; this file only defines the wire schema,
bearer-token auth, and the JSONL log reader used by `/v1/calibration`.

Run:

    uvicorn service.main:app --host 0.0.0.0 --port 8400

Env:

    SYS1_BEARER_TOKEN   Optional. When set, every /v1/* request must carry
                        `Authorization: Bearer <token>`. When unset, the
                        service runs open — only put it on a private network
                        that way.

    SYS1_LOG_DIR        Optional override for the JSONL log location.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import sys1
from sys1.telemetry import calibration_curve, confidence_histogram


def _load_dashboard_module():
    """
    Load `service/dashboard.py` by file path so it works both under uvicorn
    (`service.main:app`, namespace package) and under the test harness, which
    loads this module standalone with no package context.
    """
    path = Path(__file__).resolve().parent / "dashboard.py"
    spec = importlib.util.spec_from_file_location("sys1_dashboard", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sys1_dashboard"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("sys1_dashboard", None)
        return None
    return mod

app = FastAPI(
    title="sys1",
    version=sys1.__version__,
    description=(
        "Reusable decision-model abstraction: Jev, GLiNER-2.5-Decide, local "
        "GLiNER2, ModernBERT — behind one HTTP API."
    ),
)


# ── auth ─────────────────────────────────────────────────────────────────────

_EXPECTED_TOKEN: str | None = os.environ.get("SYS1_BEARER_TOKEN")


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not _EXPECTED_TOKEN:
        return  # service is open
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    # constant-time-ish compare; tokens are short, no fancy lib needed
    if not _secure_eq(token, _EXPECTED_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _secure_eq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a, b)


# ── schemas ───────────────────────────────────────────────────────────────────


class ClassifyRequest(BaseModel):
    provider: str | list[str] = Field(..., description="provider id, 'both'/'all' alias, comma list, or a list")
    task: str | None = Field(None, description="registered task name")
    task_spec: dict | None = Field(
        None,
        description=(
            "inline [[tasks]]-shaped task definition (id + heads) — built via "
            "sys1.task_from_spec, not registered; use for dynamic heads "
            "(e.g. workflow labels that change per install)"
        ),
    )
    text: str = Field(..., description="the payload to classify")
    confidence_floor: float | None = Field(None, ge=0, le=1, description="override cfg.classify.confidence_floor")
    block_on_classification: bool | None = Field(None, description="when true, escalate returns block")
    log: bool = Field(True, description="append a JSONL calibration record (default true)")


class CascadeRequest(BaseModel):
    chain: list[str] = Field(default_factory=lambda: ["decide", "jev"], description="provider ids in escalation order")
    task: str = Field("change")
    text: str
    confidence_floor: float | None = Field(None, ge=0, le=1)
    log: bool = True


class FeedbackRequest(BaseModel):
    input_sha256: str
    task: str
    head_id: str
    predicted_label: str | None = None
    predicted_confidence: float | None = Field(None, ge=0, le=1)
    provider: str
    actual_label: str | None = None
    escalated: bool = False


class ClassifyResponse(BaseModel):
    task: str
    provider: str
    providers_used: list[str]
    answers: dict[str, dict[str, Any]]
    telemetry: dict[str, dict[str, Any]] = Field(default_factory=dict)
    verdict: Literal["pass", "escalate", "block", "error"]
    escalated: bool
    latency_ms: int
    reason: str | None = None
    summary: list[str] = Field(default_factory=list)
    input_sha256: str | None = None


# ── classify endpoints ───────────────────────────────────────────────────────


@app.post("/v1/classify", response_model=ClassifyResponse, dependencies=[Depends(require_auth)])
def classify_endpoint(req: ClassifyRequest) -> ClassifyResponse:
    cfg = _cfg_for(req)
    if req.task_spec is not None:
        if not isinstance(req.task_spec, dict) or not req.task_spec.get("id"):
            raise HTTPException(status_code=422, detail="task_spec must be an object with an 'id'")
        task = sys1.task_from_spec(req.task_spec)
    elif req.task:
        task = req.task
    else:
        raise HTTPException(status_code=422, detail="provide 'task' (registered name) or 'task_spec' (inline)")
    result = sys1.classify(req.provider, task, req.text, cfg=cfg, log=req.log)
    return _to_response(result, req.text)


@app.post("/v1/cascade", response_model=ClassifyResponse, dependencies=[Depends(require_auth)])
def cascade_endpoint(req: CascadeRequest) -> ClassifyResponse:
    cfg = _cfg_for(req)
    result = sys1.cascade_classify(
        provider_chain=req.chain,
        task=req.task,
        text=req.text,
        cfg=cfg,
        confidence_floor=req.confidence_floor,
        log=req.log,
    )
    return _to_response(result, req.text)


def _cfg_for(req: ClassifyRequest | CascadeRequest) -> dict:
    cfg = sys1.load_config()
    if req.confidence_floor is not None:
        cfg["classify"]["confidence_floor"] = float(req.confidence_floor)
    block = getattr(req, "block_on_classification", None)
    if block is not None:
        cfg["classify"]["block_on_classification"] = bool(block)
    return cfg


def _to_response(result: sys1.ClassifyResult, text: str) -> ClassifyResponse:
    import hashlib

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return ClassifyResponse(
        task=result.task,
        provider=result.provider,
        providers_used=list(result.providers_used),
        answers={p: dict(h) for p, h in result.answers.items()},
        telemetry={p: dict(t) for p, t in result.telemetry.items()},
        verdict=result.verdict,  # type: ignore[arg-type]
        escalated=result.escalated,
        latency_ms=result.latency_ms,
        reason=result.reason,
        summary=list(result.summary),
        input_sha256=sha,
    )


# ── introspection endpoints ──────────────────────────────────────────────────


@app.get("/v1/health")
def health_endpoint() -> JSONResponse:
    report = sys1.health_report()
    available = [pid for pid, info in report.items() if info["available"]]
    return JSONResponse(
        {
            "version": sys1.__version__,
            "providers": report,
            "available": available,
            "auth": "bearer" if _EXPECTED_TOKEN else "open",
        }
    )


@app.get("/v1/tasks")
def tasks_endpoint() -> JSONResponse:
    out: list[dict[str, Any]] = []
    for name in sys1.list_tasks():
        task = sys1.get_task(name)
        if task is None:
            continue
        out.append(
            {
                "id": task.id,
                "description": task.description,
                "providers": task.providers,
                "heads": [
                    {
                        "id": h.id,
                        "kind": h.kind,
                        "task": h.task,
                        "labels": h.labels,
                        "multi_label": h.multi_label,
                    }
                    for h in task.heads
                ],
            }
        )
    return JSONResponse({"tasks": out})


@app.get("/v1/tasks/{task_id}")
def task_endpoint(task_id: str) -> JSONResponse:
    task = sys1.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"no task named {task_id!r}")
    return JSONResponse(
        {
            "id": task.id,
            "description": task.description,
            "providers": task.providers,
            "heads": [
                {"id": h.id, "kind": h.kind, "task": h.task, "labels": h.labels, "multi_label": h.multi_label}
                for h in task.heads
            ],
            "provider_heads": {
                pid: [
                    {"id": h.id, "kind": h.kind, "task": h.task, "labels": h.labels, "multi_label": h.multi_label}
                    for h in heads
                ]
                for pid, heads in task.provider_heads.items()
            },
        }
    )


# ── feedback + calibration ───────────────────────────────────────────────────


@app.post("/v1/feedback", dependencies=[Depends(require_auth)])
def feedback_endpoint(req: FeedbackRequest) -> JSONResponse:
    path = sys1.record_feedback(
        input_sha256=req.input_sha256,
        task=req.task,
        head_id=req.head_id,
        predicted_label=req.predicted_label,
        predicted_confidence=req.predicted_confidence,
        provider=req.provider,
        actual_label=req.actual_label,
        escalated=req.escalated,
        # No cfg means record_feedback falls back to the library default and
        # ignores the deployment's log_dir — the feedback rows then scatter
        # away from the classify rows they join against by input_sha256.
        cfg=sys1.load_config(),
    )
    return JSONResponse({"recorded": path is not None, "path": str(path) if path else None})


@app.get("/v1/calibration", dependencies=[Depends(require_auth)])
def calibration_endpoint(days: int = Query(30, ge=1, le=365)) -> JSONResponse:
    cfg = sys1.load_config()
    log_root = Path(cfg["classify"].get("log_dir") or str(Path.home() / ".local/share/sys1/logs")).expanduser()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    classify_rows, feedback_rows = _load_log_rows(log_root, cutoff)
    by_provider: dict[str, dict] = {}
    for prov, raw_answers in _answers_by_provider(classify_rows).items():
        # _answers_by_provider returns {head_id: [{_answer: ..., ...}, ...]}.
        # Flatten to the {head_id: Answer} shape the histogram / agreement
        # helpers expect — take the most recent entry per head.
        answers: dict[str, Any] = {}
        record_count = 0
        for head_id, entries in raw_answers.items():
            if entries:
                answers[head_id] = entries[-1].get("_answer", {})
                record_count += len(entries)
        by_provider[prov] = {
            "records": record_count,
            "histogram": confidence_histogram({"tmp": answers}).get("tmp", []),
            "agreement": sys1.provider_agreement(answers) if len(answers) > 1 else {},
        }
    return JSONResponse(
        {
            "days": days,
            "classify_records": len(classify_rows),
            "feedback_records": len(feedback_rows),
            "curve": calibration_curve(feedback_rows),
            "providers": by_provider,
        }
    )


def _load_log_rows(log_root: Path, cutoff: datetime) -> tuple[list[dict], list[dict]]:
    if not log_root.exists():
        return [], []
    classify_rows: list[dict] = []
    feedback_rows: list[dict] = []
    for events in log_root.rglob("events.jsonl"):
        # YYYY/MM/DD directory layout — derive date from path
        try:
            parts = events.parts[-4:-1]
            day = datetime.strptime("/".join(parts), "%Y/%m/%d").replace(tzinfo=timezone.utc)
            if day < cutoff:
                continue
        except (ValueError, IndexError):
            pass  # unusual layout, scan anyway
        with open(events) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("op") == "classify":
                    classify_rows.append(rec)
                elif rec.get("op") == "feedback":
                    feedback_rows.append(rec)
    return classify_rows, feedback_rows


def _answers_by_provider(classify_rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Aggregate `{head: answer}` records by provider into `{provider: {head_id: [answers...]}}`."""
    out: dict[str, dict[str, list[dict]]] = {}
    for row in classify_rows:
        for prov, heads in (row.get("answers") or {}).items():
            slot = out.setdefault(prov, {})
            for head_id, answer in heads.items():
                if head_id.startswith("_"):
                    continue
                slot.setdefault(head_id, []).append({"_answer": answer, **({"input_sha256": row.get("input_sha256")} if "input_sha256" in row else {})})
    return out


# ── root ──────────────────────────────────────────────────────────────────────


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse(
        {
            "name": "sys1",
            "version": sys1.__version__,
            "docs": "/docs",
            "dashboard": "/dashboard",
            "endpoints": [
                "/v1/classify",
                "/v1/cascade",
                "/v1/health",
                "/v1/tasks",
                "/v1/calibration",
                "/v1/feedback",
                "/v1/providers",
                "/v1/recent",
            ],
        }
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


# ── control dashboard ─────────────────────────────────────────────────────────

_dashboard = _load_dashboard_module()
if _dashboard is not None:
    app.include_router(_dashboard.router)
