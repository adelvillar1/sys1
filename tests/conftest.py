"""Shared fixtures.

The conftest pins the JSONL log dir to a tmp_path-per-test so logging
assertions don't fight each other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys1

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    """A config dict that logs to a tmp dir and points at non-existent venvs."""
    c = sys1.load_config()
    c["classify"]["log_dir"] = str(tmp_path / "logs")
    c["providers"]["local_venv"] = str(tmp_path / "missing-venv")
    return c


@pytest.fixture
def sample_diff() -> str:
    return (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+def new_helper():\n"
        "+    return 42\n"
    )


def load_fixture(name: str) -> dict:
    with open(FIXTURES / f"{name}.json") as f:
        return json.load(f)


@pytest.fixture
def decide_fixture() -> dict:
    return load_fixture("decide_sample")


@pytest.fixture
def jev_fixture() -> dict:
    return load_fixture("jev_sample")


@pytest.fixture
def local_fixture() -> dict:
    return load_fixture("local_sample")


@pytest.fixture
def modernbert_fixture() -> dict:
    return load_fixture("modernbert_sample")
