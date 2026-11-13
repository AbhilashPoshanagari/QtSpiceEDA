"""pytest setup for the v0.4 spikes: `pytest -q scratch/phase4`.

Not part of the normal `pytest -q` run (pyproject testpaths = ["tests"]).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
# Spike modules import each other as top-level modules (spawned children inherit this).
sys.path.insert(0, str(HERE))

from common import dss_available  # noqa: E402


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if dss_available():
        return
    skip = pytest.mark.skip(reason="dss-python not installed (pip install -e '.[grid]')")
    for item in items:
        if item.get_closest_marker("opendss"):
            item.add_marker(skip)
