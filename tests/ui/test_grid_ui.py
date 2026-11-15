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
from tests.ui.mouse import click, find_item, find_items, item_center  # noqa: E402
from tests.ui.qml_harness import qml_scene, qml_window  # noqa: E402

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

        # click the "V (pu)" header (column 3: widths 90 + 60 + 90, then the middle;
        # the columns are stretched by the same factor to fill the width)
        header = find_item(node_view, "header")
        stretch = node_view.property("stretch")
        assert stretch >= 1
        p = header.mapToScene(QPointF((90 + 60 + 90 + 45) * stretch, 13))
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


@pytest.mark.opendss
def test_single_line_toolbar_labels_and_hover(qapp: object, qtbot, tmp_path: Path) -> None:
    with qml_window(tmp_path, "GridView", 900, 600) as (engine, window, view):
        grid = _grid(engine)
        _open(qtbot, grid, IEEE13, run=True)
        qtbot.wait(50)
        buses = len(grid.property("geometry")["buses"])
        # every IEEE 13 label fits at this size
        qtbot.waitUntil(lambda: view.property("labelCount") == buses)

        # zoom buttons zoom about the middle; Fit goes back
        click(window, item_center(find_item(window, "gridZoomIn")))
        assert view.property("zoom") == pytest.approx(1.5)
        click(window, item_center(find_item(window, "gridZoomOut")))
        click(window, item_center(find_item(window, "gridZoomOut")))
        assert view.property("zoom") == pytest.approx(1 / 1.5)
        click(window, item_center(find_item(window, "gridFit")))
        assert view.property("zoom") == 1 and view.property("panX") == 0

        # small: labels that would overlap are left out, none is drawn twice
        window.setProperty("height", 180)
        window.setProperty("width", 300)
        qtbot.waitUntil(lambda: 0 < view.property("labelCount") < buses)
        window.setProperty("height", 600)
        window.setProperty("width", 900)
        qtbot.waitUntil(lambda: view.property("labelCount") == buses)

        click(window, item_center(find_item(window, "gridLabels")))
        qtbot.waitUntil(lambda: view.property("labelCount") == 0)
        assert not view.property("labelsOn")
        click(window, item_center(find_item(window, "gridLabels")))
        qtbot.waitUntil(lambda: view.property("labelCount") == buses)

        # hovering a bus shows its phase voltages in pu
        tip = find_item(window, "gridHoverTip")
        assert not tip.property("visible")
        QTest.mouseMove(window, _bus_point(view, "671"))
        qtbot.waitUntil(lambda: tip.property("visible"))
        text = find_item(window, "gridHoverText").property("text")
        for row in grid.busVoltages("671"):
            assert f"{row['phase']} {row['pu']}" in text
        QTest.mouseMove(window, QPoint(450, 590))  # away from every bus
        qtbot.waitUntil(lambda: not tip.property("visible"))


@pytest.mark.opendss
def test_saving_the_single_line_image(qapp: object, qtbot, tmp_path: Path) -> None:
    with qml_window(tmp_path, "GridView", 600, 400) as (engine, window, view):
        _open(qtbot, _grid(engine), IEEE13, run=False)
        qtbot.wait(50)
        target = tmp_path / "feeder view.png"
        QMetaObject.invokeMethod(view, "saveImage", Q_ARG("QVariant", Path(target).as_uri()))
        qtbot.waitUntil(target.exists, timeout=5000)
        assert target.stat().st_size > 1000
        qtbot.waitUntil(lambda: find_item(window, "gridToolbar").property("visible"))


@pytest.mark.opendss
def test_results_table_filter_per_bus_and_count(qapp: object, qtbot, tmp_path: Path) -> None:
    with qml_window(tmp_path, "GridResultsTable", 900, 500) as (engine, window, table):
        grid = _grid(engine)
        _open(qtbot, grid, IEEE13, run=True)
        nodes, buses = grid.property("nodes"), grid.property("buses")
        count = find_item(window, "gridRowCount")
        qtbot.waitUntil(lambda: count.property("text") == f"{nodes.rowCount()} rows")

        # the columns fill the width
        node_view = find_item(window, "nodeTable")
        assert node_view.property("stretch") > 1

        find_item(window, "gridFilter").setProperty("text", "67")
        for model in (nodes, buses, grid.property("branches"), grid.property("violations")):
            assert model.property("filterText") == "67"
        qtbot.waitUntil(lambda: count.property("text") == f"{nodes.rowCount()} of 41 rows")

        click(window, item_center(find_item(window, "gridPerBus")))
        assert table.property("perBus")
        qtbot.waitUntil(lambda: find_item(window, "busTable").property("visible"))
        assert not node_view.property("visible")
        qtbot.waitUntil(lambda: count.property("text") == "3 of 16 rows")

        click(window, item_center(find_item(window, "gridOnlyViolations")))
        assert nodes.property("onlyHighlighted") and buses.property("onlyHighlighted")
        assert all(r.highlight for r in buses.rows())

        # the per-bus box is only for the node tab; export follows the shown table
        table.setProperty("currentIndex", 1)
        assert not find_item(window, "gridPerBus").property("visible")


@pytest.mark.opendss
def test_summary_cards_open_violations_and_select_the_lowest_bus(
    qapp: object, qtbot, tmp_path: Path
) -> None:
    body = """
    RowLayout {
        anchors.fill: parent
        GridPanel {
            objectName: "panel"
            Layout.preferredWidth: 480
            Layout.fillHeight: true
            onViolationsRequested: results.currentIndex = 2
        }
        GridResultsTable {
            id: results
            objectName: "results"
            Layout.fillWidth: true
            Layout.fillHeight: true
        }
    }
    """
    with qml_scene(tmp_path, body, 1100, 600) as (engine, window):
        grid = _grid(engine)
        _open(qtbot, grid, IEEE13, run=True)
        cards = find_item(window, "gridTotals")
        qtbot.waitUntil(lambda: cards.property("visible"))
        summary = grid.property("summary")
        assert summary["violationCount"] > 0
        violations = find_item(window, "cardViolations")
        assert violations.property("alert")
        assert violations.property("value") == str(summary["violationCount"])
        assert summary["lossesPct"] in find_item(window, "gridLosses").property("detail")

        # the cards appear with the result and the panel lays out again: click once
        # the card has stopped moving
        last = [None]

        def settled() -> bool:
            now = (item_center(violations), violations.property("height"))
            stable, last[0] = now == last[0], now
            return stable and now[1] > 0

        qtbot.waitUntil(settled, timeout=5000)
        click(window, item_center(violations))
        qtbot.waitUntil(lambda: find_item(window, "results").property("currentIndex") == 2)

        click(window, item_center(find_item(window, "cardVoltage")))
        qtbot.waitUntil(lambda: grid.property("selectedBus") == summary["minPuBus"])
