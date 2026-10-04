"""Tests for the canonical provider env file (config.load_provider_env).

sys1 self-configures: the layer loads its own env file so consumers get the
full provider pool from any shell. Covered here:

  resolution:  $SYS1_ENV_FILE > ~/.config/sys1/env > legacy dev-decisions path
  semantics:   setdefault — an exported value always wins over the file
  tolerance:   comments, `export ` prefixes, blanks, malformed lines skipped;
                missing/unreadable file is a no-op
  safety:      the return carries key NAMES and the path, never values
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sys1.config import env_file_path, load_config, load_provider_env


@pytest.fixture
def clean_key_env(monkeypatch):
    """Strip every key the env file might inject, and pin the file path."""
    for name in ("FASTINO_API_KEY", "TYPESAFE_API_KEY", "DREX_API_KEY",
                 "SUPERSONIC_API_KEY", "LIQUID_API_KEY", "SYS1_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture(autouse=True)
def _purge_injected_keys():
    """load_provider_env writes real os.environ keys (the production
    behavior); revert whatever a test injected so later modules don't
    inherit key material."""
    import os

    before = set(os.environ)
    yield
    for name in set(os.environ) - before:
        del os.environ[name]


def write_env(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "env"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_file_is_a_noop(clean_key_env, tmp_path, monkeypatch):
    monkeypatch.setenv("SYS1_ENV_FILE", str(tmp_path / "absent"))
    out = load_provider_env()
    assert out == {"path": str(tmp_path / "absent"), "loaded": []} or out["loaded"] == []
    assert "FASTINO_API_KEY" not in __import__("os").environ


def test_resolution_sys1_env_file_override(clean_key_env, tmp_path, monkeypatch):
    p = write_env(tmp_path, "FASTINO_API_KEY=abc\n")
    monkeypatch.setenv("SYS1_ENV_FILE", str(p))
    assert env_file_path() == p
    out = load_provider_env()
    assert out["path"] == str(p) and out["loaded"] == ["FASTINO_API_KEY"]


def test_canonical_beats_legacy(clean_key_env, tmp_path, monkeypatch):
    """~/.config/sys1/env wins over the legacy dev-decisions path when both exist."""
    home = tmp_path / "home"
    (home / ".config" / "sys1").mkdir(parents=True)
    (home / ".config" / "dev-decisions").mkdir(parents=True)
    (home / ".config" / "sys1" / "env").write_text("FASTINO_API_KEY=canonical\n")
    (home / ".config" / "dev-decisions" / "env").write_text("FASTINO_API_KEY=legacy\n")
    monkeypatch.setattr(Path, "home", lambda: home)
    assert env_file_path() == home / ".config" / "sys1" / "env"
    load_provider_env()
    import os
    assert os.environ["FASTINO_API_KEY"] == "canonical"


def test_legacy_fallback_used_when_no_canonical(clean_key_env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "dev-decisions").mkdir(parents=True)
    (home / ".config" / "dev-decisions" / "env").write_text("TYPESAFE_API_KEY=legacy\n")
    monkeypatch.setattr(Path, "home", lambda: home)
    assert env_file_path() == home / ".config" / "dev-decisions" / "env"
    out = load_provider_env()
    assert out["loaded"] == ["TYPESAFE_API_KEY"]


def test_exported_value_wins_over_file(clean_key_env, tmp_path, monkeypatch):
    p = write_env(tmp_path, "FASTINO_API_KEY=from-file\n")
    monkeypatch.setenv("SYS1_ENV_FILE", str(p))
    monkeypatch.setenv("FASTINO_API_KEY", "from-caller")
    out = load_provider_env()
    assert out["loaded"] == []  # nothing loaded: name already present
    import os
    assert os.environ["FASTINO_API_KEY"] == "from-caller"


def test_tolerates_comments_export_and_garbage(clean_key_env, tmp_path, monkeypatch):
    p = write_env(tmp_path, "\n# a comment\n"
                            "  # indented comment\n"
                            "export DREX_API_KEY=ddrex\n"
                            "SUPERSONIC_API_KEY = 'quoted' \n"
                            "this line has no equals\n"
                            "1BAD-NAME=x\n"
                            "=emptyname\n"
                            "LIQUID_API_KEY=liq=value\n")
    monkeypatch.setenv("SYS1_ENV_FILE", str(p))
    out = load_provider_env()
    assert sorted(out["loaded"]) == ["DREX_API_KEY", "LIQUID_API_KEY", "SUPERSONIC_API_KEY"]
    import os
    assert os.environ["DREX_API_KEY"] == "ddrex"
    assert os.environ["SUPERSONIC_API_KEY"] == "quoted"
    assert os.environ["LIQUID_API_KEY"] == "liq=value"  # value keeps its `=`


def test_return_carries_names_never_values(clean_key_env, tmp_path, monkeypatch):
    secret = "sk-super-secret-value"
    monkeypatch.setenv("SYS1_ENV_FILE", str(write_env(tmp_path, f"FASTINO_API_KEY={secret}\n")))
    out = load_provider_env()
    assert secret not in __import__("json").dumps(out)
    assert out["loaded"] == ["FASTINO_API_KEY"]


def test_load_config_self_configures(clean_key_env, tmp_path, monkeypatch):
    """The actual contract: load_config() alone registers the hosted pool."""
    p = write_env(tmp_path, "FASTINO_API_KEY=k1\nTYPESAFE_API_KEY=k2\nDREX_API_KEY=k3\n")
    monkeypatch.setenv("SYS1_ENV_FILE", str(p))
    from sys1.providers import available_providers
    cfg = load_config()
    ids = {pid for pid, *_ in available_providers(cfg)} if isinstance(available_providers(cfg)[0], tuple) else set(available_providers(cfg))
    assert {"decide", "jev", "drex", "glide"} <= ids
