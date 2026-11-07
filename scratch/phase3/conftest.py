"""pytest setup for the v0.3 spikes: `pytest -q scratch/phase3`.

Not part of the normal `pytest -q` run (pyproject testpaths = ["tests"]).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
# Spike modules import each other as top-level modules (spawned children inherit this).
sys.path.insert(0, str(HERE))

from ngspice_sync import load_library  # noqa: E402


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if load_library() is not None:
        return
    skip = pytest.mark.skip(reason="ngspice shared library not available")
    for item in items:
        if item.get_closest_marker("ngspice"):
            item.add_marker(skip)
