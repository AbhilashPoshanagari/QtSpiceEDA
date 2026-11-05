"""L8: LibraryBrowser.qml searches, shows pins and uses a part with a symbol."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSettings

from tests.ui.qml_harness import qml_window

LIB = Path(__file__).resolve().parents[1] / "fixtures" / "lib"


@pytest.fixture
def private_settings(tmp_path: Path) -> Iterator[None]:
    """The SchematicController singleton reads its search paths from QSettings."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "s"))
    yield
    QSettings.setDefaultFormat(QSettings.Format.NativeFormat)


def _child(root: QObject, name: str) -> QObject:
    obj = root.findChild(QObject, name)
    assert obj is not None, name
    return obj


def test_search_select_and_use_with_symbol(
    qapp: object, qtbot, tmp_path: Path, private_settings
) -> None:
    with qml_window(tmp_path, "LibraryBrowser") as (engine, _window, browser):
        ctrl = engine.singletonInstance("QtSpice", "SchematicController")
        assert browser.property("results") == []

        # Changing the search paths (the Settings) refreshes the list.
        ctrl.setProperty("libraryPaths", [str(LIB)])
        qtbot.waitUntil(lambda: len(browser.property("results")) > 0)
        assert "models" in _child(browser, "libraryStatus").property("text")

        _child(browser, "librarySearch").setProperty("text", "opamp")
        assert [r["name"] for r in browser.property("results")] == ["opamp5"]
        browser.setProperty("selectedIndex", 0)
        assert _child(browser, "selectedName").property("text") == "opamp5"
        qtbot.waitUntil(lambda: _child(browser, "pinList").property("count") == 5)
        assert _child(browser, "useWithSymbolButton").property("enabled")

        browser.openUseWithSymbol()
        dialog = _child(browser, "symbolDialog")
        qtbot.waitUntil(lambda: dialog.property("opened"))
        assert _child(browser, "symbolBox").property("currentText") == "opamp"
        dialog.accept()
        qtbot.waitUntil(
            lambda: "SpiceModel=opamp5" in _child(browser, "useResult").property("text")
        )
        assert ctrl.property("pendingPart")["attrs"]["SpiceModel"] == "opamp5"

        ctrl.setProperty("libraryPaths", [])
        qtbot.waitUntil(lambda: browser.property("results") == [])
        assert browser.property("selected") is None


def test_main_window_opens_the_library_browser(qapp: object, qtbot, private_settings) -> None:
    from app.__main__ import create_engine

    engine = create_engine()
    window = engine.rootObjects()[0]
    try:
        dialog = _child(window, "libraryWindow")
        _child(window, "libraryBrowserMenu").triggered.emit()
        qtbot.waitUntil(lambda: dialog.property("opened"))
        assert _child(window, "libraryBrowser") is not None
    finally:
        for name in ("Simulator", "DiagramController"):
            engine.singletonInstance("QtSpice", name).shutdown()
