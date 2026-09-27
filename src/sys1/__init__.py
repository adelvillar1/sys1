"""
sys1 — reusable System One decision-model abstraction.

One function most callers need:

    import sys1
    result = sys1.classify("decide", "change", staged_diff)
    if result.escalated:
        sys.exit(1)

Providers (Decide, Jev, local GLiNER2, ModernBERT) are pluggable and registered
on import. Tasks (the head/label sets that name the questions) are
data-driven; eight are pre-registered (change, commit_audit, deps_risk,
docs_drift, api_drift, pr_gate, issue_triage, safety). Load more from TOML or
JSON via `sys1.load_tasks_file(...)`.

The library is stdlib-only. Optional HTTP providers and subprocess local-model
paths use `urllib` + `subprocess`. No hard runtime deps.

The HTTP service lives under `service/` and is a thin FastAPI wrapper around
this same `classify()`. `pip install 'sys1[service]'` pulls the extras.
"""

from __future__ import annotations

from .api import cascade_classify, classify, health_report
from .config import DEFAULTS, api_key_for, load_config
from .gating import final_exit_code, interpret
from .providers import (
    REGISTRY,
    available_providers,
    call_provider,
    is_available,
    register,
    resolve_providers,
)
from .tasks import (
    detect_task_from_text,
    get_task,
    heads_for,
    list_tasks,
    load_tasks_file,
    register_task,
)
from .telemetry import (
    calibration_curve,
    confidence_histogram,
    log_record,
    provider_agreement,
    record_feedback,
    sanitize_for_log,
)
from .types import (
    Answer,
    ClassifyResult,
    Head,
    HeadKind,
    Provider,
    Task,
    Verdict,
    head_id_from_task,
    make_choice,
    make_noul,
    make_score,
)

__version__ = "0.1.0"

__all__ = [
    # core
    "classify",
    "cascade_classify",
    "health_report",
    # config
    "load_config",
    "api_key_for",
    "DEFAULTS",
    # types
    "Head",
    "HeadKind",
    "Task",
    "Provider",
    "Answer",
    "ClassifyResult",
    "Verdict",
    "make_choice",
    "make_noul",
    "make_score",
    "head_id_from_task",
    # providers
    "REGISTRY",
    "register",
    "call_provider",
    "is_available",
    "available_providers",
    "resolve_providers",
    # tasks
    "register_task",
    "get_task",
    "list_tasks",
    "heads_for",
    "detect_task_from_text",
    "load_tasks_file",
    # gating
    "interpret",
    "final_exit_code",
    # telemetry
    "log_record",
    "sanitize_for_log",
    "confidence_histogram",
    "provider_agreement",
    "record_feedback",
    "calibration_curve",
]
