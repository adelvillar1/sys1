"""Control-dashboard tests: flag mutation (state.json round-trip), probe,
recent JSONL tail, auth boundaries, and the page itself.

Fixtures redirect SYS1_STATE_FILE and the JSONL log dir into tmp_path so
mutations never touch the real ~/.config/sys1/state.json or ~/.local/share.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import sys1
from sys1.types import Answer, Head, Provider

SERVICE_MAIN = Path(__file__).resolve().parent.parent / "service" / "main.py"


def _load_service_app():
    """Fresh-import service.main so module-level env reads (bearer) apply, and
    the dashboard router gets mounted from service/dashboard.py."""
    sys.modules.pop("sys1_service_main", None)
    sys.modules.pop("sys1_dashboard", None)
    spec = importlib.util.spec_from_file_location("sys1_service_main", SERVICE_MAIN)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sys1_service_main"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point the state file + JSONL logs at tmp_path for the whole test."""
    monkeypatch.setenv("SYS1_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("SYS1_CLASSIFY__LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("SYS1_BEARER_TOKEN", raising=False)
    return tmp_path


@pytest.fixture
def client(isolated_state):
    from fastapi.testclient import TestClient

    return TestClient(_load_service_app().app)


@pytest.fixture
def auth_client(isolated_state):
    """Service with SYS1_BEARER_TOKEN set."""
    isolated_state  # noqa: B018 — fixture ordering: env must be patched before import
    import os

    os.environ["SYS1_BEARER_TOKEN"] = "dash-tok"
    try:
        from fastapi.testclient import TestClient

        yield TestClient(_load_service_app().app)
    finally:
        del os.environ["SYS1_BEARER_TOKEN"]


AUTH = {"Authorization": "Bearer dash-tok"}


# ── mounting ──────────────────────────────────────────────────────────────────


class TestMounted:
    def test_dashboard_router_mounted(self, client):
        assert client.get("/dashboard").status_code == 200

    def test_root_lists_dashboard(self, client):
        body = client.get("/").json()
        assert body["dashboard"] == "/dashboard"
        assert "/v1/providers" in body["endpoints"]


# ── GET /v1/providers ─────────────────────────────────────────────────────────


class TestProvidersEndpoint:
    def test_payload_shape(self, client):
        r = client.get("/v1/providers")
        assert r.status_code == 200
        body = r.json()
        assert body["auth"] == "open"
        assert set(body["providers"]) >= {"decide", "jev", "clm", "tev1", "decide_1b"}
        assert "core" not in body["enabled"] and "decide" in body["enabled"]

    def test_open_even_with_bearer(self, auth_client):
        # read-only shape endpoint stays open like /v1/health (no secrets in it)
        assert auth_client.get("/v1/providers").status_code == 200


# ── POST /v1/providers/{pid}/flag ─────────────────────────────────────────────


class TestFlagEndpoint:
    def test_enable_candidate_persists(self, client, isolated_state):
        r = client.post("/v1/providers/clm/flag", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["providers"]["clm"]["enabled"] is True
        state = json.loads((isolated_state / "state.json").read_text())
        assert "clm" in state["providers"]["enabled"]
        # a fresh load_config() (what CLI/hooks see next run) agrees
        assert "clm" in sys1.enabled_set(sys1.load_config())

    def test_disable_core_provider(self, client):
        r = client.post("/v1/providers/decide/flag", json={"enabled": False})
        assert r.status_code == 200
        row = r.json()["providers"]["decide"]
        assert row["enabled"] is False and row["available"] is False
        assert row["reason"] == "disabled by providers.enabled flag"

    def test_disable_then_reenable(self, client):
        client.post("/v1/providers/kev/flag", json={"enabled": True})
        assert "kev" in sys1.enabled_set(sys1.load_config())
        client.post("/v1/providers/kev/flag", json={"enabled": False})
        assert "kev" not in sys1.enabled_set(sys1.load_config())

    def test_core_token_survives_explicit_enable(self, client):
        client.post("/v1/providers/clm/flag", json={"enabled": True})
        got = sys1.enabled_set(sys1.load_config())
        assert {"decide", "jev", "local", "modernbert"} <= got and "clm" in got

    def test_unknown_provider_404(self, client):
        assert client.post("/v1/providers/magic/flag", json={"enabled": True}).status_code == 404

    def test_requires_bearer_when_set(self, auth_client):
        r = auth_client.post("/v1/providers/clm/flag", json={"enabled": True})
        assert r.status_code == 401
        r2 = auth_client.post("/v1/providers/clm/flag", json={"enabled": True}, headers=AUTH)
        assert r2.status_code == 200


# ── POST /v1/providers/{pid}/probe ────────────────────────────────────────────


class TestProbeEndpoint:
    def test_probe_reports_reachability(self, client):
        r = client.post("/v1/providers/clm/probe")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False  # 127.0.0.1:8451 has no listener in CI
        assert "8451" in body["detail"]

    def test_probe_venv_provider(self, client):
        # ok depends on whether the local venv exists on this machine; the
        # contract is that the detail always names the venv path.
        body = client.post("/v1/providers/local/probe").json()
        assert isinstance(body["ok"], bool)
        assert "venv" in body["detail"]

    def test_probe_unknown_404(self, client):
        assert client.post("/v1/providers/magic/probe").status_code == 404

    def test_probe_requires_bearer_when_set(self, auth_client):
        assert auth_client.post("/v1/providers/clm/probe").status_code == 401
        assert auth_client.post("/v1/providers/clm/probe", headers=AUTH).status_code == 200


# ── GET /v1/recent ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_ready_provider():
    def classify(text: str, heads: list[Head], cfg: dict, tele: dict, images: list | None = None) -> dict[str, Answer]:
        return {h.id: {"label": h.labels[0] if h.labels else "x", "confidence": 0.9, "type": h.kind} for h in heads}

    p = Provider(id="fake_dash", name="fake", classify=classify, is_available=lambda c: True)
    sys1.register(p)
    yield p
    sys1.REGISTRY.pop("fake_dash", None)


class TestRecentEndpoint:
    def test_empty_logs_ok(self, client):
        r = client.get("/v1/recent?limit=5")
        assert r.status_code == 200
        assert r.json()["rows"] == []

    def test_classify_then_recent(self, client, fake_ready_provider):
        r = client.post("/v1/classify", json={"provider": "fake_dash", "task": "change", "text": "hello"})
        assert r.status_code == 200
        rows = client.get("/v1/recent?limit=5").json()["rows"]
        assert len(rows) == 1
        row = rows[0]
        assert row["op"] == "classify"
        assert row["verdict"] == "pass"
        assert "diff_type=feat" in row["labels"]

    def test_recent_requires_bearer_when_set(self, auth_client):
        assert auth_client.get("/v1/recent").status_code == 401
        assert auth_client.get("/v1/recent", headers=AUTH).status_code == 200

    def test_limit_clamped(self, client):
        assert client.get("/v1/recent?limit=0").status_code == 422
        assert client.get("/v1/recent?limit=999").status_code == 422


# ── the page ──────────────────────────────────────────────────────────────────


class TestDashboardPage:
    def test_page_served_open(self, auth_client):
        # the HTML itself is always served; the JS prompts for a token
        r = auth_client.get("/dashboard")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_page_content(self, client):
        html = client.get("/dashboard").text
        assert "sys1 · control" in html
        assert "/v1/providers" in html
        assert "Live classify" in html
        assert "Recent activity" in html
