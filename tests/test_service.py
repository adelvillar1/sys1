"""Service tests via FastAPI TestClient.

A fake provider is registered against `sys1.REGISTRY` for the duration of the
test so we exercise the full HTTP → api → provider → telemetry → JSONL round
trip without making network calls.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import sys1
from sys1.types import Answer, Head, Provider

SERVICE_MAIN = Path(__file__).resolve().parent.parent / "service" / "main.py"


def _load_service_app():
    """Fresh-import service.main each time so env changes (bearer token) apply."""
    if SERVICE_MAIN is None or not SERVICE_MAIN.exists():
        pytest.skip("service/main.py not found")
    # Drop the cached module so a re-import re-runs the module body.
    sys.modules.pop("sys1_service_main", None)
    spec = importlib.util.spec_from_file_location("sys1_service_main", SERVICE_MAIN)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sys1_service_main"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def service_module(tmp_path, monkeypatch):
    """Load service.main with a tmp log dir and no bearer token."""
    monkeypatch.setenv("SYS1_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("SYS1_BEARER_TOKEN", raising=False)
    return _load_service_app()


@pytest.fixture
def client(service_module):
    from fastapi.testclient import TestClient

    return TestClient(service_module.app)


@pytest.fixture
def fake_high_provider():
    def classify(text: str, heads: list[Head], cfg: dict, tele: dict) -> dict[str, Answer]:
        tele["http_status"] = 200
        return {h.id: {"label": h.labels[0] if h.labels else "x", "confidence": 0.9, "type": h.kind} for h in heads}

    p = Provider(id="fake_http", name="fake", classify=classify, is_available=lambda c: True)
    sys1.register(p)
    yield p
    sys1.REGISTRY.pop("fake_http", None)


class TestEndpoints:
    def test_root(self, client):
        r = client.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "sys1"
        assert body["version"] == sys1.__version__

    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_health_reports_providers(self, client):
        r = client.get("/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert "providers" in body
        assert set(["decide", "jev", "local", "modernbert"]).issubset(body["providers"])

    def test_tasks_lists(self, client):
        r = client.get("/v1/tasks")
        assert r.status_code == 200
        names = [t["id"] for t in r.json()["tasks"]]
        assert "change" in names

    def test_task_by_id(self, client):
        r = client.get("/v1/tasks/change")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "change"
        head_ids = [h["id"] for h in body["heads"]]
        assert "diff_type" in head_ids

    def test_task_404(self, client):
        assert client.get("/v1/tasks/nope").status_code == 404

    def test_classify_with_registered_provider(self, client, fake_high_provider):
        r = client.post(
            "/v1/classify",
            json={"provider": "fake_http", "task": "change", "text": "diff text"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verdict"] == "pass"
        assert body["task"] == "change"
        assert "fake_http" in body["providers_used"]
        assert body["answers"]["fake_http"]["diff_type"]["label"] == "feat"  # first label
        # input_sha256 should be a 16-char hex string
        assert len(body["input_sha256"]) == 16

    def test_classify_unavailable_provider(self, client):
        # decide has no API key in this env, so classify should still return 200
        # but with an "error" verdict (fail-open, not 500)
        r = client.post(
            "/v1/classify",
            json={"provider": "decide", "task": "change", "text": "diff text"},
        )
        assert r.status_code == 200
        assert r.json()["verdict"] == "error"

    def test_cascade_stops_at_first_clearing(self, client, fake_high_provider):
        r = client.post(
            "/v1/cascade",
            json={"chain": ["fake_http", "decide"], "task": "change", "text": "diff text"},
        )
        assert r.status_code == 200
        body = r.json()
        # fake_http clears the floor, so decide is never consulted
        assert body["verdict"] == "pass"
        assert list(body["answers"]) == ["fake_http"]

    def test_feedback_round_trip(self, client, tmp_path):
        r = client.post(
            "/v1/feedback",
            json={
                "input_sha256": "deadbeefcafebabe",
                "task": "change",
                "head_id": "diff_type",
                "predicted_label": "feat",
                "predicted_confidence": 0.91,
                "provider": "decide",
                "actual_label": "feat",
                "escalated": False,
            },
        )
        assert r.status_code == 200
        assert r.json()["recorded"] is True

    def test_calibration_empty_log_ok(self, client, tmp_path, monkeypatch):
        # Point the service's cfg at an empty log dir so we exercise the
        # "no records yet" path on /v1/calibration.
        r = client.get("/v1/calibration?days=1")
        assert r.status_code == 200
        body = r.json()
        assert body["days"] == 1
        assert "providers" in body

    def test_classify_then_calibration_sees_it(self, client, fake_high_provider):
        # Run a classify (which logs to the default cfg dir)
        client.post("/v1/classify", json={"provider": "fake_http", "task": "change", "text": "hello"})
        # And one feedback row
        client.post(
            "/v1/feedback",
            json={
                "input_sha256": "x",
                "task": "change",
                "head_id": "diff_type",
                "predicted_label": "feat",
                "predicted_confidence": 0.9,
                "provider": "fake_http",
                "actual_label": "feat",
                "escalated": False,
            },
        )
        r = client.get("/v1/calibration?days=1")
        assert r.status_code == 200
        body = r.json()
        # At least one classify record and one feedback record from our writes.
        # (Earlier tests may have written more; we only assert "≥ 1".)
        assert body["classify_records"] >= 1
        assert body["feedback_records"] >= 1


class TestAuth:
    def test_bearer_required_when_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYS1_BEARER_TOKEN", "secret-tok")
        mod = _load_service_app()
        from fastapi.testclient import TestClient

        c = TestClient(mod.app)
        # /v1/health is intentionally open
        assert c.get("/v1/health").status_code == 200
        # /v1/classify requires auth
        r = c.post("/v1/classify", json={"provider": "decide", "task": "change", "text": "x"})
        assert r.status_code == 401
        # with the token, it works
        r2 = c.post(
            "/v1/classify",
            json={"provider": "decide", "task": "change", "text": "x"},
            headers={"Authorization": "Bearer secret-tok"},
        )
        assert r2.status_code == 200
