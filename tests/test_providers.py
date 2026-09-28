"""Provider tests: HTTP retry/backoff, subprocess wiring, and availability checks.

All external calls are mocked: urllib for hosted providers, subprocess.run for
the local venv path.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys1
from sys1.providers import (
    http_post_json,
    is_available,
    resolve_providers,
)

# ── resolve_providers ─────────────────────────────────────────────────────────


class TestResolveProviders:
    def test_single(self):
        assert resolve_providers("decide") == ["decide"]

    def test_both_alias(self):
        assert resolve_providers("both") == ["decide", "jev"]

    def test_all_alias(self):
        assert set(resolve_providers("all")) >= {"decide", "jev", "local", "modernbert"}

    def test_comma_list(self):
        assert resolve_providers("decide,jev") == ["decide", "jev"]

    def test_unknown_dropped(self):
        assert resolve_providers("decide,magic") == ["decide"]

    def test_list_passthrough(self):
        assert resolve_providers(["decide", "jev"]) == ["decide", "jev"]


# ── http_post_json: success path ─────────────────────────────────────────────


def _make_resp(status: int, body: bytes):
    m = MagicMock()
    m.read.return_value = body
    m.status = status
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    return m


class TestHttpPostJson:
    def test_success(self):
        payload = {"ok": True}
        with patch("urllib.request.urlopen", return_value=_make_resp(200, json.dumps(payload).encode())):
            tele: dict = {}
            out = http_post_json(
                "https://api.test/v1",
                {"x": 1},
                api_key="k",
                timeout=5,
                max_retries=0,
                backoff=1,
                telemetry=tele,
            )
        assert out == payload
        assert tele["http_status"] == 200
        assert tele["structured_ok"] is True

    def test_warming_retry(self):
        """First response is a warming error, second succeeds."""
        warming = {"error": {"message": "model warming up"}}
        ok = {"answer": 42}
        responses = [_make_resp(200, json.dumps(warming).encode()), _make_resp(200, json.dumps(ok).encode())]
        with patch("urllib.request.urlopen", side_effect=responses), patch("time.sleep"):
            tele: dict = {}
            out = http_post_json(
                "https://api.test/v1",
                {},
                api_key="k",
                timeout=5,
                max_retries=1,
                backoff=0.01,
                telemetry=tele,
            )
        assert out == ok
        assert tele["retries"] == 1

    def test_4xx_raises_immediately(self):
        err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"invalid input"))
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError) as exc:
                http_post_json(
                    "https://api.test/v1",
                    {},
                    api_key="k",
                    timeout=5,
                    max_retries=2,
                    backoff=0.01,
                    telemetry={},
                )
        assert "400" in str(exc.value)

    def test_5xx_retries_then_raises(self):
        err = urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b"unavailable"))
        with patch("urllib.request.urlopen", side_effect=err), patch("time.sleep"):
            with pytest.raises(RuntimeError) as exc:
                http_post_json(
                    "https://api.test/v1",
                    {},
                    api_key="k",
                    timeout=5,
                    max_retries=1,
                    backoff=0.01,
                    telemetry={},
                )
        assert "503" in str(exc.value)

    def test_network_error_retries(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("nope")), patch("time.sleep"):
            with pytest.raises(RuntimeError):
                http_post_json(
                    "https://api.test/v1",
                    {},
                    api_key="k",
                    timeout=5,
                    max_retries=1,
                    backoff=0.01,
                    telemetry={},
                )

    def test_string_body_passthrough(self):
        with patch("urllib.request.urlopen", return_value=_make_resp(200, b'{"ok": true}')) as m:
            http_post_json(
                "https://api.test/v1",
                '{"raw": 1}',
                api_key=None,
                timeout=5,
                max_retries=0,
                backoff=0,
                telemetry={},
            )
            called = m.call_args[0][0]
            assert json.loads(called.data) == {"raw": 1}


# ── availability checks ──────────────────────────────────────────────────────


class TestAvailability:
    def test_decide_needs_key(self, cfg):
        import os

        os.environ.pop("FASTINO_API_KEY", None)
        assert is_available("decide", cfg) is False
        os.environ["FASTINO_API_KEY"] = "sk-test"
        try:
            assert is_available("decide", cfg) is True
        finally:
            os.environ.pop("FASTINO_API_KEY", None)

    def test_jev_needs_key(self, cfg):
        import os

        os.environ.pop("TYPESAFE_API_KEY", None)
        assert is_available("jev", cfg) is False
        os.environ["TYPESAFE_API_KEY"] = "sk-test"
        try:
            assert is_available("jev", cfg) is True
        finally:
            os.environ.pop("TYPESAFE_API_KEY", None)

    def test_local_needs_venv(self, cfg):
        # cfg fixture points local_venv at a non-existent path
        assert is_available("local", cfg) is False

    def test_local_with_existing_venv(self, cfg, tmp_path: Path):
        venv_py = tmp_path / "fake-venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.touch()
        cfg["providers"]["local_venv"] = str(tmp_path / "fake-venv")
        assert is_available("local", cfg) is True


# ── decide + jev end-to-end with mocked HTTP ─────────────────────────────────


class TestDecideProvider:
    def test_call_decide(self, cfg):
        import os

        os.environ["FASTINO_API_KEY"] = "sk-test"
        try:
            body = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "diff_type": {"label": "feat", "confidence": 0.9},
                                    "risk_tier": {"label": "low", "confidence": 0.8},
                                }
                            )
                        }
                    }
                ]
            }
            with patch("urllib.request.urlopen", return_value=_make_resp(200, json.dumps(body).encode())):
                tele: dict = {}
                result = sys1.call_provider("decide", "diff text", sys1.heads_for("change", "decide"), cfg, tele)
            assert result["diff_type"]["label"] == "feat"
            assert result["risk_tier"]["label"] == "low"
            assert tele["http_status"] == 200
            assert tele["structured_ok"] is True
        finally:
            os.environ.pop("FASTINO_API_KEY", None)


class TestJevProvider:
    def test_call_jev(self, cfg):
        import os

        os.environ["TYPESAFE_API_KEY"] = "sk-test"
        try:
            body = {
                "answers": {
                    "diff_type": {"type": "choice", "choice": "fix", "confidence": 0.77},
                    "risk_tier": {"type": "choice", "choice": "medium", "confidence": 0.71},
                    "breaking_change": {"type": "noul", "noul": True, "confidence": 0.83},
                    "personal_data": {"type": "noul", "noul": False, "confidence": 0.85},
                }
            }
            with patch("urllib.request.urlopen", return_value=_make_resp(200, json.dumps(body).encode())):
                result = sys1.call_provider("jev", "diff text", sys1.heads_for("change", "jev"), cfg, {})
            assert result["diff_type"]["label"] == "fix"
            assert result["breaking_change"]["noul"] is True
        finally:
            os.environ.pop("TYPESAFE_API_KEY", None)


class TestJevHeadWiring:
    """head.to_jev_head() matches the verified /v1/systemone request shapes."""

    def test_score_criteria_is_ordered_array(self):
        head = sys1.make_score(
            "How frustrated does the author appear?",
            score_min=1,
            score_max=3,
            legend=["Calm", "Frustrated", "Very angry"],
            id="frustration",
        )
        wire = head.to_jev_head()
        assert wire["type"] == "score"
        # score criteria is an ordered array of level descriptions — not a
        # label→description map — and there is no min/max on the wire
        assert wire["criteria"] == ["Calm", "Frustrated", "Very angry"]
        assert "min" not in wire
        assert "max" not in wire

    def test_score_criteria_without_legend(self):
        head = sys1.make_score("Rate severity.", id="severity")
        wire = head.to_jev_head()
        assert wire["criteria"] == ["1", "2", "3", "4", "5"]

    def test_score_labels_sorted_by_level(self):
        head = sys1.Head(
            id="s",
            kind="score",
            task="Rate it.",
            labels=["3", "1", "2"],
            descriptions={"1": "low", "2": "mid", "3": "high"},
        )
        assert head.to_jev_head()["criteria"] == ["low", "mid", "high"]

    def test_choice_criteria_is_a_label_map(self):
        head = sys1.make_choice(
            "Which team should handle this?",
            ["billing", "technical"],
            descriptions={"billing": "Payments and refunds", "technical": "Bugs"},
            id="department",
        )
        wire = head.to_jev_head()
        assert wire["criteria"] == {"billing": "Payments and refunds", "technical": "Bugs"}
        assert wire["instructions"] == "Which team should handle this?"


# ── local provider: subprocess ───────────────────────────────────────────────


class TestLocalProvider:
    def test_call_local(self, cfg, tmp_path: Path):
        venv_py = tmp_path / "venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.touch()
        cfg["providers"]["local_venv"] = str(tmp_path / "venv")

        # Mock subprocess.run to return the fixture's stdout
        stdout = json.dumps(
            {
                "diff_type": {"label": "feat", "confidence": 0.89},
                "risk_tier": {"label": "low", "confidence": 0.78},
            }
        )
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = stdout
        proc.stderr = ""
        with patch("subprocess.run", return_value=proc):
            result = sys1.call_provider("local", "diff text", sys1.heads_for("change", "local"), cfg, {})
        assert result["diff_type"]["label"] == "feat"
        assert result["risk_tier"]["label"] == "low"

    def test_subprocess_failure_raises(self, cfg, tmp_path: Path):
        venv_py = tmp_path / "venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.touch()
        cfg["providers"]["local_venv"] = str(tmp_path / "venv")
        proc = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("subprocess.run", return_value=proc), pytest.raises(RuntimeError):
            sys1.call_provider("local", "x", sys1.heads_for("change", "local"), cfg, {})

    def test_subprocess_no_venv_raises(self, cfg):
        # cfg fixture sets local_venv to a missing path
        with pytest.raises(RuntimeError):
            sys1.call_provider("local", "x", sys1.heads_for("change", "local"), cfg, {})

    def test_structured_ok_false_on_bad_json(self, cfg, tmp_path: Path):
        """Regression: structured_ok must be False when subprocess returns non-JSON stdout."""
        venv_py = tmp_path / "venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.touch()
        cfg["providers"]["local_venv"] = str(tmp_path / "venv")
        proc = MagicMock(returncode=0, stdout="NOT JSON", stderr="")
        tele: dict = {}
        with patch("subprocess.run", return_value=proc):
            result = sys1.call_provider("local", "x", sys1.heads_for("change", "local"), cfg, tele)
        assert tele["structured_ok"] is False
        assert tele["error_kind"] == "json"
        # Parser should have declined all heads (confidence=0.0)
        heads = sys1.heads_for("change", "local")
        assert all(result[h.id]["confidence"] == 0.0 for h in heads)


class TestModernBertProvider:
    def test_structured_ok_false_on_bad_json(self, cfg, tmp_path: Path):
        """Regression: structured_ok must be False when ModernBERT subprocess returns non-JSON."""
        venv_py = tmp_path / "venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.touch()
        cfg["providers"]["local_venv"] = str(tmp_path / "venv")
        proc = MagicMock(returncode=0, stdout="NOT JSON", stderr="")
        tele: dict = {}
        with patch("subprocess.run", return_value=proc):
            result = sys1.call_provider("modernbert", "x", sys1.heads_for("change", "modernbert"), cfg, tele)
        assert tele["structured_ok"] is False
        assert tele["error_kind"] == "json"
        heads = sys1.heads_for("change", "modernbert")
        assert all(result[h.id]["confidence"] == 0.0 for h in heads)
