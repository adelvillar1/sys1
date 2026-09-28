"""
Provider-response → normalized Answer mapping.

Every parser in this module turns one provider's wire shape into the shared
`{head_id: Answer}` dict defined in types.py. The shape mirrors the existing
dev-decisions log keys so JSONL consumers (dashboards, calibration) don't
need a migration.

Tolerant parsing:
  - Hosted Decide returns JSON in chat-completions `choices[0].message.content`.
  - Jev may return either `{answers: {id: {...}}}` directly or wrapped in the
    same chat-completions content shape. We try both.
  - Local GLiNER2 returns a flat JSON dict keyed by task[:40] in the current
    venv script; we canonicalize to head.id.
"""

from __future__ import annotations

from typing import Any

from .types import Answer, Head, declined

# ── Decide (OpenAI-compatible chat-completions) ───────────────────────────────

def parse_decide(resp: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    Hosted Decide response:

      {
        "choices": [{"message": {"content": "<json string>"}}],
        ...
      }

    Content is a JSON string shaped `{ "<task prefix or label>": {"label":
    ..., "confidence": ...}, ... }`. We try to map each parsed key back to a
    head id; keys that match no head get their original raw string key.
    """
    try:
        content = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = ""
    try:
        raw = _maybe_json(content) if isinstance(content, str) else content
    except Exception:
        raw = {"_raw": content if isinstance(content, str) else str(content)}
    if not isinstance(raw, dict):
        return {h.id: declined("shape") for h in heads}

    return _match_keys_to_heads(raw, heads)


# ── Jev (/v1/systemone, both shapes) ──────────────────────────────────────────

def parse_jev(resp: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    Jev response — two accepted shapes:

      a) {"answers": {"<id>": {"type": "choice", "choice": "...", "confidence": ...}}}
      b) chat-completions wrapping (a) in message.content as a JSON string.

    The SDK wire shape is (a). We also handle (b) for OpenRouter-style routes.
    """
    inner: Any = resp
    if "choices" in inner and isinstance(inner["choices"], list) and inner["choices"]:
        msg = (inner["choices"][0] or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            try:
                inner = _maybe_json(content)
            except Exception:
                return {h.id: declined("bad-content") for h in heads}

    answers = inner.get("answers") if isinstance(inner, dict) else None
    if not isinstance(answers, dict):
        return {h.id: declined("no-answers") for h in heads}

    out: dict[str, Answer] = {}
    for head in heads:
        a = answers.get(head.id) or answers.get(head.task)
        if not isinstance(a, dict):
            out[head.id] = declined("missing")
            continue
        out[head.id] = _jev_entry(head, a)
    return out


def _jev_entry(head: Head, a: dict) -> Answer:
    kind = a.get("type") or head.kind
    entry: Answer = {"type": kind}
    if kind == "choice":
        entry["label"] = a.get("choice")
        entry["confidence"] = a.get("confidence")
        probs = a.get("probabilities")
        if isinstance(probs, dict):
            entry["probabilities"] = probs
    elif kind == "score":
        entry["score"] = a.get("score")
        entry["legend"] = a.get("legend")
        entry["confidence"] = a.get("confidence")
    elif kind == "noul":
        raw_noul = a.get("noul")
        if raw_noul is None:
            # Jev sometimes returns the boolean under "label" instead of "noul".
            raw_label = a.get("label")
            if isinstance(raw_label, bool):
                entry["noul"] = raw_label
            elif isinstance(raw_label, str) and raw_label.lower() in ("yes", "no"):
                entry["noul"] = raw_label.lower() == "yes"
            else:
                entry["noul"] = None
        elif isinstance(raw_noul, str) and raw_noul.lower() in ("yes", "no"):
            entry["noul"] = raw_noul.lower() == "yes"
        else:
            entry["noul"] = raw_noul
        entry["confidence"] = a.get("confidence")
    else:
        entry["_raw"] = str(a)
    return entry


# ── Local GLiNER2 (subprocess wrapper) ────────────────────────────────────────

def parse_local(parsed: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    Local GLiNER venv returns either:
      - `{ "<head_id>": {"label": ..., "confidence": ...}, ... }` (our venv)
      - `{ "<task prefix>": {...}, ... }` (dev-decisions legacy)
    We map to head.id either way.
    """
    return _match_keys_to_heads(parsed, heads)


# ── ModernBERT eval ──────────────────────────────────────────────────────────

def parse_modernbert(parsed: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    ModernBERT returns `{ "<task prefix>": {label, confidence, all_scores, ...} }`.
    Keys match by head id, task text, or 40-char prefix. Extra fields ride
    along in the Answer dict for the calibration dashboard.
    """
    out: dict[str, Answer] = {}
    for head in heads:
        raw = _lookup_by_head_keys(parsed, head)
        if not isinstance(raw, dict):
            out[head.id] = declined("missing")
            continue
        if "_error" in raw:
            out[head.id] = declined(f"error:{raw['_error'][:120]}")
            continue
        answer: Answer = {
            "label": raw.get("label"),
            "confidence": raw.get("confidence"),
            "type": "choice",
        }
        for keep in ("all_scores", "top2_gap", "embedding_norm"):
            if keep in raw:
                answer[keep] = raw[keep]
        out[head.id] = answer
    return out


# ── shared helpers ────────────────────────────────────────────────────────────

def _maybe_json(content: str) -> Any:
    import json

    return json.loads(content)


def _match_keys_to_heads(raw: dict, heads: list[Head]) -> dict[str, Answer]:
    """Map a raw provider dict (keyed by task or id) into `{head_id: Answer}`.

    Strategy:
      1. For each head, look up by id, then full task, then 40-char task prefix,
         then any key with a matching 40-char prefix.
      2. Any remaining unknown keys get re-keyed by their raw string so we
         never lose a provider signal — the consumer decides what to do.
    """
    used: set[str] = set()
    out: dict[str, Answer] = {}
    for head in heads:
        raw_key, raw_answer = _lookup_by_head_keys_with_key(raw, head)
        if raw_answer is None:
            out[head.id] = declined("no-match")
            continue
        used.add(raw_key)
        out[head.id] = _as_answer(head, raw_answer)
    for key, value in raw.items():
        if key in used:
            continue
        # keep unmatched keys verbatim, but coerce their values to Answer shape
        out[f"_unmatched:{key}"] = _as_answer(None, value) if isinstance(value, dict) else {"_raw": str(value)}
    return out


def _lookup_by_head_keys(raw: dict, head: Head) -> Any:
    _, value = _lookup_by_head_keys_with_key(raw, head)
    return value


def _lookup_by_head_keys_with_key(raw: dict, head: Head) -> tuple[str | None, Any]:
    # 1. exact key match: id, full task, or 40-char task prefix
    for key in _head_keys(head):
        if key in raw:
            return key, raw[key]
    # 2. prefix match: any raw key whose first 40 chars equal the head task's
    #    first 40 chars (the legacy dev-decisions venv uses task[:40] as key,
    #    and hosted Decide sometimes returns the full task or a different
    #    truncation depending on schema serialization)
    head_prefix = head.task[:40]
    if head_prefix:
        for key, value in raw.items():
            if key == head.id:
                continue
            if key[:40] == head_prefix:
                return key, value
    return None, None


def _head_keys(head: Head) -> list[str]:
    return [head.id, head.task, head.task[:40]]


def _as_answer(head: Head | None, value: dict) -> Answer:
    """Coerce a raw provider entry into the Answer shape. Never loses info."""
    kind = (value.get("type") or head.kind) if head and head.kind else value.get("type", "choice")
    answer: Answer = {"type": kind}
    if kind == "choice":
        answer["label"] = value.get("label", value.get("choice"))
        answer["confidence"] = value.get("confidence")
        probs = value.get("probabilities")
        if isinstance(probs, dict):
            answer["probabilities"] = probs
    elif kind == "score":
        answer["score"] = value.get("score")
        answer["legend"] = value.get("legend")
        answer["confidence"] = value.get("confidence")
    elif kind == "noul":
        raw_noul = value.get("noul")
        if raw_noul is None:
            # Non-Jev providers (Decide, local, ModernBERT, candidates) return
            # their answer under "label". Fall back so we don't discard the
            # signal and force an unnecessary escalation.
            raw_label = value.get("label")
            if isinstance(raw_label, bool):
                answer["noul"] = raw_label
            elif isinstance(raw_label, str) and raw_label.lower() in ("yes", "no"):
                answer["noul"] = raw_label.lower() == "yes"
            else:
                answer["noul"] = None
        else:
            answer["noul"] = raw_noul
        answer["confidence"] = value.get("confidence")
    else:
        answer.update(value)
    # pass through extra diagnostic fields ModernBERT or other providers emit
    for extra in ("all_scores", "top2_gap", "embedding_norm"):
        if extra in value:
            answer[extra] = value[extra]
    if answer.get("label") is None and answer.get("noul") is None and answer.get("score") is None and "_raw" in value:
        answer["_raw"] = value["_raw"]
    return answer
