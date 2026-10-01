"""Provider-flag tests: enabled/disabled tokens, candidate registration,
state-file persistence, and the report the dashboard + doctor render.
"""

from __future__ import annotations

import pytest

import sys1
from sys1.providers import (
    availability_reason,
    enabled_set,
    is_configured,
    is_provider_enabled,
    provider_config_view,
    provider_report,
    resolve_providers,
)

CANDIDATES = ["clm", "openjev", "kev", "tev1", "laya", "decide_1b", "decide_multi"]
CORE = ["decide", "jev", "drex", "glide", "local", "modernbert"]


# ── registry shape ────────────────────────────────────────────────────────────


class TestRegistryTiers:
    def test_core_and_candidates_registered(self):
        for pid in CORE + CANDIDATES:
            assert pid in sys1.REGISTRY, pid

    def test_tier_labels(self):
        assert {sys1.REGISTRY[p].tier for p in CORE} == {"core"}
        assert {sys1.REGISTRY[p].tier for p in CANDIDATES} == {"candidate"}

    def test_wires_assigned(self):
        assert sys1.REGISTRY["clm"].wire == "jev"
        assert sys1.REGISTRY["kev"].wire == "openai"
        assert sys1.REGISTRY["decide_1b"].wire == "gliner"


# ── enabled_set tokens ────────────────────────────────────────────────────────


class TestEnabledSet:
    def test_default_is_core(self, cfg):
        assert enabled_set(cfg) == set(CORE)

    def test_all_token(self, cfg):
        cfg["providers"]["enabled"] = "all"
        assert enabled_set(cfg) == set(sys1.REGISTRY)

    def test_star_token(self, cfg):
        cfg["providers"]["enabled"] = "*"
        assert set(CANDIDATES) <= enabled_set(cfg)

    def test_optin_token(self, cfg):
        cfg["providers"]["enabled"] = "optin"
        got = enabled_set(cfg)
        assert set(CANDIDATES) <= got
        assert "decide" not in got  # optin means candidates only

    def test_candidates_token_alias(self, cfg):
        cfg["providers"]["enabled"] = "candidates"
        assert set(CANDIDATES) <= enabled_set(cfg)

    def test_explicit_adds_to_core(self, cfg):
        cfg["providers"]["enabled"] = "core,clm"
        got = enabled_set(cfg)
        assert set(CORE) <= got and "clm" in got and "kev" not in got

    def test_disabled_wins(self, cfg):
        cfg["providers"]["enabled"] = "all"
        cfg["providers"]["disabled"] = "clm,local"
        got = enabled_set(cfg)
        assert "clm" not in got and "local" not in got and "decide" in got

    def test_unknown_id_kept(self, cfg):
        cfg["providers"]["enabled"] = "core,magic"
        assert "magic" in enabled_set(cfg)  # custom providers may register later

    def test_is_provider_enabled(self, cfg):
        assert is_provider_enabled("decide", cfg)
        assert not is_provider_enabled("clm", cfg)


# ── resolve_providers with the new tokens ────────────────────────────────────


class TestResolveTokens:
    def test_core_token(self):
        assert set(resolve_providers("core")) == set(CORE)

    def test_optin_token(self):
        assert set(CANDIDATES) <= set(resolve_providers("optin"))

    def test_all_includes_candidates(self):
        assert set(CANDIDATES) <= set(resolve_providers("all"))


# ── candidate availability reporting ─────────────────────────────────────────


class TestCandidateReporting:
    def test_disabled_reason(self, cfg):
        assert availability_reason("clm", cfg) == "disabled by providers.enabled flag"

    def test_not_registered_reason(self, cfg):
        assert availability_reason("magic", cfg) == "not registered"

    def test_configured_false_without_endpoint(self, cfg):
        cfg["providers"]["enabled"] = "all"
        # tev1 is a hosted candidate: configured only when its key env is set
        assert is_configured("tev1", cfg) is False
        assert "TOGETHER_API_KEY" in availability_reason("tev1", cfg)

    def test_report_fields(self, cfg):
        rep = provider_report(cfg)
        row = rep["clm"]
        for key in ("id", "name", "tier", "wire", "enabled", "configured", "available", "reason", "config", "meta"):
            assert key in row, key
        assert row["enabled"] is False
        assert row["available"] is False
        assert row["meta"]["setup"]

    def test_report_covers_registry(self, cfg):
        assert set(provider_report(cfg)) == set(sys1.REGISTRY)

    def test_config_view_never_leaks_key(self, cfg):
        cfg["providers"]["enabled"] = "all"
        view = provider_config_view("tev1", cfg)
        assert "api_key_env" in view and "api_key" not in view


# ── call_provider flag gate ───────────────────────────────────────────────────


class TestCallGate:
    def test_disabled_raises_with_hint(self, cfg):
        with pytest.raises(RuntimeError, match="disabled"):
            sys1.call_provider("clm", "text", [], cfg, {})

    def test_core_provider_passes_gate_when_unavailable(self, cfg):
        # decide gets past the flag check and fails later on its missing key
        with pytest.raises(Exception, match=r"API_KEY|api_key"):
            sys1.call_provider("decide", "text", [], cfg, {})

    def test_unregistered_raises_keyerror(self, cfg):
        with pytest.raises(KeyError):
            sys1.call_provider("magic", "text", [], cfg, {})


# ── state file round-trip (what the dashboard toggles persist) ───────────────


class TestStateFile:
    def test_write_then_reload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYS1_STATE_FILE", str(tmp_path / "state.json"))
        sys1.write_state_flags(["core", "clm"], ["jev"])
        cfg = sys1.load_config()
        got = enabled_set(cfg)
        assert "clm" in got
        assert "jev" not in got
        assert "decide" in got

    def test_state_wins_over_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYS1_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("SYS1_PROVIDERS__ENABLED", "core,kev")
        sys1.write_state_flags(["core", "clm"], ["kev"])
        got = enabled_set(sys1.load_config())
        assert "clm" in got and "kev" not in got

    def test_broken_state_ignored(self, tmp_path, monkeypatch):
        p = tmp_path / "state.json"
        p.write_text("not json at all")
        monkeypatch.setenv("SYS1_STATE_FILE", str(p))
        assert "decide" in enabled_set(sys1.load_config())  # never breaks the tool

    def test_missing_state_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYS1_STATE_FILE", str(tmp_path / "absent.json"))
        assert enabled_set(sys1.load_config()) == set(CORE)
