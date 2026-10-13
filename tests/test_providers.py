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
        with patch("urllib.request.urlopen", side_effect=err), pytest.raises(RuntimeError) as exc:
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
        with (
            patch("urllib.request.urlopen", side_effect=err),
            patch("time.sleep"),
            pytest.raises(RuntimeError) as exc,
        ):
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

    def test_429_retries_then_succeeds(self):
        """Jev documents 429 rate-limiting (1,200 req/min) — retryable."""
        ok = {"answers": {}}
        err = urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(b"rate limited"))
        responses = [err, _make_resp(200, json.dumps(ok).encode())]
        tele: dict = {}
        with patch("urllib.request.urlopen", side_effect=responses), patch("time.sleep"):
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
        assert tele["structured_ok"] is True

    def test_429_exhausted_raises(self):
        err = urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(b"rate limited"))
        tele: dict = {}
        with patch("urllib.request.urlopen", side_effect=err), patch("time.sleep"), pytest.raises(RuntimeError) as exc:
            http_post_json(
                "https://api.test/v1",
                {},
                api_key="k",
                timeout=5,
                max_retries=1,
                backoff=0.01,
                telemetry=tele,
            )
        assert "429" in str(exc.value)
        assert tele["http_status"] == 429
        assert tele["structured_ok"] is False

    def test_network_error_retries(self):
        with (
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("nope")),
            patch("time.sleep"),
            pytest.raises(RuntimeError),
        ):
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


# ── Drex provider (hosted Nace, /v1/systemone, plain-string state) ────────────


class TestDrexProvider:
    def _heads(self):
        return sys1.heads_for("change", "drex")

    def test_drex_needs_key(self, cfg):
        import os

        os.environ.pop("DREX_API_KEY", None)
        assert is_available("drex", cfg) is False
        os.environ["DREX_API_KEY"] = "sk-test"
        try:
            assert is_available("drex", cfg) is True
        finally:
            os.environ.pop("DREX_API_KEY", None)

    def test_call_drex_wire_and_usage(self, cfg):
        import os

        os.environ["DREX_API_KEY"] = "sk-test"
        cfg["providers"]["drex_model"] = "drex-v1.5"
        try:
            resp_body = {
                "model": "drex-v1.5",
                "answers": {
                    "diff_type": {"type": "choice", "choice": "fix", "probabilities": {"fix": 0.8}, "confidence": 0.8},
                    "risk_tier": {"type": "choice", "choice": "medium", "confidence": 0.71},
                    "breaking_change": {"type": "noul", "noul": 0.02},
                },
                "usage": {"input_tokens": 106, "output_tokens": 212},
                "evaluation_time_ms": 142.6,
                "request_id": "req_" + "a" * 32,
            }
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(200, json.dumps(resp_body).encode())

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                tele: dict = {}
                result = sys1.call_provider("drex", "diff text", self._heads(), cfg, tele)

            # request shape: plain-string state, config model, drex questions
            assert captured["url"] == cfg["providers"]["drex_api_url"]
            assert captured["body"]["model"] == "drex-v1.5"
            assert captured["body"]["state"] == "diff text"
            assert "id" not in next(iter(captured["body"]["questions"].values()))
            # answers normalize to the shared wire
            assert result["diff_type"]["label"] == "fix"
            assert result["breaking_change"]["noul"] == 0.02
            # contract telemetry keys populated from Drex usage
            assert tele["http_status"] == 200
            assert tele["structured_ok"] is True
            assert tele["token_prompt"] == 106
            assert tele["token_completion"] == 212
            assert tele["token_total"] == 318
            assert tele["evaluation_time_ms"] == 142.6
            assert tele["request_id"].startswith("req_")
        finally:
            os.environ.pop("DREX_API_KEY", None)

    def test_state_truncated_by_drex_max_chars(self, cfg):
        import os

        os.environ["DREX_API_KEY"] = "sk-test"
        cfg["providers"]["drex_max_chars"] = 4
        try:
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(
                    200,
                    json.dumps(
                        {
                            "answers": {
                                "diff_type": {"type": "choice", "choice": "fix", "confidence": 0.8},
                                "risk_tier": {"type": "choice", "choice": "low", "confidence": 0.8},
                                "breaking_change": {"type": "noul", "noul": 0.5},
                            }
                        }
                    ).encode(),
                )

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                sys1.call_provider("drex", "abcdefghij", self._heads(), cfg, {})
            assert captured["body"]["state"] == "abcd"  # not scan.max_diff_chars
        finally:
            os.environ.pop("DREX_API_KEY", None)

    def test_noul_criteria_uses_true_false_keys(self, cfg):
        import os

        os.environ["DREX_API_KEY"] = "sk-test"
        try:
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(
                    200,
                    json.dumps(
                        {
                            "answers": {
                                "diff_type": {"type": "choice", "choice": "fix", "confidence": 0.8},
                                "risk_tier": {"type": "choice", "choice": "low", "confidence": 0.8},
                                "breaking_change": {"type": "noul", "noul": 0.5},
                            }
                        }
                    ).encode(),
                )

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                sys1.call_provider("drex", "diff", self._heads(), cfg, {})
            noul_q = next(
                q for q in captured["body"]["questions"].values() if q["type"] == "noul"
            )
            assert set(noul_q["criteria"].keys()) == {"true", "false"}
        finally:
            os.environ.pop("DREX_API_KEY", None)

    def test_config_view_is_url_kind(self, cfg):
        from sys1.providers import provider_config_view

        view = provider_config_view("drex", cfg)
        assert view["kind"] == "url"
        assert view["api_key_env"] == "DREX_API_KEY"
        assert view["requires_key"] is True
        assert view["model"] == cfg["providers"]["drex_model"]

    def test_floor_registered(self):
        from sys1.gating import _DEFAULT_FLOORS

        assert _DEFAULT_FLOORS["drex"] == 0.7

    def test_in_core_roster(self):
        assert "drex" in resolve_providers("core")
        assert "drex" in resolve_providers("all")


class TestGlideProvider:
    def _heads(self):
        return sys1.heads_for("change", "glide")

    def test_glide_needs_key(self, cfg):
        import os

        os.environ.pop("FASTINO_API_KEY", None)
        assert is_available("glide", cfg) is False
        os.environ["FASTINO_API_KEY"] = "fk-test"
        try:
            assert is_available("glide", cfg) is True
        finally:
            os.environ.pop("FASTINO_API_KEY", None)

    def test_call_glide_wire_and_usage(self, cfg):
        import os

        os.environ["FASTINO_API_KEY"] = "fk-test"
        cfg["providers"]["glide_model"] = "fastino/glide"
        try:
            resp_body = {
                "model": "glide",
                "answers": {
                    "diff_type": {"type": "choice", "choice": "fix", "probabilities": {"fix": 0.8}, "confidence": 0.8},
                    "risk_tier": {"type": "choice", "choice": "medium", "confidence": 0.71},
                    "breaking_change": {"type": "noul", "noul": 0.02, "confidence": 0.96},
                },
                "usage": {"input_tokens": 152, "output_tokens": 1},
                "token_usage": 153,
            }
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(200, json.dumps(resp_body).encode())

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                tele: dict = {}
                result = sys1.call_provider("glide", "diff text", self._heads(), cfg, tele)

            # request shape: plain-string state, config model, Drex-shape
            # questions (GLiDE is strictly validated: no `id`, true/false noul)
            assert captured["url"] == cfg["providers"]["glide_api_url"]
            assert captured["body"]["model"] == "fastino/glide"
            assert captured["body"]["state"] == "diff text"
            assert "id" not in next(iter(captured["body"]["questions"].values()))
            noul_q = next(q for q in captured["body"]["questions"].values() if q["type"] == "noul")
            assert set(noul_q["criteria"].keys()) == {"true", "false"}
            # answers normalize to the shared wire; noul confidence stripped
            assert result["diff_type"]["label"] == "fix"
            assert result["breaking_change"]["noul"] == 0.02
            assert result["breaking_change"].get("confidence") is None
            # contract telemetry keys populated from GLiDE usage
            assert tele["http_status"] == 200
            assert tele["structured_ok"] is True
            assert tele["token_prompt"] == 152
            assert tele["token_completion"] == 1
            assert tele["token_total"] == 153
            assert "evaluation_time_ms" not in tele  # Drex-only extra
        finally:
            os.environ.pop("FASTINO_API_KEY", None)

    def test_state_truncated_by_glide_max_chars(self, cfg):
        import os

        os.environ["FASTINO_API_KEY"] = "fk-test"
        cfg["providers"]["glide_max_chars"] = 4
        try:
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(
                    200,
                    json.dumps(
                        {
                            "answers": {
                                "diff_type": {"type": "choice", "choice": "fix", "confidence": 0.8},
                                "risk_tier": {"type": "choice", "choice": "low", "confidence": 0.8},
                                "breaking_change": {"type": "noul", "noul": 0.5},
                            }
                        }
                    ).encode(),
                )

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                sys1.call_provider("glide", "abcdefghij", self._heads(), cfg, {})
            assert captured["body"]["state"] == "abcd"  # not scan.max_diff_chars
        finally:
            os.environ.pop("FASTINO_API_KEY", None)

    def test_heads_for_inherits_jev_set(self):
        assert [h.id for h in self._heads()] == [h.id for h in sys1.heads_for("change", "jev")]

    def test_422_error_envelope_raises(self, cfg):
        import os

        os.environ["FASTINO_API_KEY"] = "fk-test"
        try:
            # GLiDE is strictly validated: extra `id` fields / yes-no noul
            # criteria come back as HTTP 422 with an OpenAI-style envelope.
            err_body = json.dumps(
                {
                    "error": {
                        "message": "'questions.action.choice.id' is invalid: Extra inputs are not permitted",
                        "type": "invalid_request_error",
                    }
                }
            ).encode()
            err = urllib.error.HTTPError("u", 422, "Unprocessable Entity", {}, io.BytesIO(err_body))
            with patch("urllib.request.urlopen", side_effect=err), pytest.raises(RuntimeError) as exc:
                sys1.call_provider("glide", "diff", self._heads(), cfg, {})
            assert "422" in str(exc.value)
        finally:
            os.environ.pop("FASTINO_API_KEY", None)

    def test_config_view_is_url_kind(self, cfg):
        from sys1.providers import provider_config_view

        view = provider_config_view("glide", cfg)
        assert view["kind"] == "url"
        assert view["api_key_env"] == "FASTINO_API_KEY"
        assert view["requires_key"] is True
        assert view["model"] == cfg["providers"]["glide_model"]

    def test_floor_registered(self):
        from sys1.gating import _DEFAULT_FLOORS

        assert _DEFAULT_FLOORS["glide"] == 0.7

    def test_in_core_roster(self):
        assert "glide" in resolve_providers("core")
        assert "glide" in resolve_providers("all")


class TestScaledownProvider:
    """ScaleDown Decisions (/v1/scaledown, classify-1) — the Jev mirror.

    Probed live 2026-10-13: route-task verdicts agree with d1/GLiDE;
    prose-rating nouls compress toward the middle. Wire deltas vs GLiDE:
    noul questions take `instructions` (not true/false criteria), score
    criteria is an ORDERED ARRAY, usage carries cost_usd.
    """

    def _heads(self):
        return sys1.heads_for("change", "scaledown")

    def test_scaledown_needs_key(self, cfg):
        import os

        os.environ.pop("SCALEDOWN_API_KEY", None)
        assert is_available("scaledown", cfg) is False
        os.environ["SCALEDOWN_API_KEY"] = "sd-test"
        try:
            assert is_available("scaledown", cfg) is True
        finally:
            os.environ.pop("SCALEDOWN_API_KEY", None)

    def test_call_scaledown_wire_and_usage(self, cfg):
        import os

        os.environ["SCALEDOWN_API_KEY"] = "sd-test"
        try:
            resp_body = {
                "model": "classify-1",
                "answers": {
                    "diff_type": {"type": "choice", "choice": "fix",
                                  "probabilities": {"fix": 0.8}, "confidence": 0.8},
                    "risk_tier": {"type": "choice", "choice": "medium", "confidence": 0.71},
                    "breaking_change": {"type": "noul", "noul": 0.02},
                },
                "usage": {"input_tokens": 152, "output_tokens": 1, "cost": 7.6e-06},
            }
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(200, json.dumps(resp_body).encode())

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                tele: dict = {}
                result = sys1.call_provider("scaledown", "diff text", self._heads(), cfg, tele)

            # request shape: plain-string state, config model, scaledown-shape
            # questions (noul takes `instructions`; no true/false criteria)
            assert captured["url"] == cfg["providers"]["scaledown_api_url"]
            assert captured["body"]["model"] == "classify-1"
            assert captured["body"]["state"] == "diff text"
            noul_q = next(q for q in captured["body"]["questions"].values() if q["type"] == "noul")
            assert "criteria" not in noul_q
            assert noul_q["instructions"]
            # answers normalize to the shared wire
            assert result["diff_type"]["label"] == "fix"
            assert result["breaking_change"]["noul"] == 0.02
            # contract telemetry keys + cost_usd from ScaleDown usage
            assert tele["http_status"] == 200
            assert tele["structured_ok"] is True
            assert tele["token_prompt"] == 152
            assert tele["token_completion"] == 1
            assert tele["token_total"] == 153
            assert tele["cost_usd"] == 7.6e-06
        finally:
            os.environ.pop("SCALEDOWN_API_KEY", None)

    def test_state_truncated_by_scaledown_max_chars(self, cfg):
        import os

        os.environ["SCALEDOWN_API_KEY"] = "sd-test"
        cfg["providers"]["scaledown_max_chars"] = 4
        try:
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(200, json.dumps({"answers": {}}).encode())

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                sys1.call_provider("scaledown", "abcdefghij", self._heads(), cfg, {})
            assert captured["body"]["state"] == "abcd"
        finally:
            os.environ.pop("SCALEDOWN_API_KEY", None)

    def test_422_error_envelope_raises(self, cfg):
        import io
        import os
        import urllib.error

        os.environ["SCALEDOWN_API_KEY"] = "sd-test"
        try:
            # ScaleDown 422s a score question whose criteria is a map instead
            # of an ordered array (probed live 2026-10-13).
            err_body = json.dumps({"detail": "criteria outside 2-10"}).encode()
            err = urllib.error.HTTPError("u", 422, "Unprocessable Entity", {}, io.BytesIO(err_body))
            with patch("urllib.request.urlopen", side_effect=err), pytest.raises(Exception) as exc:
                sys1.call_provider("scaledown", "diff text", self._heads(), cfg, {})
            assert "422" in str(exc.value)
        finally:
            os.environ.pop("SCALEDOWN_API_KEY", None)

    def test_heads_for_inherits_jev_set(self):
        assert [h.id for h in self._heads()] == [h.id for h in sys1.heads_for("change", "jev")]


class TestJuliaProvider:
    def _heads(self):
        return sys1.heads_for("change", "julia")

    def test_julia_needs_key(self, cfg):
        import os

        os.environ.pop("SUPERSONIC_API_KEY", None)
        assert is_available("julia", cfg) is False
        os.environ["SUPERSONIC_API_KEY"] = "sl-test"
        try:
            assert is_available("julia", cfg) is True
        finally:
            os.environ.pop("SUPERSONIC_API_KEY", None)

    def test_call_julia_wire_headers_and_usage(self, cfg):
        import os
        import uuid

        os.environ["SUPERSONIC_API_KEY"] = "sl-test"
        try:
            resp_body = {
                "model": "julia-1",
                "answers": {
                    "diff_type": {"type": "choice", "choice": "fix", "probabilities": {"fix": 0.8}, "confidence": 0.8},
                    "risk_tier": {"type": "choice", "choice": "medium", "confidence": 0.71},
                    "breaking_change": {"type": "noul", "noul": 0.02},
                },
                "usage": {"input_tokens": 152, "output_tokens": 0, "total_tokens": 152},
            }
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode())
                captured["headers"] = dict(req.header_items())
                return _make_resp(200, json.dumps(resp_body).encode())

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                tele: dict = {}
                result = sys1.call_provider("julia", "diff text", self._heads(), cfg, tele)

            # request shape: plain-string state, config model, Drex-shape
            # questions (julia is strictly validated: no `id`, true/false noul,
            # score criteria as an ordered array)
            assert captured["url"] == cfg["providers"]["julia_api_url"]
            assert captured["body"]["model"] == "julia-1"
            assert captured["body"]["state"] == "diff text"
            assert "id" not in next(iter(captured["body"]["questions"].values()))
            noul_q = next(q for q in captured["body"]["questions"].values() if q["type"] == "noul")
            assert set(noul_q["criteria"].keys()) == {"true", "false"}
            # wire-required headers: Idempotency-Key (a parseable uuid) and a
            # User-Agent (Cloudflare 403s the default Python-urllib signature)
            idem = captured["headers"].get("Idempotency-key") or captured["headers"].get("Idempotency-Key")
            assert idem and uuid.UUID(idem)
            assert captured["headers"].get("User-agent") or captured["headers"].get("User-Agent")
            # answers normalize to the shared wire; noul confidence absent
            assert result["diff_type"]["label"] == "fix"
            assert result["breaking_change"]["noul"] == 0.02
            assert result["breaking_change"].get("confidence") is None
            # contract telemetry keys populated from julia usage
            assert tele["http_status"] == 200
            assert tele["structured_ok"] is True
            assert tele["token_prompt"] == 152
            assert tele["token_completion"] == 0
            assert tele["token_total"] == 152
        finally:
            os.environ.pop("SUPERSONIC_API_KEY", None)

    def test_state_truncated_by_julia_max_chars(self, cfg):
        import os

        os.environ["SUPERSONIC_API_KEY"] = "sl-test"
        cfg["providers"]["julia_max_chars"] = 4
        try:
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(
                    200,
                    json.dumps(
                        {
                            "answers": {
                                "diff_type": {"type": "choice", "choice": "fix", "confidence": 0.8},
                                "risk_tier": {"type": "choice", "choice": "low", "confidence": 0.8},
                                "breaking_change": {"type": "noul", "noul": 0.5},
                            }
                        }
                    ).encode(),
                )

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                sys1.call_provider("julia", "abcdefghij", self._heads(), cfg, {})
            assert captured["body"]["state"] == "abcd"  # not scan.max_diff_chars
        finally:
            os.environ.pop("SUPERSONIC_API_KEY", None)

    def test_heads_for_inherits_jev_set(self):
        assert [h.id for h in self._heads()] == [h.id for h in sys1.heads_for("change", "jev")]

    def test_502_window_overflow_raises(self, cfg):
        import os

        os.environ["SUPERSONIC_API_KEY"] = "sl-test"
        try:
            # Julia's 8192-token window overflow surfaces as a raw 502 (no
            # structured error envelope) — the client-side julia_max_chars
            # bound is the guard; the wire surfaces the 502 as RuntimeError.
            err = urllib.error.HTTPError("u", 502, "Bad Gateway", {}, io.BytesIO(b"error code: 502"))
            with patch("urllib.request.urlopen", side_effect=err), pytest.raises(RuntimeError) as exc:
                sys1.call_provider("julia", "diff", self._heads(), cfg, {})
            assert "502" in str(exc.value)
        finally:
            os.environ.pop("SUPERSONIC_API_KEY", None)

    def test_config_view_is_url_kind(self, cfg):
        from sys1.providers import provider_config_view

        view = provider_config_view("julia", cfg)
        assert view["kind"] == "url"
        assert view["api_key_env"] == "SUPERSONIC_API_KEY"
        assert view["requires_key"] is True
        assert view["model"] == cfg["providers"]["julia_model"]

    def test_floor_registered(self):
        from sys1.gating import _DEFAULT_FLOORS

        assert _DEFAULT_FLOORS["julia"] == 0.7

    def test_in_core_roster(self):
        assert "julia" in resolve_providers("core")
        assert "julia" in resolve_providers("all")

    def test_routing_default_chain_unchanged_and_size_excluded(self, cfg):
        import os

        from sys1 import routing
        from sys1.tasks import get_task

        # julia is registered but NOT in the default chain (waits on
        # calibration evidence per the plan); the size gate respects its
        # 8,192-token bound wherever a custom chain includes it.
        os.environ["FASTINO_API_KEY"] = "fk-test"
        os.environ["DREX_API_KEY"] = "sk-test"
        os.environ["TYPESAFE_API_KEY"] = "ts-test"
        try:
            task = get_task("change")
            assert routing.route(task, "short text", cfg) == ["glide", "drex", "jev"]
        finally:
            for name in ("FASTINO_API_KEY", "DREX_API_KEY", "TYPESAFE_API_KEY"):
                os.environ.pop(name, None)
        assert "julia" in routing._SIZE_EXCLUDED


class TestLiquidProvider:
    def _heads(self):
        return sys1.heads_for("change", "liquid")

    def test_liquid_needs_key(self, cfg):
        import os

        os.environ.pop("LIQUID_API_KEY", None)
        assert is_available("liquid", cfg) is False
        os.environ["LIQUID_API_KEY"] = "liquid-test"
        try:
            assert is_available("liquid", cfg) is True
        finally:
            os.environ.pop("LIQUID_API_KEY", None)

    def test_call_liquid_wire_request_and_usage(self, cfg):
        import os

        os.environ["LIQUID_API_KEY"] = "liquid-test"
        try:
            # d1 response values captured verbatim from the 2026-10-05 live
            # probe (docs/plans/2026-10-05-liquid-d1-provider.md): d1 speaks
            # the TypeSafe System One wire like jev/drex/glide/julia.
            resp_body = {
                "model": "d1",
                "answers": {
                    "diff_type": {
                        "type": "choice",
                        "choice": "fix",
                        "probabilities": {
                            "fix": 0.9305552282044707,
                            "refactor": 0.06740919062018329,
                        },
                        "confidence": 0.8958328423067061,
                    },
                    "risk_tier": {
                        "type": "choice",
                        "choice": "low",
                        "probabilities": {"low": 0.6, "medium": 0.3, "high": 0.1},
                        "confidence": 0.4,
                    },
                    "breaking_change": {"type": "noul", "noul": 0.9433475830538234},
                },
                "usage": {"input_tokens": 171, "output_tokens": 0, "cost": 0.00000684},
            }
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode())
                captured["headers"] = dict(req.header_items())
                return _make_resp(200, json.dumps(resp_body).encode())

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                tele: dict = {}
                result = sys1.call_provider("liquid", "diff text", self._heads(), cfg, tele)

            # request shape: the /decisions/v1/systemone origin, a plain-string
            # state, Drex-shape questions (d1's validation is as strict as
            # Drex's) — unlike julia, no idempotency/UA headers required
            assert captured["url"] == cfg["providers"]["liquid_api_url"]
            assert captured["url"] == "https://api.liquid.ai/decisions/v1/systemone"
            assert captured["body"]["model"] == "d1"
            assert captured["body"]["state"] == "diff text"
            assert "images" not in captured["body"]  # a text-only call stays minimal
            assert "id" not in next(iter(captured["body"]["questions"].values()))
            noul_q = next(
                q for q in captured["body"]["questions"].values() if q["type"] == "noul"
            )
            assert isinstance(noul_q["instructions"], str) and noul_q["instructions"]
            assert set(noul_q["criteria"].keys()) == {"true", "false"}
            auth = captured["headers"].get("Authorization") or captured["headers"].get(
                "authorization"
            )
            assert auth == "Bearer liquid-test"
            # answers normalize to the shared wire; choice confidence is the
            # Jev-family distribution statistic, kept as returned; noul
            # answers carry no confidence field
            assert result["diff_type"]["label"] == "fix"
            assert result["diff_type"]["confidence"] == 0.8958328423067061
            assert result["breaking_change"]["noul"] == 0.9433475830538234
            assert result["breaking_change"].get("confidence") is None
            # d1's usage payload maps onto the contract keys + cost extra
            assert tele["http_status"] == 200
            assert tele["structured_ok"] is True
            assert tele["token_prompt"] == 171
            assert tele["token_completion"] == 0
            assert tele["token_total"] == 171
            assert tele["cost_usd"] == 0.00000684
        finally:
            os.environ.pop("LIQUID_API_KEY", None)

    def test_images_passthrough(self, cfg):
        import os

        os.environ["LIQUID_API_KEY"] = "liquid-test"
        try:
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(
                    200,
                    json.dumps(
                        {
                            "answers": {
                                "diff_type": {
                                    "type": "choice",
                                    "choice": "fix",
                                    "confidence": 0.9,
                                }
                            }
                        }
                    ).encode(),
                )

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                tele: dict = {}
                sys1.call_provider(
                    "liquid",
                    "screenshot",
                    self._heads(),
                    cfg,
                    tele,
                    images=[
                        "data:image/png;base64,AAAA",
                        {"content_type": "image/jpeg", "base64": "BBBB"},
                    ],
                )
            # the OpenJev extension: images ride in the body (both shapes d1
            # accepts), only the count lands in telemetry
            assert captured["body"]["images"] == [
                "data:image/png;base64,AAAA",
                {"content_type": "image/jpeg", "base64": "BBBB"},
            ]
            assert tele["image_count"] == 2
        finally:
            os.environ.pop("LIQUID_API_KEY", None)

    def test_state_truncated_by_liquid_max_chars(self, cfg):
        import os

        os.environ["LIQUID_API_KEY"] = "liquid-test"
        cfg["providers"]["liquid_max_chars"] = 4
        try:
            captured = {}

            def _fake_urlopen(req, timeout=None):
                captured["body"] = json.loads(req.data.decode())
                return _make_resp(200, json.dumps({"answers": {}}).encode())

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                sys1.call_provider("liquid", "abcdefghij", self._heads(), cfg, {})
            assert captured["body"]["state"] == "abcd"  # not scan.max_diff_chars
        finally:
            os.environ.pop("LIQUID_API_KEY", None)

    def test_heads_for_inherits_jev_set(self):
        assert [h.id for h in self._heads()] == [
            h.id for h in sys1.heads_for("change", "jev")
        ]

    def test_422_validation_fails_fast(self, cfg):
        import os

        os.environ["LIQUID_API_KEY"] = "liquid-test"
        try:
            # d1's validation is strict (as strict as Drex's): a malformed
            # question shape is a 422, and http_post_json surfaces it
            # immediately — no retry
            err = urllib.error.HTTPError(
                "u", 422, "Unprocessable Entity", {}, io.BytesIO(b'{"detail":"bad question"}')
            )
            with (
                patch("urllib.request.urlopen", side_effect=err),
                pytest.raises(RuntimeError) as exc,
            ):
                sys1.call_provider("liquid", "diff", self._heads(), cfg, {})
            assert "422" in str(exc.value)
        finally:
            os.environ.pop("LIQUID_API_KEY", None)

    def test_config_view_is_url_kind(self, cfg):
        from sys1.providers import provider_config_view

        view = provider_config_view("liquid", cfg)
        assert view["kind"] == "url"
        assert view["api_key_env"] == "LIQUID_API_KEY"
        assert view["requires_key"] is True
        assert view["model"] == cfg["providers"]["liquid_model"]

    def test_floor_registered(self):
        from sys1.gating import _DEFAULT_FLOORS

        assert _DEFAULT_FLOORS["liquid"] == 0.7

    def test_in_core_roster(self):
        assert "liquid" in resolve_providers("core")
        assert "liquid" in resolve_providers("all")

    def test_routing_default_chain_unchanged_and_not_size_excluded(self, cfg):
        import os

        from sys1 import routing
        from sys1.tasks import get_task

        # liquid is registered but NOT in the default chain (same posture as
        # julia: waits on calibration evidence); d1's 65,536-token window
        # (liquid_max_chars = 150K chars of state) exceeds the router's
        # long-input threshold, so it never needs the size-gate exclusion
        os.environ["FASTINO_API_KEY"] = "fk-test"
        os.environ["DREX_API_KEY"] = "sk-test"
        os.environ["TYPESAFE_API_KEY"] = "ts-test"
        os.environ["LIQUID_API_KEY"] = "liquid-test"
        try:
            task = get_task("change")
            assert routing.route(task, "short text", cfg) == ["glide", "drex", "jev"]
        finally:
            for name in (
                "FASTINO_API_KEY",
                "DREX_API_KEY",
                "TYPESAFE_API_KEY",
                "LIQUID_API_KEY",
            ):
                os.environ.pop(name, None)
        assert "liquid" not in routing._SIZE_EXCLUDED
