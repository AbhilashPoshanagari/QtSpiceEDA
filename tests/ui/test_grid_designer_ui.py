"""D6 smoke test of the Grid Network Designer in the main window: the QML loads
without warnings, the Designer tab and menus exist, a click on a library item and one
on the canvas place a bus through dropItem, and Ctrl+Z (Edit > Undo) takes it back.
QML itself is not unit-tested (CLAUDE.md): every action is a tested controller slot
(tests/bridge/test_grid_diagram_controller.py); this only checks the wiring."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.__main__ import create_engine  # noqa: E402
from tests.ui.mouse import click, find_item, item_center  # noqa: E402


def test_designer_tab_place_and_undo(qapp: object, qtbot) -> None:
    engine = create_engine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
    window = engine.rootObjects()[0]
    designer = window.findChild(QObject, "gridDesigner")
    try:
        for name in ("newGridDesignMenu", "openDssAsDesignMenu", "saveDesignMenu",
                     "saveDesignAsMenu", "exportDssMenu", "runPowerFlowMenu",
                     "designUndoMenu", "designRedoMenu"):  # fmt: skip
            assert window.findChild(QObject, name) is not None, name
        window.findChild(QObject, "leftTabs").setProperty("currentIndex", 4)
        assert window.property("designerShown")
        assert designer.newDesign("ui", 50.0)
        QTest.qWait(50)
        editor = find_item(window, "gridDiagramEditor")
        editor.setProperty("zoom", 1.0)
        editor.setProperty("panX", 100.0)
        editor.setProperty("panY", 100.0)
        click(window, item_center(find_item(window, "libraryItem_bus")))
        assert editor.property("tool") == "place"
        canvas = find_item(window, "designerCanvas")
        # diagram point (3, 2) -> canvas pixels at zoom 1 (20 px per unit)
        p = canvas.mapToScene(QPointF(100 + 3 * 20, 100 + 2 * 20))
        click(window, QPoint(round(p.x()), round(p.y())))
        assert designer.property("busCount") == 1
        g = designer.design().diagram.bus_glyphs["b1"]
        assert (g.x, g.y) == (3.0, 2.0)
        assert designer.property("selection") == ["bus.b1"]
        assert window.findChild(QObject, "designUndoMenu").property("text") == "&Undo Add bus b1"
        QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(20)
        assert designer.property("busCount") == 0
        ours = [w for w in warnings if "/grid/" in w or "Main.qml" in w]
        assert ours == [], ours
    finally:
        window.setProperty("quitting", True)
        designer.shutdown()
        for name in ("Simulator", "DiagramController", "GridController"):
            engine.singletonInstance("QtSpice", name).shutdown()
        window.close()
        engine.deleteLater()


@pytest.mark.opendss
def test_results_overlay_and_feasibility_panel(qapp: object, qtbot) -> None:
    """D8 wiring: after a run of IEEE 13 as a design the overlay, its legend and the
    feasibility rows are shown; a Level 2 row selects its element; an edit hatches
    the overlay and shows the "Run again" banner."""
    from tests.core.grid.helpers import IEEE13

    engine = create_engine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
    window = engine.rootObjects()[0]
    designer = window.findChild(QObject, "gridDesigner")
    try:
        window.findChild(QObject, "leftTabs").setProperty("currentIndex", 4)
        assert find_item(window, "feasibilitySnapshotNote").property("visible")  # rule 27
        assert find_item(window, "feasibilityEmpty").property("visible")
        with qtbot.waitSignal(designer.designOpened, timeout=60_000):
            assert designer.openDssAsDesign(str(IEEE13))
        with qtbot.waitSignal(designer.resultReady, timeout=60_000):
            assert designer.runPowerFlow(), designer.property("statusText")
        QTest.qWait(50)
        editor = find_item(window, "gridDiagramEditor")  # the G6 view has an overlay too
        assert find_item(editor, "gridOverlayLayer").property("visible")
        assert find_item(editor, "gridOverlayLegend").property("visible")
        assert not find_item(editor, "gridOverlayStaleBanner").property("visible")
        assert find_item(editor, "gridOverlay_bus.671") is not None
        assert find_item(window, "feasibilitySnapshotNote").property("visible")
        row = find_item(window, "feasibilityL2_voltage_min")
        assert row is not None and row.property("visible")
        element = designer.report().check("voltage_min").worst_element
        click(window, item_center(row))
        QTest.qWait(20)
        assert designer.property("selection") == [element]
        # the toggle hides the overlay
        toggle = find_item(editor, "gridOverlayToggle")
        click(window, item_center(toggle))
        assert not find_item(editor, "gridOverlayLayer").property("visible")
        click(window, item_center(toggle))
        assert find_item(editor, "gridOverlayLayer").property("visible")
        # any edit: stale
        assert designer.moveGlyph("bus.671", 1.0, 1.0)
        QTest.qWait(20)
        assert designer.property("resultsStale")
        assert find_item(editor, "gridOverlayStaleBanner").property("visible")
        assert find_item(editor, "gridOverlayHatch").property("visible")
        assert find_item(window, "feasibilityStale").property("visible")
        ours = [w for w in warnings if "/grid/" in w or "Main.qml" in w]
        assert ours == [], ours
    finally:
        window.setProperty("quitting", True)
        designer.shutdown()
        for name in ("Simulator", "DiagramController", "GridController"):
            engine.singletonInstance("QtSpice", name).shutdown()
        window.close()
        engine.deleteLater()
