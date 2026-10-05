"""
sys1 — reusable System One decision-model abstraction.

One function most callers need:

    import sys1
    result = sys1.classify("decide", "change", staged_diff)
    if result.escalated:
        sys.exit(1)

Providers (Decide, Jev, local GLiNER2, ModernBERT) are pluggable and registered
on import. Candidate providers (CLM-8B, openjev, Kev, Tev1, Laya, the GLiNER
1B/multilingual variants) are wired but opt-in: they speak an existing wire and
stay disabled until listed in `providers.enabled` and their endpoint is up.
Tasks (the head/label sets that name the questions) are
data-driven; nine are pre-registered (change, commit_audit, deps_risk,
docs_drift, api_drift, pr_gate, issue_triage, safety, and sys1's own
vision_qc — the liquid-only artifact-QC task). Load more from TOML or
JSON via `sys1.load_tasks_file(...)`.

The library is stdlib-only. Optional HTTP providers and subprocess local-model
paths use `urllib` + `subprocess`. No hard runtime deps.

The HTTP service lives under `service/` and is a thin FastAPI wrapper around
this same `classify()`. `pip install 'sys1[service]'` pulls the extras.
"""

from __future__ import annotations

from .api import cascade_classify, classify, consistency_classify, health_report
from .config import (
    DEFAULTS,
    api_key_for,
    env_file_path,
    load_config,
    load_provider_env,
    state_file_path,
    write_state_flags,
)
from .gating import answer_confidence, effective_floor, final_exit_code, interpret
from .providers import (
    REGISTRY,
    availability_reason,
    available_providers,
    call_provider,
    enabled_set,
    is_available,
    is_configured,
    is_provider_enabled,
    probe,
    provider_config_view,
    provider_report,
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
    task_from_spec,
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

__version__ = "0.2.0"

__all__ = [
    # constants
    "DEFAULTS",
    "REGISTRY",
    # types
    "Answer",
    "ClassifyResult",
    "Head",
    "HeadKind",
    "Provider",
    "Task",
    "Verdict",
    # functions
    "answer_confidence",
    "api_key_for",
    "availability_reason",
    "available_providers",
    "calibration_curve",
    "call_provider",
    "cascade_classify",
    "classify",
    "confidence_histogram",
    "consistency_classify",
    "detect_task_from_text",
    "effective_floor",
    "enabled_set",
    "env_file_path",
    "final_exit_code",
    "get_task",
    "head_id_from_task",
    "heads_for",
    "health_report",
    "interpret",
    "is_available",
    "is_configured",
    "is_provider_enabled",
    "list_tasks",
    "load_config",
    "load_provider_env",
    "load_tasks_file",
    "log_record",
    "make_choice",
    "make_noul",
    "make_score",
    "probe",
    "provider_agreement",
    "provider_config_view",
    "provider_report",
    "record_feedback",
    "register",
    "register_task",
    "resolve_providers",
    "sanitize_for_log",
    "state_file_path",
    "task_from_spec",
    "write_state_flags",
]
