"""Telemetry tests: log write, redaction, histogram, agreement, feedback join."""

from __future__ import annotations

import json
from pathlib import Path

import sys1
from sys1.telemetry import (
    calibration_curve,
    confidence_histogram,
    log_record,
    provider_agreement,
    record_feedback,
    sanitize_for_log,
)


def test_log_record_writes_to_year_dir(cfg, tmp_path: Path):
    p = log_record({"op": "test", "hello": "world"}, cfg=cfg)
    assert p is not None
    assert p.exists()
    assert p.name == "events.jsonl"
    # path is tmp_path/logs/YYYY/MM/DD/events.jsonl
    assert str(tmp_path / "logs") in str(p)
    record = json.loads(p.read_text())
    assert record["op"] == "test"
    assert "ts" in record


def test_log_record_redacts_default_fields(cfg, tmp_path: Path):
    p = log_record({"op": "test", "content": "secret-payload"}, cfg=cfg)
    record = json.loads(p.read_text())
    assert record["content"] == "[redacted]"


def test_log_record_never_raises_on_bad_path(cfg):
    bad = {"classify": {"log_dir": "/root/forbidden"}}
    # Should not raise; returns None
    assert log_record({"op": "x"}, cfg=bad) is None


def test_sanitize_for_log_walks_nested(cfg):
    answers = {
        "decide": {
            "diff_type": {"label": "feat", "confidence": 0.9, "content": "raw-text"},
            "risk_tier": {"label": "low", "confidence": 0.8},
        }
    }
    safe = sanitize_for_log(answers)
    assert safe["decide"]["diff_type"]["content"] == "[redacted]"
    assert safe["decide"]["diff_type"]["label"] == "feat"
    # original not mutated
    assert answers["decide"]["diff_type"]["content"] == "raw-text"


def test_confidence_histogram():
    answers = {
        "decide": {
            "h1": {"label": "a", "confidence": 0.95},
            "h2": {"label": "b", "confidence": 0.45},
        },
        "jev": {
            "h1": {"label": "a", "confidence": 0.70},
        },
    }
    hist = confidence_histogram(answers, bins=10)
    assert hist["decide"][9] == 1  # 0.95 in bin 9
    assert hist["decide"][4] == 1  # 0.45 in bin 4
    assert hist["jev"][7] == 1  # 0.70 in bin 7 (edge inclusive)


def test_provider_agreement_full_match():
    answers = {
        "a": {"diff_type": {"label": "feat", "confidence": 0.9}},
        "b": {"diff_type": {"label": "feat", "confidence": 0.8}},
    }
    out = provider_agreement(answers)
    assert out["diff_type"]["agree"] is True
    assert out["diff_type"]["majority_label"] == "feat"


def test_provider_agreement_disagreement():
    answers = {
        "a": {"diff_type": {"label": "feat", "confidence": 0.9}},
        "b": {"diff_type": {"label": "fix", "confidence": 0.8}},
        "c": {"diff_type": {"label": "fix", "confidence": 0.7}},
    }
    out = provider_agreement(answers)
    assert out["diff_type"]["agree"] is False
    # majority is "fix" (2/3)
    assert out["diff_type"]["majority_label"] == "fix"
    assert out["diff_type"]["distribution"] == {"feat": 1, "fix": 2}


def test_provider_agreement_no_labels():
    answers = {
        "a": {"diff_type": {"label": None, "confidence": 0.0}},
        "b": {"diff_type": {"label": None, "confidence": 0.0}},
    }
    out = provider_agreement(answers)
    # both declined, so no agreement entry (or empty distribution)
    assert "diff_type" not in out


def test_record_feedback(cfg, tmp_path: Path):
    p = record_feedback(
        input_sha256="abc123",
        task="change",
        head_id="diff_type",
        predicted_label="feat",
        predicted_confidence=0.85,
        provider="decide",
        actual_label="fix",
        escalated=False,
        cfg=cfg,
    )
    assert p is not None
    record = json.loads(p.read_text())
    assert record["op"] == "feedback"
    assert record["predicted_confidence"] == 0.85
    assert record["actual"] == "fix"


def test_calibration_curve():
    records = [
        # bin 0 (conf 0.0-0.1): 1 wrong
        {"predicted_confidence": 0.05, "predicted": "a", "actual": "b"},
        # bin 8 (conf 0.8-0.9): 3 records, 2 right
        {"predicted_confidence": 0.85, "predicted": "a", "actual": "a"},
        {"predicted_confidence": 0.87, "predicted": "a", "actual": "a"},
        {"predicted_confidence": 0.83, "predicted": "a", "actual": "b"},
        # bin 9 (conf 0.9-1.0): 2 records, both right
        {"predicted_confidence": 0.95, "predicted": "a", "actual": "a"},
        {"predicted_confidence": 0.98, "predicted": "a", "actual": "a"},
    ]
    bins = calibration_curve(records)
    by_lower = {b["bin_lower"]: b for b in bins}
    assert by_lower[0.0]["count"] == 1
    assert by_lower[0.0]["accuracy"] == 0.0
    assert by_lower[0.8]["count"] == 3
    assert abs(by_lower[0.8]["accuracy"] - 2 / 3) < 1e-3
    assert by_lower[0.9]["count"] == 2
    assert by_lower[0.9]["accuracy"] == 1.0


class TestLogDirEnvOverride:
    """A deployed service must write its calibration set where the deployment
    mounted it. `SYS1_LOG_DIR` is the name the Dockerfile, the service header
    and FUNCTIONAL-SPECIFICATIONS.md all advertise; the generic
    `SYS1_<SECTION>__<KEY>` loop cannot reach `classify.log_dir` under that
    name, so config.load_config binds it explicitly."""

    def _feedback(self, cfg):
        return record_feedback(
            input_sha256="ab" * 8,
            task="scan_line_correctness",
            head_id="h0_desc",
            predicted_label="correct",
            predicted_confidence=0.9,
            provider="jev",
            actual_label="correct",
            escalated=False,
            cfg=cfg,
        )

    def test_alias_sets_classify_log_dir(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("SYS1_CLASSIFY__LOG_DIR", raising=False)
        monkeypatch.setenv("SYS1_LOG_DIR", str(tmp_path / "mounted"))
        assert sys1.load_config()["classify"]["log_dir"] == str(tmp_path / "mounted")

    def test_section_qualified_var_wins_over_alias(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("SYS1_LOG_DIR", str(tmp_path / "alias"))
        monkeypatch.setenv("SYS1_CLASSIFY__LOG_DIR", str(tmp_path / "qualified"))
        assert sys1.load_config()["classify"]["log_dir"] == str(tmp_path / "qualified")

    def test_empty_qualified_value_does_not_suppress_alias(self, monkeypatch, tmp_path: Path):
        # `-e SYS1_CLASSIFY__LOG_DIR=` (empty) is a deployment mistake, not a
        # choice of log dir — the alias must still bind, not collapse to "".
        monkeypatch.setenv("SYS1_LOG_DIR", str(tmp_path / "alias"))
        monkeypatch.setenv("SYS1_CLASSIFY__LOG_DIR", "")
        assert sys1.load_config()["classify"]["log_dir"] == str(tmp_path / "alias")

    def test_feedback_row_lands_under_the_env_dir(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("SYS1_CLASSIFY__LOG_DIR", raising=False)
        root = tmp_path / "volume"
        monkeypatch.setenv("SYS1_LOG_DIR", str(root))
        path = self._feedback(sys1.load_config())
        assert path is not None
        assert str(root) in str(path)
        assert path.exists()
        assert json.loads(path.read_text())["op"] == "feedback"
