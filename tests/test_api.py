"""End-to-end tests for `classify()` and `cascade_classify()`.

Providers are mocked at the `sys1.providers.REGISTRY` layer so we don't have
to patch every transport (HTTP / subprocess) twice — the transport-specific
tests live in test_providers.py.
"""

from __future__ import annotations

import json

import pytest

import sys1


def _mock_decide(answers):
    """Build a classify function that returns the given answers dict."""

    def _fn(text, heads, cfg, tele):
        return {h.id: dict(answers.get(h.id, {"_raw": "", "confidence": 0.0})) for h in heads}

    return _fn


@pytest.fixture
def fake_decide(cfg):
    """Register a fake provider named `fake_decide` for the duration of a test."""
    from sys1.types import Provider

    provider = Provider(
        id="fake_decide",
        name="fake decide",
        classify=_mock_decide(
            {
                "diff_type": {"label": "feat", "confidence": 0.9},
                "risk_tier": {"label": "low", "confidence": 0.85},
            }
        ),
        is_available=lambda c: True,
    )
    sys1.register(provider)
    yield provider
    sys1.REGISTRY.pop("fake_decide", None)


@pytest.fixture
def fake_low_conf(cfg):
    """A provider whose answers fall below the confidence floor."""
    from sys1.types import Provider

    provider = Provider(
        id="fake_low_conf",
        name="fake low conf",
        classify=_mock_decide(
            {
                "diff_type": {"label": "feat", "confidence": 0.4},
                "risk_tier": {"label": "low", "confidence": 0.3},
            }
        ),
        is_available=lambda c: True,
    )
    sys1.register(provider)
    yield provider
    sys1.REGISTRY.pop("fake_low_conf", None)


class TestClassify:
    def test_single_provider_passes(self, cfg, fake_decide):
        result = sys1.classify("fake_decide", "change", "diff text", cfg=cfg, log=False)
        assert result.verdict == "pass"
        assert result.escalated is False
        assert result.answers["fake_decide"]["diff_type"]["label"] == "feat"

    def test_low_conf_escalates(self, cfg, fake_low_conf):
        result = sys1.classify("fake_low_conf", "change", "diff", cfg=cfg, log=False)
        assert result.verdict == "escalate"
        assert result.escalated is True

    def test_no_providers_errors(self, cfg):
        # ask for a provider id that isn't available in this cfg (no API keys)
        result = sys1.classify("decide", "change", "diff", cfg=cfg, log=False)
        assert result.verdict == "error"
        assert result.providers_used == []

    def test_unknown_provider_errors(self, cfg):
        # provider not in registry at all
        result = sys1.classify("nonexistent_provider", "change", "diff", cfg=cfg, log=False)
        assert result.verdict == "error"

    def test_task_name_resolves(self, cfg, fake_decide):
        result = sys1.classify("fake_decide", "change", "diff", cfg=cfg, log=False)
        assert result.task == "change"

    def test_task_instance_works(self, cfg, fake_decide):
        task = sys1.Task(id="custom", heads=[sys1.make_choice("?", ["a", "b"], id="h1")])
        result = sys1.classify("fake_decide", task, "diff", cfg=cfg, log=False)
        assert result.task == "custom"
        # fake_decide returns answers for the heads it was asked about
        assert "h1" in result.answers["fake_decide"]

    def test_log_writes_jsonl(self, cfg, fake_decide, tmp_path):
        sys1.classify("fake_decide", "change", "diff", cfg=cfg, log=True)
        # log dir is tmp_path/logs/YYYY/MM/DD/events.jsonl
        logs = list((tmp_path / "logs").rglob("events.jsonl"))
        assert logs, "expected exactly one events.jsonl"
        with open(logs[0]) as f:
            line = f.readline()
        record = json.loads(line)
        assert record["op"] == "classify"
        assert record["task"] == "change"
        assert "fake_decide" in record["providers_used"]
        assert "confidence_histogram" in record
        assert "input_sha256" in record


class TestCascade:
    def test_high_conf_first_provider_stops_chain(self, cfg, fake_decide, fake_low_conf):
        # First provider is high-conf, so cascade stops without consulting second
        result = sys1.cascade_classify(
            provider_chain=["fake_decide", "fake_low_conf"],
            task="change",
            text="diff",
            cfg=cfg,
            log=False,
        )
        assert result.verdict == "pass"
        # only the first provider's answers should be present
        assert list(result.answers) == ["fake_decide"]

    def test_low_conf_first_provider_escalates_to_second(self, cfg):
        from sys1.types import Provider

        low = Provider(
            id="low",
            name="low",
            classify=_mock_decide(
                {
                    "diff_type": {"label": "feat", "confidence": 0.4},
                    "risk_tier": {"label": "low", "confidence": 0.4},
                }
            ),
            is_available=lambda c: True,
        )
        high = Provider(
            id="high",
            name="high",
            classify=_mock_decide(
                {
                    "diff_type": {"label": "feat", "confidence": 0.92},
                    "risk_tier": {"label": "low", "confidence": 0.9},
                }
            ),
            is_available=lambda c: True,
        )
        sys1.register(low)
        sys1.register(high)
        try:
            result = sys1.cascade_classify(["low", "high"], "change", "diff", cfg=cfg, log=False)
            # low fell short, cascade escalated to high which cleared
            assert "low" in result.answers
            assert "high" in result.answers
            assert result.verdict == "pass"
        finally:
            sys1.REGISTRY.pop("low", None)
            sys1.REGISTRY.pop("high", None)

    def test_chain_all_failing_escalates(self, cfg, fake_low_conf):
        # only low-conf in the chain, so final verdict is escalate
        cfg["classify"]["confidence_floor"] = 0.7
        result = sys1.cascade_classify(["fake_low_conf"], "change", "diff", cfg=cfg, log=False)
        assert result.verdict == "escalate"


class TestHealth:
    def test_health_report_shape(self, cfg):
        report = sys1.health_report(cfg)
        for pid in ("decide", "jev", "local", "modernbert"):
            assert pid in report
            assert "available" in report[pid]
            assert "reason" in report[pid]
