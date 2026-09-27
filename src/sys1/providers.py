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
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import api_key_for, load_config
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


# ── Wire factories ────────────────────────────────────────────────────────────
#
# Every provider's classify()/is_available() is generated from its `wire`
# family + config keys (`{pid}_api_url`, `{pid}_model`, `{pid}_api_key_env`,
# `{pid}_requires_key`). Adding a new model that speaks an existing wire is a
# config entry + one register() call — no new call code.

def _openai_wire(pid: str, *, system_prompt: str | None = None):
    """Classify fn for OpenAI-compatible chat-completions with a `classifications` schema."""

    def _fn(text: str, heads: list[Head], cfg: dict, telemetry: dict) -> dict[str, Answer]:
        pcfg = cfg["providers"]
        api_key = api_key_for(pid, cfg)
        if _requires_key(pid, cfg) and not api_key:
            raise RuntimeError(f"${pcfg.get(f'{pid}_api_key_env', pid.upper() + '_API_KEY')} not set")
        url = pcfg.get(f"{pid}_api_url", "")
        if not url:
            raise RuntimeError(f"providers.{pid}_api_url not configured")
        body = {
            "model": pcfg.get(f"{pid}_model", pid),
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                    or "You are a precise change classifier. Answer only with the requested labels.",
                },
                {
                    "role": "user",
                    "content": f"Review the following text and answer every question.\n\n{text}",
                },
            ],
            "temperature": 0.0,
            "schema": {"classifications": [h.to_decide_head() for h in heads]},
        }
        resp = http_post_json(
            url,
            body,
            api_key=api_key,
            timeout=pcfg["request_timeout_seconds"],
            max_retries=pcfg["max_retries"],
            backoff=pcfg["retry_backoff_seconds"],
            telemetry=telemetry,
            label=pid,
        )
        return parse_decide(resp, heads)

    return _fn


def _jev_wire(pid: str):
    """Classify fn for the TypeSafe /v1/systemone shape ({state, questions})."""

    def _fn(text: str, heads: list[Head], cfg: dict, telemetry: dict) -> dict[str, Answer]:
        pcfg = cfg["providers"]
        api_key = api_key_for(pid, cfg)
        if _requires_key(pid, cfg) and not api_key:
            raise RuntimeError(f"${pcfg.get(f'{pid}_api_key_env', pid.upper() + '_API_KEY')} not set")
        url = pcfg.get(f"{pid}_api_url", "")
        if not url:
            raise RuntimeError(f"providers.{pid}_api_url not configured")
        body = {
            "model": pcfg.get(f"{pid}_model", pid),
            "state": {"text": text[: cfg["scan"]["max_diff_chars"]]},
            "questions": {h.id: h.to_jev_head() for h in heads},
        }
        resp = http_post_json(
            url,
            body,
            api_key=api_key,
            timeout=pcfg["request_timeout_seconds"],
            max_retries=pcfg["max_retries"],
            backoff=pcfg["retry_backoff_seconds"],
            telemetry=telemetry,
            label=pid,
        )
        return parse_jev(resp, heads)

    return _fn


def _gliner_wire(pid: str, model_key: str = "local_model"):
    """Classify fn for GLiNER2-family models run through the local uv venv."""

    def _fn(text: str, heads: list[Head], cfg: dict, telemetry: dict) -> dict[str, Answer]:
        venv_python = _local_venv_python(cfg)
        if not venv_python.exists():
            raise RuntimeError(f"Local GLiNER venv not found at {venv_python}")

        tasks = {h.id: h.to_local_head() for h in heads}
        max_chars = cfg["scan"]["max_diff_chars"]
        model = cfg["providers"].get(model_key) or cfg["providers"]["local_model"]

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

    return _fn


# ── Availability primitives (flag-independent; the gate lives in the registry) ─


def _requires_key(pid: str, cfg: dict) -> bool:
    """
    Whether the provider's API key env var is mandatory.

    - Core hosted providers (decide, jev) always require one.
    - Remote candidate endpoints (e.g. api.together.xyz for Tev1) require one.
    - Self-hosted candidates (localhost URLs) default to no auth; the key env
      var, if set, is sent as a bearer token anyway. Force either way with
      `{pid}_requires_key = true/false` in config.
    """
    pcfg = cfg.get("providers", {})
    explicit = pcfg.get(f"{pid}_requires_key")
    if explicit is not None:
        return bool(explicit)
    if pid in ("decide", "jev"):
        return True
    url = pcfg.get(f"{pid}_api_url", "")
    if url:
        return not _is_localhost(url)
    return False


def _key_available(pid: str, cfg: dict) -> bool:
    """True when no key is required, or the key env var is set."""
    return (not _requires_key(pid, cfg)) or bool(api_key_for(pid, cfg))


def _tcp_open(url: str, timeout: float = 0.5) -> bool:
    """Fast reachability probe for a self-hosted endpoint (connects, no request sent)."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_localhost(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _url_for(pid: str, cfg: dict) -> str:
    return cfg.get("providers", {}).get(f"{pid}_api_url", "")


def _hosted_available(pid: str):
    """Hosted providers: available iff the required key is present."""

    def _fn(cfg: dict) -> bool:
        return _key_available(pid, cfg)

    return _fn


def _self_hosted_available(pid: str):
    """Self-hosted candidates: configured + key (when required) + a listener on the port."""

    def _fn(cfg: dict) -> bool:
        url = _url_for(pid, cfg)
        if not url:
            return False
        if not _key_available(pid, cfg):
            return False
        return _tcp_open(url)

    return _fn


def _venv_available(_cfg: dict) -> bool:
    try:
        return _local_venv_python(_cfg).exists()
    except Exception:
        return False


def _endpoint_configured(pid: str):
    """Static config check for the dashboard: endpoint present, key satisfied (no TCP)."""

    def _fn(cfg: dict) -> bool:
        url = _url_for(pid, cfg)
        if not url:
            return False
        return _key_available(pid, cfg)

    return _fn


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


# ── core providers (tier="core": enabled by default) ─────────────────────────

register(
    Provider(
        id="decide",
        name="Fastino GLiNER-2.5-Decide (hosted)",
        classify=_openai_wire("decide"),
        is_available=_hosted_available("decide"),
        is_configured=_hosted_available("decide"),
        description="OpenAI-compatible chat-completions with structured `classifications` schema.",
        tier="core",
        wire="openai",
    )
)
register(
    Provider(
        id="jev",
        name="TypeSafe Jev",
        classify=_jev_wire("jev"),
        is_available=_hosted_available("jev"),
        is_configured=_hosted_available("jev"),
        description="POST /v1/systemone with {state, questions}. Three primitives: choice, noul, score.",
        tier="core",
        wire="jev",
    )
)
register(
    Provider(
        id="local",
        name="GLiNER2.5-Decide (local venv)",
        classify=_gliner_wire("local", "local_model"),
        is_available=_venv_available,
        is_configured=_venv_available,
        description="Subprocess into a uv-managed venv running `gliner2`. No network.",
        tier="core",
        wire="gliner",
    )
)
register(
    Provider(
        id="modernbert",
        name="ModernBERT-base (eval, no fine-tuning)",
        classify=_modernbert_classify,
        is_available=_modernbert_available,
        is_configured=_modernbert_available,
        description="Sentence-encoder cosine similarity. Calibration / comparison use, not production.",
        tier="core",
        wire="modernbert",
    )
)

# ── candidate providers (tier="candidate": opt-in via providers.enabled) ──────
#
# These speak an existing wire; the classify fns are real and fully functional.
# They stay disabled until named in `providers.enabled` (TOML/env/state-file),
# and report `configured=False` / `available=False` until their endpoint is
# actually set up. When you stand up one of these models locally, point its
# `{pid}_api_url` at it and flip the flag — no code change needed.

register(
    Provider(
        id="clm",
        name="CLM-8B (Contrastive-LM, self-hosted)",
        classify=_jev_wire("clm"),
        is_available=_self_hosted_available("clm"),
        is_configured=_endpoint_configured("clm"),
        description="Stanford/NVIDIA state-action scorer. TypeSafe /v1/systemone wire-compatible: "
        "run `clm-serve`, set clm_api_url, enable.",
        tier="candidate",
        wire="jev",
        meta={"setup": "clm-serve on 127.0.0.1:8451 (or any /v1/systemone-compatible host)"},
    )
)
register(
    Provider(
        id="openjev",
        name="openjev / JevK5 (community reproduction)",
        classify=_jev_wire("openjev"),
        is_available=_self_hosted_available("openjev"),
        is_configured=_endpoint_configured("openjev"),
        description="Open reproduction of Jev behind the same /v1/systemone shape. Not TypeSafe's Jev.",
        tier="candidate",
        wire="jev",
        meta={"setup": "serve openjev behind a /v1/systemone-compatible endpoint"},
    )
)
register(
    Provider(
        id="kev",
        name="Kev (Jared Palmer, Apache 2.0)",
        classify=_openai_wire("kev"),
        is_available=_self_hosted_available("kev"),
        is_configured=_endpoint_configured("kev"),
        description="0.8B/4B/9B Apache-2.0 decision model. Serve behind any OpenAI-compatible "
        "endpoint (vLLM, Ollama /v1, llama.cpp); set kev_model to match.",
        tier="candidate",
        wire="openai",
        meta={"setup": "vllm serve kev-4b (or Ollama) behind /v1/chat/completions"},
    )
)
register(
    Provider(
        id="tev1",
        name="Tev1 (Together AI)",
        classify=_openai_wire("tev1"),
        is_available=_hosted_available("tev1"),
        is_configured=_endpoint_configured("tev1"),
        description="Together AI decision model (Qwen3.5-4B fine-tune). Hosted, OpenAI-compatible; "
        "needs TOGETHER_API_KEY. Confirm tev1_model against Together's catalog.",
        tier="candidate",
        wire="openai",
        meta={"setup": "export TOGETHER_API_KEY=... and confirm tev1_model"},
    )
)
register(
    Provider(
        id="laya",
        name="Laya (Convai, 421M ModernBERT)",
        classify=_openai_wire("laya"),
        is_available=_self_hosted_available("laya"),
        is_configured=_endpoint_configured("laya"),
        description="Convai's 421M decision model. Wire assumptions are provisional — "
        "adjust laya_api_url / the wire factory when its serving shape is confirmed.",
        tier="candidate",
        wire="openai",
        meta={"setup": "serve Laya behind an OpenAI-compatible endpoint (wire unconfirmed)"},
    )
)
register(
    Provider(
        id="decide_1b",
        name="GLiNER2.5-Decide-1B (local venv)",
        classify=_gliner_wire("decide_1b", "decide_1b_model"),
        is_available=_venv_available,
        is_configured=_venv_available,
        description="1.19B Ettin-encoder variant of the local Decide model; same venv, bigger head.",
        tier="candidate",
        wire="gliner",
        meta={"setup": "uv pip install gliner2 in local_venv; first run downloads the model"},
    )
)
register(
    Provider(
        id="decide_multi",
        name="GLiNER2.5-multi-Decide (local venv)",
        classify=_gliner_wire("decide_multi", "decide_multi_model"),
        is_available=_venv_available,
        is_configured=_venv_available,
        description="287M multilingual boundary variant of the local Decide model.",
        tier="candidate",
        wire="gliner",
        meta={"setup": "uv pip install gliner2 in local_venv; first run downloads the model"},
    )
)


# ── provider flags ────────────────────────────────────────────────────────────

_CORE_TOKEN = "core"
_ALL_TOKENS = frozenset({"all", "*"})
_CANDIDATE_TOKENS = frozenset({"optin", "opt_in", "opt-in", "experimental", "candidates", "candidate"})


def _split_ids(raw) -> list[str]:
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    return [p.strip().lower() for p in re.split(r"[+,;\s]+", str(raw or "")) if p.strip()]


def enabled_set(cfg: dict) -> set[str]:
    """
    Resolve `providers.enabled` tokens against the registry, minus `providers.disabled`.

    Tokens: "core" (all tier=core), "all"/"*" (every registered id),
    "optin"/"candidates"/"experimental" (every tier=candidate), or explicit ids.
    An explicit id that isn't registered is kept anyway (custom providers may
    register later); the disabled list always wins.
    """
    pcfg = cfg.get("providers", {})
    tokens = _split_ids(pcfg.get("enabled", _CORE_TOKEN)) or [_CORE_TOKEN]
    ids: set[str] = set()
    for tok in tokens:
        if tok == _CORE_TOKEN:
            ids |= {p.id for p in REGISTRY.values() if p.tier == "core"}
        elif tok in _ALL_TOKENS:
            ids |= set(REGISTRY)
        elif tok in _CANDIDATE_TOKENS:
            ids |= {p.id for p in REGISTRY.values() if p.tier != "core"}
        else:
            ids.add(tok)
    ids -= set(_split_ids(pcfg.get("disabled", "")))
    return ids


def is_provider_enabled(pid: str, cfg: dict) -> bool:
    return str(pid).strip().lower() in enabled_set(cfg)


def is_configured(pid: str, cfg: dict) -> bool:
    """Static config check (endpoint present + key satisfied), ignoring the flag."""
    provider = REGISTRY.get(pid)
    if provider is None:
        return False
    check = provider.is_configured or provider.is_available
    try:
        return bool(check(cfg))
    except Exception:
        return False


def availability_reason(pid: str, cfg: dict) -> str:
    """Human sentence for why a provider is off — the doctor/dashboard text."""
    provider = REGISTRY.get(pid)
    if provider is None:
        return "not registered"
    if pid not in enabled_set(cfg):
        return "disabled by providers.enabled flag"
    pcfg = cfg.get("providers", {})
    if provider.wire in ("gliner", "modernbert"):
        venv = pcfg.get("local_venv", "/private/tmp/gliner-decide")
        return "available" if _venv_available(cfg) else f"venv not found at {venv}"
    if provider.is_available(cfg):
        return "available"
    url = _url_for(pid, cfg)
    if not url:
        return f"providers.{pid}_api_url not set"
    if _requires_key(pid, cfg) and not api_key_for(pid, cfg):
        env = pcfg.get(f"{pid}_api_key_env") or f"{pid.upper()}_API_KEY"
        return f"missing API key: ${env}"
    if _is_localhost(url) and not _tcp_open(url):
        return f"no listener on {url}"
    return "unavailable"


def provider_config_view(pid: str, cfg: dict) -> dict:
    """The config surface a dashboard needs per provider (never the key value)."""
    pcfg = cfg.get("providers", {})
    wire = REGISTRY[pid].wire if pid in REGISTRY else "openai"
    if wire == "gliner":
        model_key = {"local": "local_model", "decide_1b": "decide_1b_model", "decide_multi": "decide_multi_model"}.get(
            pid, f"{pid}_model"
        )
        return {"kind": "venv", "venv": pcfg.get("local_venv", ""), "model": pcfg.get(model_key, "")}
    if wire == "modernbert":
        return {"kind": "venv", "venv": pcfg.get("local_venv", ""), "model": pcfg.get("modernbert_model", "")}
    return {
        "kind": "url",
        "url": _url_for(pid, cfg),
        "model": pcfg.get(f"{pid}_model", pid),
        "api_key_env": pcfg.get(f"{pid}_api_key_env") or None,
        "requires_key": _requires_key(pid, cfg),
        "api_key_set": bool(api_key_for(pid, cfg)),
    }


def probe(pid: str, cfg: dict | None = None, timeout: float = 1.0) -> dict:
    """Cheap reachability probe: TCP connect for URL providers, stat for venvs."""
    cfg = cfg or load_config()
    provider = REGISTRY.get(pid)
    if provider is None:
        return {"ok": False, "detail": "not registered"}
    t0 = time.monotonic()
    if provider.wire in ("gliner", "modernbert"):
        ok = _venv_available(cfg)
        return {
            "ok": ok,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "detail": f"venv at {cfg['providers'].get('local_venv', '')}" + ("" if ok else " (missing)"),
        }
    url = _url_for(pid, cfg)
    if not url:
        return {"ok": False, "detail": f"providers.{pid}_api_url not set"}
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        return {"ok": False, "latency_ms": int((time.monotonic() - t0) * 1000), "detail": f"{host}:{port} — {e}"}
    return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000), "detail": f"{host}:{port} reachable"}


def provider_report(cfg: dict | None = None) -> dict[str, dict]:
    """
    Full per-provider view for the control dashboard and `sys1 doctor`.

    Per id: name/description/tier/wire, the three flags (enabled / configured /
    available — available means enabled AND usable right now), a human reason,
    the non-secret config view, and the registration meta.
    """
    cfg = cfg or load_config()
    out: dict[str, dict] = {}
    for pid, provider in REGISTRY.items():
        try:
            enabled = pid in enabled_set(cfg)
        except Exception:
            enabled = True
        try:
            usable = bool(provider.is_available(cfg))
        except Exception:
            usable = False
        out[pid] = {
            "id": pid,
            "name": provider.name,
            "description": provider.description,
            "tier": provider.tier,
            "wire": provider.wire,
            "enabled": enabled,
            "configured": is_configured(pid, cfg),
            "available": enabled and usable,
            "reason": availability_reason(pid, cfg),
            "config": provider_config_view(pid, cfg),
            "meta": provider.meta,
        }
    return out


def resolve_providers(spec: str | list[str]) -> list[str]:
    """
    Turn a user-facing provider spec into a list of ids.

    Aliases: "both" (decide+jev, the dev-decisions legacy), "core" (the four
    built-ins), "optin"/"candidates" (every registered candidate), "all"
    (everything). Unregistered tokens are dropped.
    """
    if isinstance(spec, list):
        return [s.strip().lower() for s in spec if s.strip().lower() in REGISTRY]
    s = spec.strip().lower()
    if s == "both":
        return [p for p in ("decide", "jev") if p in REGISTRY]
    if s == "all":
        return list(REGISTRY.keys())
    if s == _CORE_TOKEN:
        return [p for p, prov in REGISTRY.items() if prov.tier == "core"]
    if s in _CANDIDATE_TOKENS:
        return [p for p, prov in REGISTRY.items() if prov.tier != "core"]
    if re.search(r"[+,;\s]", s):
        return [p for p in re.split(r"[+,;\s]+", s) if p in REGISTRY]
    return [s]


def call_provider(
    provider_id: str, text: str, heads: list[Head], cfg: dict, telemetry: dict | None = None
) -> dict[str, Answer]:
    provider = REGISTRY.get(provider_id)
    if provider is None:
        raise KeyError(f"unknown provider {provider_id!r}; known: {sorted(REGISTRY)}")
    if provider_id not in enabled_set(cfg):
        raise RuntimeError(
            f"provider {provider_id!r} is disabled — add it to providers.enabled "
            f"(e.g. \"core,{provider_id}\") or toggle it in the control dashboard"
        )
    if not provider.is_available(cfg):
        raise RuntimeError(f"provider {provider_id!r} unavailable: {availability_reason(provider_id, cfg)}")
    tele = telemetry if telemetry is not None else {}
    return provider.classify(text, heads, cfg, tele)


def is_available(provider_id: str, cfg: dict) -> bool:
    """Flag-aware availability: enabled AND the provider's own check passing."""
    provider = REGISTRY.get(provider_id)
    return bool(provider and provider_id in enabled_set(cfg) and provider.is_available(cfg))


def available_providers(cfg: dict) -> list[str]:
    return [pid for pid, p in REGISTRY.items() if pid in enabled_set(cfg) and p.is_available(cfg)]
