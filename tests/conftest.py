from __future__ import annotations

import os

# UI tests run without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402

from core.grid.engines.opendss.engine import OpenDSSEngine  # noqa: E402
from core.sim.ngspice_adapter import NgspiceAdapter  # noqa: E402

# Same style as the app; it must be set before any test loads QML.
QQuickStyle.setStyle("Fusion")


@pytest.fixture(autouse=True)
def _isolated_library_folder(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Tests never see the user's real Documents/QtSpice/library (N0): the default
    library folder is a folder that does not exist, unless a test sets its own."""
    missing = tmp_path_factory.getbasetemp() / "no-default-library"
    monkeypatch.setattr("bridge.schematic_controller.default_library_folder", lambda: missing)


def _ngspice_status() -> tuple[bool, str]:
    adapter = NgspiceAdapter()
    return adapter.available(), adapter.last_error


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _skip_opendss(items)
    marked = [item for item in items if item.get_closest_marker("ngspice")]
    if not marked:
        return
    ok, error = _ngspice_status()
    if ok:
        return
    skip = pytest.mark.skip(reason=f"ngspice shared library not available: {error}")
    for item in marked:
        item.add_marker(skip)


def _skip_opendss(items: list[pytest.Item]) -> None:
    marked = [item for item in items if item.get_closest_marker("opendss")]
    if not marked or OpenDSSEngine.available():
        return
    skip = pytest.mark.skip(reason='dss-python not installed (pip install -e ".[grid]")')
    for item in marked:
        item.add_marker(skip)
