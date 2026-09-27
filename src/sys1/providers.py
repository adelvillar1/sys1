"""
Provider implementations.

Each provider is a `Provider` dataclass (see types.py) with a `classify()` and
an `is_available()`. The registry exposes the four built-in providers plus a
`register()` extension point so callers can add their own (e.g. a local vLLM
server, a self-hosted Jev, an Ollama model) without touching this module.

Retry + telemetry semantics are preserved from dev-decisions so existing
dashboards see the same keys: `http_status`, `latency_ms`, `retries`,
`structured_ok`, `token_prompt/completion/total`, plus provider-specific extras.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import api_key_for
from .parsers import parse_decide, parse_jev, parse_local, parse_modernbert
from .types import Answer, Head, Provider

# ── HTTP core (stdlib-only) ──────────────────────────────────────────────────


def http_post_json(
    url: str,
    body: dict | str,
    *,
    api_key: str | None,
    timeout: float,
    max_retries: int,
    backoff: float,
    telemetry: dict,
    extra_headers: dict[str, str] | None = None,
    label: str = "http",
) -> dict:
    """
    POST JSON body to url. Returns parsed JSON response.

    Retries on HTTP 5xx, network errors, and (Fastino-style) "warming" errors
    with exponential backoff. 4xx raises immediately.
    """
    if isinstance(body, str):
        data = body.encode("utf-8")
    else:
        data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)

    last_err = ""
    t0 = time.monotonic()
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                http_status = resp.status
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                _set_telemetry(telemetry, http_status, t0, attempt, structured_ok=False)
                raise RuntimeError(f"{label} returned non-JSON: {raw[:200]!r}") from e

            err = parsed.get("error") if isinstance(parsed, dict) else None
            if err and attempt < max_retries:
                msg = (err or {}).get("message", str(err)) if isinstance(err, dict) else str(err)
                if "warming" in msg.lower() or "temporarily" in msg.lower():
                    _sleep_backoff(backoff, attempt)
                    continue

            if isinstance(parsed, dict) and parsed.get("error"):
                msg = parsed["error"].get("message", str(parsed["error"]))
                _set_telemetry(telemetry, http_status, t0, attempt, structured_ok=False)
                raise RuntimeError(f"Provider error: {msg}")

            _fill_usage(telemetry, parsed)
            _set_telemetry(telemetry, http_status, t0, attempt, structured_ok=True)
            return parsed
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            last_err = f"HTTP {e.code}: {body_text}"
            if 500 <= e.code < 600 and attempt < max_retries:
                _set_telemetry(telemetry, e.code, t0, attempt, structured_ok=False)
                _sleep_backoff(backoff, attempt)
                continue
            _set_telemetry(telemetry, e.code, t0, attempt, structured_ok=False)
            raise RuntimeError(f"{label} {last_err}") from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = str(e)
            if attempt < max_retries:
                _sleep_backoff(backoff, attempt)
                continue
            _set_telemetry(telemetry, None, t0, attempt, structured_ok=False)
            raise RuntimeError(f"{label} request failed: {last_err}") from e

    _set_telemetry(telemetry, None, t0, max_retries, structured_ok=False)
    raise RuntimeError(f"{label} exhausted retries: {last_err}")


def _set_telemetry(
    telemetry: dict,
    http_status: int | None,
    t0: float,
    attempt: int,
    *,
    structured_ok: bool,
) -> None:
    latency_ms = int((time.monotonic() - t0) * 1000)
    telemetry["http_status"] = http_status
    telemetry["latency_ms"] = latency_ms
    telemetry["retries"] = attempt
    telemetry["structured_ok"] = structured_ok


def _fill_usage(telemetry: dict, parsed: Any) -> None:
    usage = (parsed.get("usage") or {}) if isinstance(parsed, dict) else {}
    telemetry["token_prompt"] = usage.get("prompt_tokens")
    telemetry["token_completion"] = usage.get("completion_tokens")
    telemetry["token_total"] = usage.get("total_tokens")


def _sleep_backoff(backoff: float, attempt: int) -> None:
    time.sleep(backoff * (attempt + 1))


# ── Decide provider (hosted Fastino, OpenAI-compatible) ───────────────────────


def _decide_classify(text: str, heads: list[Head], cfg: dict, telemetry: dict) -> dict[str, Answer]:
    pcfg = cfg["providers"]
    api_key = api_key_for("decide", cfg)
    if not api_key:
        raise RuntimeError(f"{pcfg.get('decide_api_key_env', 'FASTINO_API_KEY')} not set")

    wire = [h.to_decide_head() for h in heads]
    body = {
        "model": pcfg["decide_model"],
        "messages": [
            {
                "role": "system",
                "content": "You are a precise change classifier. Answer only with the requested labels.",
            },
            {
                "role": "user",
                "content": f"Review the following text and answer every question.\n\n{text}",
            },
        ],
        "temperature": 0.0,
        "schema": {"classifications": wire},
    }
    resp = http_post_json(
        pcfg["decide_api_url"],
        body,
        api_key=api_key,
        timeout=pcfg["request_timeout_seconds"],
        max_retries=pcfg["max_retries"],
        backoff=pcfg["retry_backoff_seconds"],
        telemetry=telemetry,
        label="decide",
    )
    return parse_decide(resp, heads)


def _decide_available(cfg: dict) -> bool:
    return bool(api_key_for("decide", cfg))


# ── Jev provider (TypeSafe /v1/systemone) ────────────────────────────────────


def _jev_classify(text: str, heads: list[Head], cfg: dict, telemetry: dict) -> dict[str, Answer]:
    pcfg = cfg["providers"]
    api_key = api_key_for("jev", cfg)
    if not api_key:
        raise RuntimeError(f"{pcfg.get('jev_api_key_env', 'TYPESAFE_API_KEY')} not set")

    payload_state: dict[str, Any] = {"text": text[: cfg["scan"]["max_diff_chars"]]}
    payload_questions: dict[str, dict] = {h.id: h.to_jev_head() for h in heads}
    body = {
        "model": pcfg["jev_model"],
        "state": payload_state,
        "questions": payload_questions,
    }
    resp = http_post_json(
        pcfg["jev_api_url"],
        body,
        api_key=api_key,
        timeout=pcfg["request_timeout_seconds"],
        max_retries=pcfg["max_retries"],
        backoff=pcfg["retry_backoff_seconds"],
        telemetry=telemetry,
        label="jev",
    )
    return parse_jev(resp, heads)


def _jev_available(cfg: dict) -> bool:
    return bool(api_key_for("jev", cfg))


# ── Local GLiNER2 provider (subprocess into uv venv) ─────────────────────────

_LOCAL_INLINE = """
import json, sys, io, contextlib
from gliner2 import GLiNER2

# Suppress model load banner so we get clean stdout
old_stdout = sys.stdout
sys.stdout = io.StringIO()

model_name = sys.argv[1]
text = sys.argv[2]
tasks = json.loads(sys.argv[3])

model = GLiNER2.from_pretrained(model_name)

results = {}
for head_id, task in tasks.items():
    r = model.classify_text(
        text,
        tasks={"label": task["labels"]},
        include_confidence=True,
    )
    entry = {}
    if isinstance(r, dict):
        inner = r.get("label") or r
        if isinstance(inner, dict):
            entry["label"] = inner.get("label")
            entry["confidence"] = inner.get("confidence")
        else:
            entry["label"] = str(inner)
    results[head_id] = entry

sys.stdout = old_stdout
print(json.dumps(results))
"""


def _local_classify(text: str, heads: list[Head], cfg: dict, telemetry: dict) -> dict[str, Answer]:
    venv_python = _local_venv_python(cfg)
    if not venv_python.exists():
        raise RuntimeError(f"Local GLiNER venv not found at {venv_python}")

    tasks = {h.id: h.to_local_head() for h in heads}
    max_chars = cfg["scan"]["max_diff_chars"]
    model = cfg["providers"]["local_model"]

    t0 = time.monotonic()
    proc = subprocess.run(
        [str(venv_python), "-c", _LOCAL_INLINE, model, text[:max_chars], json.dumps(tasks)],
        capture_output=True,
        text=True,
        timeout=cfg["providers"]["request_timeout_seconds"],
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    telemetry["latency_ms"] = latency_ms
    telemetry["retries"] = 0
    if proc.returncode != 0:
        telemetry["error_kind"] = "subprocess"
        telemetry["stderr"] = (proc.stderr or "")[:300]
        telemetry["structured_ok"] = False
        raise RuntimeError(f"Local GLiNER failed: {proc.stderr[:300]}")

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = {"_raw": proc.stdout[:200]}
        telemetry["error_kind"] = "json"
        telemetry["structured_ok"] = False

    out = parse_local(parsed, heads)
    null_count = sum(1 for a in out.values() if a.get("label") is None and a.get("noul") is None)
    telemetry["null_label_count"] = null_count
    telemetry["structured_ok"] = True
    return out


def _local_available(cfg: dict) -> bool:
    try:
        return _local_venv_python(cfg).exists()
    except Exception:
        return False


def _local_venv_python(cfg: dict) -> Path:
    return Path(cfg["providers"].get("local_venv", "/private/tmp/gliner-decide")) / "bin" / "python"


# ── ModernBERT eval provider (sentence-encoder similarity, no fine-tuning) ────

_MODERNBERT_INLINE = """
import json, sys, math
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel

model_name = sys.argv[1]
text = sys.argv[2]
tasks = json.loads(sys.argv[3])

tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModel.from_pretrained(model_name)
model.eval()


def to_python(val):
    return val.item() if hasattr(val, "item") else val


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def encode(s):
    inputs = tokenizer(s, return_tensors="pt", truncation=True, max_length=8192, padding=False)
    with torch.no_grad():
        out = model(**inputs)
    cls = out.last_hidden_state[0, 0, :].numpy()
    norm = np.linalg.norm(cls)
    return cls / norm if norm > 0 else cls


try:
    text_emb = encode(text)
    results = {}
    for tid, task in tasks.items():
        labels = task.get("labels", [])
        if not labels:
            results[tid] = {"label": None, "confidence": 0.0}
            continue
        scores = [cosine(text_emb, encode(label)) for label in labels]
        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        sorted_scores = sorted(scores, reverse=True)
        top2_gap = float(sorted_scores[0] - sorted_scores[1]) if len(sorted_scores) > 1 else 1.0
        results[tid] = {
            "label": to_python(labels[best_idx]),
            "confidence": to_python(round(float(scores[best_idx]), 4)),
            "all_scores": {to_python(l): to_python(round(float(s), 4)) for l, s in zip(labels, scores)},
            "top2_gap": to_python(round(top2_gap, 4)),
            "embedding_norm": to_python(round(float(np.linalg.norm(text_emb)), 4)),
        }
    print(json.dumps(results))
except Exception as e:
    print(json.dumps({"_error": str(e)}))
"""


def _modernbert_classify(text: str, heads: list[Head], cfg: dict, telemetry: dict) -> dict[str, Answer]:
    venv_python = _local_venv_python(cfg)
    if not venv_python.exists():
        raise RuntimeError(f"Local venv not found at {venv_python}")

    model = cfg["providers"].get("modernbert_model", "answerdotai/ModernBERT-base")
    max_chars = cfg["scan"]["max_diff_chars"]
    tasks = {h.id: {"labels": h.labels} for h in heads}

    t0 = time.monotonic()
    proc = subprocess.run(
        [str(venv_python), "-c", _MODERNBERT_INLINE, model, text[:max_chars], json.dumps(tasks)],
        capture_output=True,
        text=True,
        timeout=cfg["providers"]["request_timeout_seconds"] * 2,  # slower on CPU
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    telemetry["latency_ms"] = latency_ms
    telemetry["retries"] = 0
    if proc.returncode != 0:
        stderr = (proc.stderr or "")[:300]
        lowered = stderr.lower()
        telemetry["error_kind"] = (
            "timeout" if "timeout" in lowered else "model_load" if "model" in lowered else "other"
        )
        telemetry["stderr"] = stderr
        telemetry["structured_ok"] = False
        raise RuntimeError(f"ModernBERT eval failed: {stderr}")

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = {"_raw": proc.stdout[:200]}
        telemetry["error_kind"] = "json"

    out = parse_modernbert(parsed, heads)
    telemetry["structured_ok"] = True
    return out


def _modernbert_available(cfg: dict) -> bool:
    try:
        return _local_venv_python(cfg).exists()
    except Exception:
        return False


# ── registry ──────────────────────────────────────────────────────────────────

REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    REGISTRY[provider.id] = provider
    return provider


register(
    Provider(
        id="decide",
        name="Fastino GLiNER-2.5-Decide (hosted)",
        classify=_decide_classify,
        is_available=_decide_available,
        description="OpenAI-compatible chat-completions with structured `classifications` schema.",
    )
)
register(
    Provider(
        id="jev",
        name="TypeSafe Jev",
        classify=_jev_classify,
        is_available=_jev_available,
        description="POST /v1/systemone with {state, questions}. Three primitives: choice, noul, score.",
    )
)
register(
    Provider(
        id="local",
        name="GLiNER2.5-Decide (local venv)",
        classify=_local_classify,
        is_available=_local_available,
        description="Subprocess into a uv-managed venv running `gliner2`. No network.",
    )
)
register(
    Provider(
        id="modernbert",
        name="ModernBERT-base (eval, no fine-tuning)",
        classify=_modernbert_classify,
        is_available=_modernbert_available,
        description="Sentence-encoder cosine similarity. Calibration / comparison use, not production.",
    )
)


def resolve_providers(spec: str | list[str]) -> list[str]:
    """Turn a user-facing provider spec (e.g. 'both', 'all', 'decide+jev') into a list of ids."""
    if isinstance(spec, list):
        return [s.strip() for s in spec if s.strip() in REGISTRY]
    s = spec.strip().lower()
    if s in ("both", "all"):
        return ["decide", "jev"] if s == "both" else list(REGISTRY.keys())
    return [p for p in re.split(r"[+,;\s]+", s) if p in REGISTRY] if re.search(r"[+,;\s]", s) else [s]


def call_provider(provider_id: str, text: str, heads: list[Head], cfg: dict, telemetry: dict | None = None) -> dict[str, Answer]:
    provider = REGISTRY.get(provider_id)
    if provider is None:
        raise KeyError(f"unknown provider {provider_id!r}; known: {sorted(REGISTRY)}")
    if not provider.is_available(cfg):
        raise RuntimeError(f"provider {provider_id!r} not available (missing key or venv)")
    tele = telemetry if telemetry is not None else {}
    return provider.classify(text, heads, cfg, tele)


def is_available(provider_id: str, cfg: dict) -> bool:
    provider = REGISTRY.get(provider_id)
    return bool(provider and provider.is_available(cfg))


def available_providers(cfg: dict) -> list[str]:
    return [pid for pid, p in REGISTRY.items() if p.is_available(cfg)]


def _env_snapshot() -> dict[str, str]:
    """Tiny helper for debug logs: which API-key env names are populated?"""
    return {k: "set" for k in ("FASTINO_API_KEY", "TYPESAFE_API_KEY") if os.environ.get(k)}
