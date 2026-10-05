"""
Config: defaults, TOML loading, env-var overrides.

Config dict mirrors dev-decisions' shape 1:1 (`providers`, `classify`, `scan`,
`hooks`, `gate`) so existing config files and the JSONL log keys stay compatible.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

DEFAULT_LOG_ROOT = Path.home() / ".local" / "share" / "sys1" / "logs"
DEFAULT_CONFIG_FILES = [
    Path.home() / ".config" / "sys1" / "config.toml",
    Path.home() / ".config" / "dev-decisions" / "config.toml",
]

DEFAULTS: dict = {
    "scan": {
        "max_diff_chars": 12_000,
    },
    "classify": {
        "provider": "decide",        # decide | jev | local | modernbert | both | all
        "block_on_classification": False,
        "confidence_floor": 0.7,
        "escalate_on_null": True,
        "log_dir": str(DEFAULT_LOG_ROOT),
    },
    "providers": {
        # Fastino hosted Decide (OpenAI-compatible chat-completions). Note: the
        # provider id `decide` means this HOSTED wire; the Decide model itself
        # is what the `local` provider runs (below). Not on any consumer's
        # default path — see docs/plans/2026-09-28-fastino-decide-adoption.md.
        "decide_api_url": "https://api.fastino.ai/v1/chat/completions",
        "decide_model": "fastino/GLiNER2.5-Decide",
        "decide_api_key_env": "FASTINO_API_KEY",
        # TypeSafe Jev (/v1/systemone, the SDK shape).
        "jev_api_url": "https://api.typesafe.ai/v1/systemone",
        "jev_model": "jev-1.13.0",
        "jev_api_key_env": "TYPESAFE_API_KEY",
        # Nace Drex (hosted, /v1/systemone wire) — Jev-class decision model
        # with a 131,072-token state window on drex-v1.5. state is a plain
        # string, bounded by drex_max_chars (~headroom under 128K tokens),
        # NOT scan.max_diff_chars — the long context is the feature.
        # `drex-latest` is the documented stable alias for drex-v1.5.
        "drex_api_url": "https://drex.nace.ai/v1/systemone",
        "drex_model": "drex-v1.5",
        "drex_api_key_env": "DREX_API_KEY",
        "drex_max_chars": 400_000,
        # Fastino GLiDE (hosted, /v1/systemone wire) — Jev/Drex-class decision
        # model with a 40,000-token rendered-prompt limit. state is a plain
        # string, bounded by glide_max_chars (~headroom under 40K tokens at
        # ~2.5 chars/token). Strict validation like Drex: no extra `id` field
        # per question; noul criteria keys must be `true`/`false`. Confidence
        # is explicit on choice/score (top1-top2 margin); noul confidence is
        # stripped in parse_glide so gating derives max(p, 1-p) family-
        # consistently. Billing (user-corrected 2026-10-02): same as
        # GLiNER-2.5-Decide hosted billing — NOT free; an earlier
        # "$0 input / $0.04 per 1M output" note here was wrong.
        "glide_api_url": "https://api.fastino.ai/v1/systemone",
        "glide_model": "fastino/glide",
        "glide_api_key_env": "FASTINO_API_KEY",
        "glide_max_chars": 100_000,
        # Supersoniclabs Julia-1 (hosted, /v1/systemone wire) — BERT-based
        # decision model with an 8,192-token window. state is a plain string,
        # bounded by julia_max_chars (headroom at the measured ~2.4-2.5
        # chars/token prose rate; window overflow surfaces as a raw 502, so
        # the client-side bound is the only guard). Strict validation like
        # Drex/GliDE; requires an Idempotency-Key and a User-Agent header
        # (Cloudflare 403s the default Python-urllib signature). Confidence
        # on choice/score is the top1 probability — kept as returned; noul
        # has no confidence field, gating derives max(p, 1-p). Cheapest
        # hosted model in the pool: $0.025 per 1M input tokens, $0 output
        # (user-stated 2026-10-02).
        "julia_api_url": "https://api.supersoniclabs.ai/v1/systemone",
        "julia_model": "julia-1",
        "julia_api_key_env": "SUPERSONIC_API_KEY",
        "julia_max_chars": 12_000,
        # Liquid AI d1 (hosted, /decisions/v1/systemone — TypeSafe's System
        # One API as implemented at api.liquid.ai, including the OpenJev
        # `images` extension) — the pool's first vision-capable decision
        # model. d1 caps each prompt (every question re-renders the whole
        # state, images included) at 65,536 tokens and 250KB of text, and
        # the request body at 4.5MB. Probed live 2026-10-05: ~4.4
        # chars/token on prose (2,173 tokens for 9,639 chars), so
        # liquid_max_chars stays under the token cap even at dense prose
        # rates. Choice/score confidence is the Jev-family distribution
        # statistic (N*max_prob - 1)/(N - 1) — thresholdable at the family
        # floor; noul answers carry no confidence field. $0.04 per 1M
        # input tokens at the probed cost ($4e-8/token), output free.
        "liquid_api_url": "https://api.liquid.ai/decisions/v1/systemone",
        "liquid_model": "d1",
        "liquid_api_key_env": "LIQUID_API_KEY",
        "liquid_max_chars": 150_000,
        # Local GLiNER2 — the fastino-prescribed classification API
        # (gliner2.classification.Classifier). Two serving modes: a load-once
        # classifier server (service/classifier_server.py) when
        # `local_server_url` is set (per-provider `{pid}_server_url` overrides,
        # e.g. a second server for decide_1b's bigger model), else a subprocess
        # into the uv-managed venv. Same answer shapes either way.
        "local_venv": "/private/tmp/gliner-decide",
        "local_model": "fastino/GLiNER2.5-Decide",
        "local_server_url": "",
        # ModernBERT-base as a sentence encoder (no fine-tuning; eval only).
        "modernbert_model": "answerdotai/ModernBERT-base",
        # HTTP transport.
        "request_timeout_seconds": 30,
        "max_retries": 2,
        "retry_backoff_seconds": 5,

        # ── provider flags ──────────────────────────────────────────────────
        # `enabled` is a comma list of: core | all | optin | explicit ids.
        #   "core"                    → the four built-ins (default)
        #   "core,clm"                → built-ins + CLM-8B
        #   "all" / "optin"           → everything registered
        # `disabled` always wins over `enabled` (remove one id from a set).
        # Runtime toggles (the control dashboard) persist to state.json —
        # see SYS1_STATE_FILE; they rewrite these two keys.
        "enabled": "core",
        "disabled": "",

        # ── candidate providers (off until enabled + configured) ────────────
        # CLM-8B (Stanford/NVIDIA state-action scorer) — wire-compatible with
        # TypeSafe Jev: point `clm_api_url` at a running `clm-serve`.
        "clm_api_url": "http://127.0.0.1:8451/v1/systemone",
        "clm_model": "Contrastive-LM/CLM-v0.1-8B",
        "clm_api_key_env": "CLM_API_KEY",
        # openjev / JevK5 — community Jev reproduction, same /v1/systemone shape.
        "openjev_api_url": "http://127.0.0.1:8452/v1/systemone",
        "openjev_model": "openjev-k5",
        "openjev_api_key_env": "OPENJEV_API_KEY",
        # Kev (Jared Palmer; Apache 2.0; 0.8B/4B/9B) — serve behind any
        # OpenAI-compatible endpoint (vLLM, Ollama /v1, llama.cpp).
        "kev_api_url": "http://127.0.0.1:8453/v1/chat/completions",
        "kev_model": "kev-4b",
        "kev_api_key_env": "KEV_API_KEY",
        # Tev1 (Together AI decision model) — hosted OpenAI-compatible.
        "tev1_api_url": "https://api.together.xyz/v1/chat/completions",
        "tev1_model": "TogetherTevalabs/tev1",
        "tev1_api_key_env": "TOGETHER_API_KEY",
        # Laya (Convai; 421M ModernBERT decision model) — OpenAI-compatible
        # when served; swap the url for your endpoint.
        "laya_api_url": "http://127.0.0.1:8454/v1/chat/completions",
        "laya_model": "convai/laya",
        "laya_api_key_env": "LAYA_API_KEY",
        # GLiNER-family variants run through the same local venv as `local`;
        # only the model name differs (zero network).
        "decide_1b_model": "fastino/GLiNER2.5-Decide-1B",
        "decide_multi_model": "fastino/GLiNER2.5-multi-Decide",

        # ── routing (routing.route → cascade_classify's default chain) ──────
        # Deterministic chain policy: per-task override, else the default
        # chain (hosted-only by design — local/modernbert are never
        # auto-routed), size-gated by the provider's documented bound,
        # filtered by availability and the task's own provider allow-list.
        # `enabled = false` restores the legacy ["decide", "jev"] cascade
        # default. See docs/plans/2026-10-01-glide-provider-and-router.md.
        "route": {
            "enabled": True,
            "default_chain": "glide,drex,jev",
            "by_task": {
                # task id -> chain string, e.g. {safety = "jev,glide"}
                # vision_qc has no text-grounded serving provider: d1 is the
                # only pool that reads pixels, and the pair probe
                # (docs/plans/2026-10-05-vision-qc-task.md, 2026-10-05) showed
                # the description-mediated chain missing the artifact on
                # matches_brief 0/7 and on_palette 3/8 vs pixels 5/7 and 6/8.
                "vision_qc": "liquid",
            },
            "long_input_chars": None,  # default: glide_max_chars
        },
    },
    "log": {
        "redact_fields": ("content", "context", "match", "_raw"),
    },
}

ENV_PREFIX = "SYS1_"


def env_file_path() -> Path:
    """Where the provider-key env file lives.

    Resolution order: `$SYS1_ENV_FILE` override → `~/.config/sys1/env`
    (canonical) → `~/.config/dev-decisions/env` (legacy fallback — the keys
    historically lived there; no migration required). The returned path may
    not exist; callers treat that as a no-op.
    """
    env = os.environ.get("SYS1_ENV_FILE")
    if env:
        return Path(env).expanduser()
    canonical = Path.home() / ".config" / "sys1" / "env"
    if canonical.exists():
        return canonical
    return Path.home() / ".config" / "dev-decisions" / "env"


def load_provider_env() -> dict:
    """Load the provider env file into `os.environ` (setdefault semantics —
    a value the caller exported always wins).

    sys1 is the canonical provider of sys1 calls: consumers get the full
    provider pool from their own shell, without sourcing anything. Returns
    `{"path": str | None, "loaded": [key names]}` — names only, never values
    (same line the rest of the codebase holds about key material).
    """
    out: dict = {"path": None, "loaded": []}
    path = env_file_path()
    if not path.exists():
        return out
    out["path"] = str(path)
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            name, _, val = line.partition("=")
            name = name.strip()
            if not name or not name.replace("_", "a").isalnum():
                continue  # not a plausible env name; skip the line
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if name not in os.environ:
                os.environ[name] = val
                out["loaded"].append(name)
    except Exception:
        pass  # a broken env file never breaks the tool
    return out


def load_config(
    extra_paths: list[str | Path] | None = None,
    *,
    repo_root: Path | None = None,
) -> dict:
    """Merge DEFAULTS ← discovered TOML files ← repo-local TOML ← env vars.

    Discovery order for TOML files (later wins):
      1. `~/.config/sys1/config.toml` (if exists)
      2. `~/.config/dev-decisions/config.toml` (if exists, for compat)
      3. Each path in `extra_paths` (caller-provided, must exist)
      4. `<repo_root>/.sys1.toml` and `<repo_root>/.dev-decisions.toml`
    """
    # deepcopy, not dict(): nested sections (providers.route) must not be
    # shared by reference across load_config() calls — a caller mutating
    # cfg["providers"]["route"] would otherwise mutate the DEFAULTS module
    # global and leak into every later load.
    cfg: dict[str, Any] = copy.deepcopy(DEFAULTS)

    # the layer self-configures: provider keys come from the env file unless
    # the caller exported their own (load_provider_env setdefaults) — before
    # the env-override loop below, so SYS1_* names in the file also apply.
    load_provider_env()

    files_to_load: list[Path] = [p for p in DEFAULT_CONFIG_FILES if p.exists()]
    if extra_paths:
        for p in extra_paths:
            path = Path(p).expanduser()
            if path.exists():
                files_to_load.append(path)
    if repo_root:
        for name in (".sys1.toml", ".dev-decisions.toml"):
            candidate = repo_root / name
            if candidate.exists():
                files_to_load.append(candidate)

    for path in files_to_load:
        try:
            import tomllib  # 3.11+; the dev-decisions path is 3.10+ so guard.

            with open(path, "rb") as f:
                user = tomllib.load(f)
            _deep_merge(cfg, user)
        except Exception:
            pass  # a broken config never breaks the tool

    # env overrides: SYS1_PROVIDERS__JEV_MODEL=... etc
    for key, val in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX) :].lower().split("__")
        _set_nested(cfg, path, _coerce(val))

    # `SYS1_LOG_DIR` is the documented single-name override for the JSONL
    # location (service/Dockerfile sets it, service/main.py's header and
    # FUNCTIONAL-SPECIFICATIONS.md advertise it). The generic loop above can
    # only map that name to a top-level `log_dir`, which nothing reads — so bind
    # it to the section here. The section-qualified `SYS1_CLASSIFY__LOG_DIR` is
    # more specific: the loop already applied it, and a non-empty one suppresses
    # the alias.
    alias_log_dir = os.environ.get(f"{ENV_PREFIX}LOG_DIR")
    if alias_log_dir and not os.environ.get(f"{ENV_PREFIX}CLASSIFY__LOG_DIR"):
        cfg["classify"]["log_dir"] = alias_log_dir

    # runtime flag state (written by the control dashboard) wins over TOML/env
    _apply_state_file(cfg)

    return cfg


def state_file_path() -> Path:
    """Where runtime provider-flag toggles persist."""
    env = os.environ.get("SYS1_STATE_FILE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "sys1" / "state.json"


def _apply_state_file(cfg: dict) -> None:
    path = state_file_path()
    if not path.exists():
        return
    try:
        import json

        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return  # a broken state file never breaks the tool
    prov = state.get("providers") or {}
    if "enabled" in prov:
        cfg["providers"]["enabled"] = ",".join(str(x) for x in prov["enabled"])
    if "disabled" in prov:
        cfg["providers"]["disabled"] = ",".join(str(x) for x in prov["disabled"])


def write_state_flags(enabled_ids: list[str], disabled_ids: list[str]) -> Path:
    """Persist runtime provider-flag state (used by the dashboard toggles)."""
    import json

    path = state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"providers": {"enabled": sorted(enabled_ids), "disabled": sorted(disabled_ids)}},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _set_nested(d: dict, path: list[str], val: Any) -> None:
    cur = d
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    cur[path[-1]] = val


def _coerce(val: str):
    lowered = val.lower()
    if lowered in ("true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def api_key_for(provider_id: str, cfg: dict) -> str | None:
    """Resolve the env-var name from the provider's `*_api_key_env` config."""
    env_name = cfg["providers"].get(f"{provider_id}_api_key_env")
    if env_name:
        return os.environ.get(env_name)
    # legacy compat with dev-decisions: hardcoded names
    legacy = {"decide": "FASTINO_API_KEY", "jev": "TYPESAFE_API_KEY"}
    return os.environ.get(legacy.get(provider_id, ""))
