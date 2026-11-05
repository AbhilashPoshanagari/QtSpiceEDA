from __future__ import annotations

import os

# UI tests run without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from core.sim.ngspice_adapter import NgspiceAdapter  # noqa: E402


def _ngspice_status() -> tuple[bool, str]:
    adapter = NgspiceAdapter()
    return adapter.available(), adapter.last_error


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    marked = [item for item in items if item.get_closest_marker("ngspice")]
    if not marked:
        return
    ok, error = _ngspice_status()
    if ok:
        return
    skip = pytest.mark.skip(reason=f"ngspice shared library not available: {error}")
    for item in marked:
        item.add_marker(skip)
