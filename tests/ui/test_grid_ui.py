"""G6 smoke tests of the grid UI: menu and tab, single-line view, results table."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, QPoint, QPointF, Qt  # noqa: E402  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import bridge.grid_controller as grid_controller  # noqa: E402
from app.__main__ import create_engine  # noqa: E402
from tests.core.grid.helpers import IEEE13, IEEE123  # noqa: E402
from tests.ui.mouse import find_item, find_items  # noqa: E402
from tests.ui.qml_harness import qml_window  # noqa: E402

RUN_TIMEOUT_MS = 60_000


def _grid(engine: QObject) -> QObject:
    return engine.singletonInstance("QtSpice", "GridController")


def _open(qtbot, grid: QObject, path: Path, run: bool) -> None:
    assert grid.openFile(str(path))
    qtbot.waitUntil(lambda: not grid.property("running"), timeout=RUN_TIMEOUT_MS)
    if run:
        assert grid.run()
        qtbot.waitUntil(lambda: not grid.property("running"), timeout=RUN_TIMEOUT_MS)
        assert grid.property("hasResult"), grid.property("consoleText")


def _close(engine: QObject, window: QObject) -> None:
    for name in ("Simulator", "DiagramController", "GridController"):
        engine.singletonInstance("QtSpice", name).shutdown()
    window.close()
    engine.deleteLater()


@pytest.mark.opendss
def test_main_window_grid_tab_runs_ieee13(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    grid = _grid(engine)
    try:
        assert window.findChild(QObject, "openGridMenu").property("height") > 0
        tab = window.findChild(QObject, "gridTab")
        assert tab.property("visible")
        assert window.findChild(QObject, "gridOpenDialog") is not None

        # File > Open Grid ends in openGrid(): it switches to the Grid tab
        QMetaObject.invokeMethod(window, "openGrid", Q_ARG("QVariant", str(IEEE13)))
        left = window.findChild(QObject, "leftTabs")
        assert left.property("currentIndex") == 3
        qtbot.waitUntil(lambda: not grid.property("running"), timeout=RUN_TIMEOUT_MS)
        run = window.findChild(QObject, "runButton")
        stop = window.findChild(QObject, "stopButton")
        assert run.property("enabled") and not stop.property("enabled")
        assert not window.findChild(QObject, "checkButton").property("enabled")

        QMetaObject.invokeMethod(run, "clicked")
        qtbot.waitUntil(lambda: grid.property("hasResult"), timeout=RUN_TIMEOUT_MS)
        status = window.findChild(QObject, "runStatus")
        assert status.property("text").startswith("Finished")
        # the voltage profile is shown in the Waveforms tab
        assert window.findChild(QObject, "bottomTabs").property("currentIndex") == 0
        waveforms = engine.singletonInstance("QtSpice", "WaveformController")
        assert waveforms.property("analysis") == "profile"
        assert find_item(window, "nodeTable") is not None
        console = window.findChild(QObject, "consoleText")
        assert "Losses:" in console.property("text")
    finally:
        _close(engine, window)


def test_main_window_without_dss_python(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grid_controller, "engine_available", lambda: False)
    engine = create_engine()
    window = engine.rootObjects()[0]
    try:
        assert not _grid(engine).property("available")
        menu = window.findChild(QObject, "openGridMenu")
        assert menu.property("height") == 0
        tab = window.findChild(QObject, "gridTab")
        assert not tab.property("visible") and tab.property("width") == 0
        # the other features are unaffected
        assert window.findChild(QObject, "runButton").property("enabled")
    finally:
        _close(engine, window)


def _bus_point(view: QObject, bus: str) -> QPoint:
    value = QMetaObject.invokeMethod(
        view, "busPoint", Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", bus)
    )
    assert value is not None, f"bus {bus} is not drawn"
    p = view.mapToScene(QPointF(value.x(), value.y()))
    return QPoint(round(p.x()), round(p.y()))


@pytest.mark.opendss
def test_click_bus_675_shows_its_phase_voltages(qapp: object, qtbot, tmp_path: Path) -> None:
    with qml_window(tmp_path, "GridView", 900, 600) as (engine, window, view):
        grid = _grid(engine)
        _open(qtbot, grid, IEEE13, run=True)
        qtbot.wait(50)
        assert find_item(window, "gridCanvas").property("visible")
        assert not find_item(window, "gridViewMessage").property("visible")
        panel = find_item(window, "busVoltagePanel")
        assert not panel.property("visible")

        QTest.mouseClick(
            window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            _bus_point(view, "675"),
        )
        qtbot.waitUntil(lambda: grid.property("selectedBus") == "675")
        assert panel.property("visible")
        expected = grid.busVoltages("675")
        lines = {p: find_item(window, f"busVoltage_{p}").property("text") for p in "abc"}
        for row in expected:
            text = lines[row["phase"]]
            assert row["kv"] in text and row["pu"] in text and row["angle"] in text

        # a click away from every bus clears the selection
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 3)
        )
        qtbot.waitUntil(lambda: grid.property("selectedBus") == "")


@pytest.mark.opendss
def test_feeder_without_coordinates_shows_a_message(qapp: object, qtbot, tmp_path: Path) -> None:
    with qml_window(tmp_path, "GridView") as (engine, window, _):
        _open(qtbot, _grid(engine), IEEE123, run=False)  # the master file has no BusCoords
        qtbot.wait(50)
        message = find_item(window, "gridViewMessage")
        assert message.property("visible")
        assert "no bus coordinates" in message.property("text")
        assert not find_item(window, "gridCanvas").property("visible")


@pytest.mark.opendss
def test_results_table_sorts_and_highlights(qapp: object, qtbot, tmp_path: Path) -> None:
    with qml_window(tmp_path, "GridResultsTable", 900, 500) as (engine, window, table):
        grid = _grid(engine)
        _open(qtbot, grid, IEEE13, run=True)
        nodes = grid.property("nodes")
        node_view = find_item(window, "nodeTable")
        tv = find_item(node_view, "table")
        qtbot.waitUntil(lambda: tv.property("rows") == nodes.rowCount())

        # click the "V (pu)" header (column 3: widths 90 + 60 + 90, then the middle)
        header = find_item(node_view, "header")
        p = header.mapToScene(QPointF(90 + 60 + 90 + 45, 13))
        QTest.mouseClick(
            window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(round(p.x()), round(p.y())),
        )
        qtbot.waitUntil(lambda: nodes.property("sortColumn") == 3)
        assert nodes.property("sortAscending")
        first = float(nodes.cell(0, 3))
        assert all(first <= float(nodes.cell(r, 3)) for r in range(1, nodes.rowCount()))

        # the violations tab lists them, every row highlighted
        table.setProperty("currentIndex", 2)
        violations = grid.property("violations")
        assert violations.rowCount() == grid.property("summary")["violationCount"] > 0
        rows = violations.rows()
        assert all(r.highlight for r in rows)
        assert len(find_items(window, "violationTable")) == 1
