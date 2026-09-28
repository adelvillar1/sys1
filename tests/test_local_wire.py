"""Local fastino wire: prescribed classification API (schema spec, parsing,
classifier-server contract, server-first fallback).

The vendor prescription (gliner25-decide-playground/model.py) is the bar:
one ClassificationSchema for all heads, one decode, full probabilities. None
of these tests load a model — the real-model check is a manual verification
step documented in the plan.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from sys1 import make_choice, make_noul, make_score
from sys1.parsers import parse_gliner2
from sys1.providers import _gliner_wire, _post_local_server, classification_schema_spec

REPO = Path(__file__).parent.parent


def _classifier_server():
    """Import service/classifier_server.py without the service package."""
    spec = importlib.util.spec_from_file_location("classifier_server", REPO / "service" / "classifier_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── spec builder (pure) ──────────────────────────────────────────────────────


class TestSchemaSpec:
    def test_choice_head_is_single(self):
        head = make_choice("Which type?", ["feat", "fix"], id="diff_type")
        assert classification_schema_spec([head]) == {
            "diff_type": {"kind": "single", "labels": ["feat", "fix"], "instruction": "Which type?"}
        }

    def test_instruction_rides_in_the_spec(self):
        """Without instruction the model only sees the label set — query-
        dependent tasks (semantic find) were answer-blind before this."""
        head = make_choice('Contains the answer to: "refund window"?', ["L001", "L002"], id="where")
        spec = classification_schema_spec([head])
        assert spec["where"]["instruction"] == 'Contains the answer to: "refund window"?'

    def test_noul_head_is_binary_single(self):
        head = make_noul("Is this breaking?", id="breaking_change")
        spec = classification_schema_spec([head])
        assert spec["breaking_change"]["kind"] == "single"
        assert spec["breaking_change"]["labels"] == ["yes", "no"]

    def test_score_head_is_ordinal_with_min(self):
        head = make_score("Severity?", score_min=1, score_max=3, id="severity")
        spec = classification_schema_spec([head])
        assert spec["severity"]["kind"] == "ordinal"
        assert spec["severity"]["labels"] == ["1", "2", "3"]
        assert spec["severity"]["score_min"] == 1
        assert spec["severity"]["instruction"] == "Severity?"

    def test_score_head_uses_descriptions_as_levels(self):
        head = make_score(
            "Severity?",
            score_min=1,
            score_max=3,
            legend=["No threat", "Policy violation", "Active attack"],
            id="severity",
        )
        spec = classification_schema_spec([head])
        assert spec["severity"]["labels"] == ["No threat", "Policy violation", "Active attack"]


# ── parser ───────────────────────────────────────────────────────────────────


class TestParseGliner2:
    def test_choice_answer_carries_full_distribution(self):
        head = make_choice("Which type?", ["feat", "fix"], id="diff_type")
        parsed = {"diff_type": {"labels": ["feat"], "probabilities": {"feat": 0.7, "fix": 0.3}}}
        (answer,) = parse_gliner2(parsed, [head]).values()
        assert answer["label"] == "feat"
        assert answer["confidence"] == 0.7  # max(probs)
        assert answer["probabilities"] == {"feat": 0.7, "fix": 0.3}
        assert answer["type"] == "choice"

    def test_multi_label_answer_keeps_all_labels(self):
        head = make_choice("Which?", ["a", "b", "c"], id="h", multi_label=True)
        parsed = {"h": {"labels": ["a", "b"], "probabilities": {"a": 0.6, "b": 0.55, "c": 0.1}}}
        answer = parse_gliner2(parsed, [head])["h"]
        assert answer["label"] == "a"
        assert answer["labels"] == ["a", "b"]

    def test_noul_answer_is_docs_accurate_no_confidence(self):
        head = make_noul("Breaking?", id="breaking_change")
        parsed = {"breaking_change": {"labels": ["yes"], "probabilities": {"yes": 0.69, "no": 0.31}}}
        answer = parse_gliner2(parsed, [head])["breaking_change"]
        assert answer["type"] == "noul"
        assert answer["noul"] == 0.69  # probability of "true"
        assert "confidence" not in answer  # docs-accurate: gating derives max(p, 1-p)

    def test_noul_missing_yes_probability_gates_as_decline(self):
        head = make_noul("Breaking?", id="breaking_change")
        parsed = {"breaking_change": {"labels": [], "probabilities": {}}}
        answer = parse_gliner2(parsed, [head])["breaking_change"]
        assert answer["noul"] is None  # _label_of -> None -> escalate

    def test_score_answer_maps_ordinal_level_to_number(self):
        head = make_score("Severity?", score_min=1, score_max=3, id="severity")
        spec = classification_schema_spec([head])
        parsed = {"severity": {"labels": ["2"], "probabilities": {"1": 0.2, "2": 0.5, "3": 0.3}}}
        answer = parse_gliner2(parsed, [head], spec)["severity"]
        assert answer["score"] == 2.0  # score_min + level index
        assert answer["confidence"] == 0.5

    def test_score_answer_with_descriptions_builds_legend(self):
        head = make_score(
            "Severity?", score_min=1, score_max=3, legend=["none", "low", "high"], id="severity"
        )
        spec = classification_schema_spec([head])
        parsed = {"severity": {"labels": ["high"], "probabilities": {"none": 0.2, "low": 0.1, "high": 0.7}}}
        answer = parse_gliner2(parsed, [head], spec)["severity"]
        assert answer["score"] == 3.0
        assert answer["legend"] == {"1": "none", "2": "low", "3": "high"}

    def test_missing_head_declines(self):
        head = make_choice("Which?", ["a", "b"], id="h")
        (answer,) = parse_gliner2({}, [head]).values()
        assert answer["confidence"] == 0.0
        assert answer["_declined"] == "missing"

    def test_error_entry_declines(self):
        head = make_choice("Which?", ["a", "b"], id="h")
        (answer,) = parse_gliner2({"h": {"_error": "CUDA OOM"}}, [head]).values()
        assert "CUDA OOM" in answer["_declined"]

    def test_legacy_entry_still_parses(self):
        """Pre-migration mocks/fixtures emit {label, confidence} — keep them working."""
        head = make_choice("Which type?", ["feat", "fix"], id="diff_type")
        parsed = {"diff_type": {"label": "feat", "confidence": 0.89}}
        (answer,) = parse_gliner2(parsed, [head]).values()
        assert answer == {"label": "feat", "confidence": 0.89, "type": "choice"}


# ── classifier server (HTTP contract, stubbed inference) ─────────────────────


@pytest.fixture
def server():
    cs = _classifier_server()
    cs.Handler.model_name = "fastino/GLiNER2.5-Decide"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield cs, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


class TestClassifierServer:
    def test_infer_happy_path_returns_playground_contract(self, server):
        cs, url = server
        with patch.object(cs, "infer", return_value={"h1": {"labels": ["a"], "probabilities": {"a": 0.9}}}):
            resp = _post_local_server(url, {"text": "x", "tasks": {"h1": {"kind": "single", "labels": ["a"]}}}, 5)
        assert resp["tasks"]["h1"]["labels"] == ["a"]
        assert resp["meta"]["model"] == "fastino/GLiNER2.5-Decide"
        assert resp["meta"]["decoder"] == "independent"
        assert isinstance(resp["meta"]["ms"], int)

    def test_ready_reports_model(self, server):
        _, url = server
        with urllib.request.urlopen(url + "/ready", timeout=5) as resp:
            body = json.loads(resp.read())
        assert body == {"ready": False, "model": "fastino/GLiNER2.5-Decide"}

    def test_model_mismatch_is_409(self, server):
        _, url = server
        payload = {"text": "x", "tasks": {"h": {"kind": "single", "labels": ["a"]}}, "model": "fastino/other"}
        try:
            _post_local_server(url, payload, 5)
            raise AssertionError("expected HTTP 409")
        except urllib.error.HTTPError as e:
            assert e.code == 409

    def test_missing_text_is_400(self, server):
        _, url = server
        try:
            _post_local_server(url, {"tasks": {}}, 5)
            raise AssertionError("expected HTTP 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_inference_failure_is_500(self, server):
        cs, url = server
        with patch.object(cs, "infer", side_effect=RuntimeError("CUDA OOM")):
            try:
                _post_local_server(url, {"text": "x", "tasks": {"h": {"kind": "single", "labels": ["a"]}}}, 5)
                raise AssertionError("expected HTTP 500")
            except urllib.error.HTTPError as e:
                assert e.code == 500

    def test_build_schema_dispatches_kinds(self):
        cs = _classifier_server()

        class FakeSchema:
            def __init__(self):
                self.calls = []

            def single(self, name, labels, instruction=None):
                self.calls.append(("single", name, labels, instruction))

            def ordinal(self, name, labels, instruction=None):
                self.calls.append(("ordinal", name, labels, instruction))

        schema = cs.build_schema(
            FakeSchema(),
            {"a": {"kind": "single", "labels": ["x"], "instruction": "q?"}, "b": {"kind": "ordinal", "labels": ["1"]}},
        )
        assert schema.calls == [("single", "a", ["x"], "q?"), ("ordinal", "b", ["1"], None)]

    def test_extract_skips_absent_tasks(self):
        cs = _classifier_server()

        class FakeResult:
            def value(self, name):
                if name == "absent":
                    raise KeyError(name)
                return "a"

            def probabilities(self, name):
                if name == "absent":
                    raise KeyError(name)
                return {"a": 0.9}

        out = cs.extract(FakeResult(), {"h1": {}, "absent": {}})
        assert out == {"h1": {"labels": ["a"], "probabilities": {"a": 0.9}}}


# ── wire: server-first with subprocess fallback ──────────────────────────────


class TestWireServingModes:
    def _heads(self):
        return [make_choice("Which type?", ["feat", "fix"], id="diff_type")]

    def test_server_mode_parses_prescribed_shape(self, cfg):
        cs = _classifier_server()
        cs.Handler.model_name = "fastino/GLiNER2.5-Decide"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        cfg["providers"]["local_server_url"] = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with patch.object(
                cs,
                "infer",
                return_value={"diff_type": {"labels": ["feat"], "probabilities": {"feat": 0.7, "fix": 0.3}}},
            ):
                tele: dict = {}
                out = _gliner_wire("local")("diff text", self._heads(), cfg, tele)
            assert tele["mode"] == "server"
            assert out["diff_type"]["label"] == "feat"
            assert out["diff_type"]["confidence"] == 0.7
        finally:
            httpd.shutdown()

    def test_server_down_falls_back_to_venv(self, cfg):
        # configured server is unreachable and the venv path is missing
        cfg["providers"]["local_server_url"] = "http://127.0.0.1:1"
        with pytest.raises(RuntimeError, match="venv not found"):
            _gliner_wire("local")("text", self._heads(), cfg, {})

    def test_model_mismatch_falls_back(self, cfg):
        cs = _classifier_server()
        cs.Handler.model_name = "fastino/other-model"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        cfg["providers"]["local_server_url"] = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with pytest.raises(RuntimeError, match="venv not found"):
                # venv missing -> the fallback after the mismatch surfaces here
                _gliner_wire("local")("text", self._heads(), cfg, {})
        finally:
            httpd.shutdown()
