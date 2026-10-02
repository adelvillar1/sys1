"""Small shared helpers for the probe scripts (hyphen-free module name so
probe scripts can import it when run from scripts/)."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


def sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunked(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def first_answers(result, chain: list[str]) -> tuple[str, dict]:
    for pid in chain:
        got = (result.answers.get(pid) or {}) if hasattr(result, "answers") else (result.get(pid) or {})
        if got:
            return pid, got
    return (chain[0] if chain else ""), {}


def span_source(src: str, start: int, end: int) -> str:
    return "\n".join(src.splitlines(keepends=True)[start - 1:end])


def load_module_by_path(stem: str, path: Path):
    spec = importlib.util.spec_from_file_location(stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
