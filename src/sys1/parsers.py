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


# ── Drex (/v1/systemone, nace.ai) ─────────────────────────────────────────────

def parse_drex(resp: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    Drex response — always the SDK shape `{"answers": {id: {...}}}` (chat-
    completions wrapping tolerated for symmetry with parse_jev).

    Per-kind deltas vs Jev (docs.nace.ai, llms-full.txt):
      - choice: {type, choice, probabilities, confidence} — `choice` renames
        to sys1's `label` (handled by _jev_entry).
      - noul: {type, noul: p} — identical semantics (p IS the certainty; no
        confidence field; gating derives max(p, 1-p)).
      - score: Drex returns `score = Σ index x probability` over the 0-indexed
        criteria array. sys1 scales run score_min..score_max, so the score is
        shifted by head.score_min, and Drex's returned legend ARRAY becomes
        sys1's {level: description} dict.
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
        entry = _jev_entry(head, a)
        if entry.get("type") == "score" and head.kind == "score":
            score = entry.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                entry["score"] = float(head.score_min) + float(score)
            legend = entry.get("legend")
            if isinstance(legend, list):
                entry["legend"] = {
                    str(head.score_min + i): str(level) for i, level in enumerate(legend)
                }
        out[head.id] = entry
    return out


# ── GLiDE (fastino /v1/systemone) ─────────────────────────────────────────────

def parse_glide(resp: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    GLiDE response — the SDK shape `{"answers": {id: {...}}}` (chat-completions
    wrapping tolerated for symmetry with parse_jev/parse_drex).

    Per-kind deltas vs Drex (docs.fastino.ai/concepts/decision-models, probed
    live 2026-10-01):
      - choice: {type, choice, confidence, probabilities} — confidence is the
        documented top1-top2 margin; kept as returned (same flavor as the
        Jev family per gating.py's docstring).
      - noul: {type, noul: p, confidence: |2p-1|} — the explicit confidence
        is STRIPPED: on the |2p-1| scale a p=0.85 answer scores 0.70, which
        would make the shared Jev-family floor stricter than jev/drex for the
        same answer. Gating derives max(p, 1-p) instead (family-consistent).
      - score: GLiDE is 0-indexed over the criteria array like Drex (score
        shifted by head.score_min), but probabilities/legend arrive as a dict
        keyed by index STRING ("0".."N-1") — the legend keys shift directly
        (no array->dict conversion needed). `expected_level` (Σ index x p)
        rides along as a calibration extra.
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
        entry = _jev_entry(head, a)
        if entry.get("type") == "score" and head.kind == "score":
            score = entry.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                entry["score"] = float(head.score_min) + float(score)
            legend = entry.get("legend")
            if isinstance(legend, dict):
                entry["legend"] = {
                    str(head.score_min + int(k)): str(v) for k, v in legend.items()
                }
            if a.get("expected_level") is not None:
                entry["expected_level"] = a["expected_level"]
        if entry.get("type") == "noul":
            entry.pop("confidence", None)
        out[head.id] = entry
    return out


def parse_julia(resp: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    Julia-1 response — `{"answers": {id: {...}}}` (probed live 2026-10-02;
    deltas quoted in docs/plans/2026-10-02-julia-provider.md).

    Per-kind deltas vs GliDE:
      - choice: {type, choice, confidence, probabilities} — confidence is the
        top1 probability (byte-equals probabilities[top1] on the wire), a
        probability scale directly thresholdable at the family floor; KEPT as
        returned (no margin-strip decision like GliDE's).
      - noul: {type, noul: p} — no confidence field exists on this wire, so
        gating derives max(p, 1-p) family-consistently with nothing to strip.
      - score: 0-indexed over the criteria array like Drex/GliDE (score
        shifted by head.score_min); probabilities keyed by index STRING
        ("0".."N-1"), and **legend arrives as null** (Drex sends an array,
        GliDE an index-keyed dict) — synthesized from head.labels shifted by
        score_min so score answers carry the same legend shape downstream.
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
        entry = _jev_entry(head, a)
        if entry.get("type") == "score" and head.kind == "score":
            score = entry.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                entry["score"] = float(head.score_min) + float(score)
            legend = entry.get("legend")
            if isinstance(legend, dict):
                entry["legend"] = {
                    str(head.score_min + int(k)): str(v) for k, v in legend.items()
                }
            else:
                # the criteria array to_drex_head() sent IS the level legend,
                # in wire order (index 0 = lowest)
                entry["legend"] = {
                    str(head.score_min + i): str(label)
                    for i, label in enumerate(head._score_criteria())
                }
        out[head.id] = entry
    return out


# ── Liquid d1 (api.liquid.ai /decisions/v1/systemone) ─────────────────────────

def parse_liquid(resp: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    Liquid d1 response — `{"answers": {id: {...}}}` on the System One wire
    (probed live 2026-10-05; deltas quoted in
    docs/plans/2026-10-05-liquid-d1-provider.md).

    Per-kind deltas vs GliDE:
      - choice: {type, choice, probabilities, confidence} — confidence is the
        Jev-family distribution statistic (N*max_prob - 1)/(N - 1), verified
        byte-equal on the wire (3 options, top1 p=0.9306 -> conf 0.8958), NOT
        a top1-top2 margin — directly thresholdable at the family floor; KEPT
        as returned.
      - noul: {type, noul: p} — no confidence field on this wire (same as
        Julia, unlike GliDE which strips one); gating derives max(p, 1-p)
        family-consistently with nothing to strip.
      - score: 0-indexed expected level over the criteria array like
        Drex/GliDE (shifted by head.score_min); probabilities and legend
        arrive as dicts keyed by index STRING ("0".."N-1"), so the legend keys
        shift directly. No `expected_level` extra — d1 doesn't send one.
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
        entry = _jev_entry(head, a)
        if entry.get("type") == "score" and head.kind == "score":
            score = entry.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                entry["score"] = float(head.score_min) + float(score)
            legend = entry.get("legend")
            if isinstance(legend, dict):
                entry["legend"] = {
                    str(head.score_min + int(k)): str(v) for k, v in legend.items()
                }
            else:
                entry["legend"] = {
                    str(head.score_min + i): str(label)
                    for i, label in enumerate(head._score_criteria())
                }
        out[head.id] = entry
    return out


# ── Local GLiNER2 (subprocess wrapper) ────────────────────────────────────────

def parse_local(parsed: dict, heads: list[Head]) -> dict[str, Answer]:
    """
    Local GLiNER venv returns either:
      - `{ "<head_id>": {"label": ..., "confidence": ...}, ... }` (our venv)
      - `{ "<task prefix>": {...}, ... }` (dev-decisions legacy)
    We map to head.id either way.
    """
    return _match_keys_to_heads(parsed, heads)


def parse_gliner2(
    parsed: dict, heads: list[Head], spec: dict[str, dict] | None = None
) -> dict[str, Answer]:
    """
    Parse the fastino-prescribed response shape:
    `{head_id: {"labels": [...], "probabilities": {level: p}}}`.

    The confidence basis is the full distribution — `confidence` is
    `max(probabilities)` for choice/score heads. Noul heads emit the
    docs-accurate shape `{"type": "noul", "noul": p_yes}` with **no**
    confidence field: the probability of "true" is the answer, and gating
    derives `max(p, 1 - p)` (see `gating.answer_confidence`). Score heads map
    the selected ordinal level back to `score_min + level_index`.

    Legacy entries (`{"label": ..., "confidence": ...}` from the pre-migration
    script or hand-written mocks) still parse; missing heads become declined.
    `spec` is the `classification_schema_spec(heads)` output — needed to map
    ordinal level strings back to numeric scores.
    """
    spec = spec or {}
    out: dict[str, Answer] = {}
    for head in heads:
        raw = parsed.get(head.id) if isinstance(parsed, dict) else None
        if not isinstance(raw, dict):
            out[head.id] = declined("missing")
            continue
        if "_error" in raw:
            out[head.id] = declined(f"error:{str(raw['_error'])[:120]}")
            continue
        probs_raw = raw.get("probabilities")
        if isinstance(probs_raw, dict):
            out[head.id] = _prescribed_answer(head, raw, spec.get(head.id) or {})
        else:
            out[head.id] = _legacy_answer(head, raw)
    return out


def _prescribed_answer(head: Head, raw: dict, head_spec: dict) -> Answer:
    probs = {
        str(k): float(v)
        for k, v in (raw.get("probabilities") or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    labels = [str(v) for v in raw.get("labels") or []]
    conf = max(probs.values()) if probs else None

    if head.kind == "noul":
        # docs-accurate: no confidence field; the probability of "true" gates
        # through derived certainty. A missing p_yes is a decline (escalates).
        yes = str(head.metadata.get("yes", "yes"))
        return {"type": "noul", "noul": probs.get(yes), "probabilities": probs}

    answer: Answer = {"probabilities": probs, "confidence": conf, "type": head.kind}
    if head.kind == "score":
        levels = list(head_spec.get("labels") or [])
        score_min = head_spec.get("score_min", head.score_min)
        score = None
        if labels and labels[0] in levels:
            score = float(score_min + levels.index(labels[0]))
        elif labels and labels[0].lstrip("-").replace(".", "", 1).isdigit():
            score = float(labels[0])
        answer["score"] = score
        if head.descriptions:
            answer["legend"] = {
                str(score_min + i): head.descriptions.get(lv, lv) for i, lv in enumerate(levels)
            }
        return answer

    # choice
    answer["label"] = labels[0] if labels else None
    if len(labels) > 1:
        answer["labels"] = labels
    return answer


def _legacy_answer(head: Head, raw: dict) -> Answer:
    label = raw.get("label")
    conf = raw.get("confidence")
    if label is None and conf is None:
        return declined("missing")
    return {"label": label, "confidence": conf, "type": "choice"}


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
