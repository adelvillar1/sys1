"""Routing tests: route() chain policy for cascade_classify.

route() is a pure policy function — deterministic chains from per-task
overrides, the default hosted-only chain, the input-size gate, availability,
and the task allow-list. All provider availability comes from env keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import sys1
from sys1.routing import route, route_decision


@pytest.fixture
def keys():
    """Hosted-provider keys so glide/drex/jev are 'available'."""
    import os

    os.environ["FASTINO_API_KEY"] = "fk-test"
    os.environ["DREX_API_KEY"] = "sk-test"
    os.environ["TYPESAFE_API_KEY"] = "ts-test"
    yield
    for name in ("FASTINO_API_KEY", "DREX_API_KEY", "TYPESAFE_API_KEY"):
        os.environ.pop(name, None)


def _task(name: str = "change"):
    return sys1.get_task(name)


# ── route_decision: chain + reason ───────────────────────────────────────────


class TestRouteDecision:
    def test_default_chain_short_input(self, cfg, keys):
        chain, reason = route_decision(_task(), "short diff", cfg)
        assert chain == ["glide", "drex", "jev"]
        assert reason == "default"

    def test_route_returns_chain_only(self, cfg, keys):
        assert route(_task(), "short diff", cfg) == ["glide", "drex", "jev"]

    def test_long_input_moves_glide_last(self, cfg, keys):
        cfg["providers"]["glide_max_chars"] = 100
        chain, reason = route_decision(_task(), "x" * 150, cfg)
        assert chain == ["drex", "jev", "glide"]
        assert reason == "long-input"

    def test_long_input_chars_override(self, cfg, keys):
        cfg["providers"]["route"]["long_input_chars"] = 10
        chain, reason = route_decision(_task(), "x" * 11, cfg)
        assert chain == ["drex", "jev", "glide"]
        assert reason == "long-input"

    def test_by_task_override(self, cfg, keys):
        cfg["providers"]["route"]["by_task"] = {"change": "jev,glide"}
        chain, reason = route_decision(_task("change"), "short", cfg)
        assert chain == ["jev", "glide"]
        assert reason == "task"

    def test_task_allowlist_hard_filter(self, cfg, keys):
        # _SAFETY's allow-list is ["decide", "jev", "local"] — glide/drex out
        chain, reason = route_decision(_task("safety"), "short", cfg)
        assert chain == ["jev"]
        assert reason == "default"

    def test_availability_filter(self, cfg, keys):
        import os

        os.environ.pop("DREX_API_KEY", None)
        chain, reason = route_decision(_task(), "short", cfg)
        assert chain == ["glide", "jev"]
        assert reason == "availability-filtered"

    def test_disabled_restores_legacy(self, cfg, keys):
        cfg["providers"]["route"]["enabled"] = False
        chain, reason = route_decision(_task(), "short", cfg)
        assert chain == ["decide", "jev"]
        assert reason == "default"

    def test_unknown_ids_filtered(self, cfg, keys):
        cfg["providers"]["route"]["default_chain"] = "glide,magic,jev"
        chain, _ = route_decision(_task(), "short", cfg)
        assert chain == ["glide", "jev"]


# ── cascade_classify integration ─────────────────────────────────────────────


def _good_answers(pid, text, heads, cfg, tele):
    """Every head clears its floor, so the first chain member wins."""
    out = {}
    for h in heads:
        if h.kind == "score":
            out[h.id] = {"type": "score", "score": h.score_min + 1, "confidence": 0.9}
        elif h.kind == "noul":
            out[h.id] = {"type": "noul", "noul": 0.9}
        else:
            out[h.id] = {"type": "choice", "label": h.labels[0], "confidence": 0.9}
    return out


class TestCascadeIntegration:
    def test_routes_by_default(self, cfg, keys):
        with patch("sys1.providers.call_provider", side_effect=_good_answers):
            r = sys1.cascade_classify(task="change", text="short diff", cfg=cfg, log=False)
        assert r.provider == "cascade[glide,drex,jev]"
        assert r.providers_used == ["glide"]  # glide cleared the floor, cascade stops

    def test_explicit_chain_bypasses_route(self, cfg, keys):
        with patch("sys1.providers.call_provider", side_effect=_good_answers):
            r = sys1.cascade_classify(["drex"], task="change", text="short diff", cfg=cfg, log=False)
        assert r.provider == "cascade[drex]"

    def test_route_disabled_legacy_chain(self, cfg, keys):
        cfg["providers"]["route"]["enabled"] = False
        with patch("sys1.providers.call_provider", side_effect=_good_answers):
            r = sys1.cascade_classify(task="change", text="short diff", cfg=cfg, log=False)
        assert r.provider == "cascade[decide,jev]"

    def test_route_decision_logged(self, cfg, keys):
        with patch("sys1.providers.call_provider", side_effect=_good_answers):
            sys1.cascade_classify(task="change", text="short diff", cfg=cfg, log=True)
        # log_record partitions by date: log_dir/YYYY/MM/DD/events.jsonl
        rows = [
            json.loads(line)
            for p in Path(cfg["classify"]["log_dir"]).rglob("events.jsonl")
            for line in p.read_text().splitlines()
        ]
        assert any(
            row.get("route_chain") == ["glide", "drex", "jev"] and row.get("route_reason") == "default"
            for row in rows
        )
