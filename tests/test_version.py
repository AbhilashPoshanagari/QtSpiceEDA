"""The version is written in two places; they must agree."""

from __future__ import annotations

import tomllib
from pathlib import Path

from app import __version__

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_app_version_matches_pyproject() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__ == "0.4.0"
