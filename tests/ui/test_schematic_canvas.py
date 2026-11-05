"""S5-S8: SchematicCanvas, SymbolItem, PartPicker and the tools, driven with mouse and keys."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from core.schematic.model import Schematic
from tests.ui.mouse import click, drag, find_item, find_items, key, wheel
from tests.ui.qml_harness import qml_scene

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

SCENE = """
SchematicCanvas { objectName: "canvas"; anchors.fill: parent }
"""


def rc() -> Schematic:
    data = json.loads((FIXTURES / "schematics" / "rc" / "rc.json").read_text("utf-8"))
    return Schematic.from_dict(data)[0]


class Scene:
    def __init__(self, engine: QObject, window: QObject, qtbot) -> None:
        self.window = window
        self.qtbot = qtbot
        self.ctl = engine.singletonInstance("QtSpice", "SchematicController")
        self.canvas = find_item(window, "canvas")
        self.viewport = find_item(window, "canvasViewport")

    def at(self, x: float, y: float) -> QPoint:
        """Window position of sheet point (x, y)."""
        zoom = self.canvas.property("zoom")
        vx = self.canvas.property("panX") + x * zoom
        vy = self.canvas.property("panY") + y * zoom
        p = self.viewport.mapToScene(QPointF(vx, vy))
        return QPoint(round(p.x()), round(p.y()))

    def fit(self) -> None:
        self.canvas.zoomToFit()
        self.qtbot.wait(20)

    def symbols(self) -> list[QQuickItem]:
        return [i for i in find_items(self.canvas, "symbolItem") if i.property("instName")]


@pytest.fixture
def scene(qapp: object, tmp_path: Path, qtbot) -> Iterator[Scene]:
    with qml_scene(tmp_path, SCENE, 1000, 700) as (engine, window):
        s = Scene(engine, window, qtbot)
        s.ctl.load_schematic(rc())
        qtbot.wait(20)
        s.fit()
        yield s
        s.ctl.load_schematic(None)


# ---------------------------------------------------------------- S5


def test_draws_parts_flags_and_wires(scene: Scene) -> None:
    names = sorted(i.property("instName") for i in scene.symbols())
    assert names == ["C1", "R2", "V1"]
    texts = {t.property("text") for t in find_items(scene.canvas, "symbolText")}
    assert {"V1", "R2", "10k", "AC 1", ".1µ"} <= texts
    assert len(find_items(scene.canvas, "flagItem")) == 4
    assert find_item(scene.canvas, "wireShape") is not None
    assert [t.property("text") for t in find_items(scene.canvas, "sheetText")] == [
        ".ac dec 100 1 1MEG"
    ]


def test_zoom_to_fit_shows_everything(scene: Scene) -> None:
    x1, y1, x2, y2 = scene.ctl.contentBounds()
    top_left = scene.viewport.mapFromScene(QPointF(scene.at(x1, y1)))
    bottom_right = scene.viewport.mapFromScene(QPointF(scene.at(x2, y2)))
    assert top_left.x() >= 0 and top_left.y() >= 0
    assert bottom_right.x() <= scene.viewport.width()
    assert bottom_right.y() <= scene.viewport.height()
    assert scene.canvas.property("zoom") > 1  # the RC sheet is small


def test_wheel_zooms_around_the_pointer(scene: Scene) -> None:
    point = scene.at(192, 112)
    zoom = scene.canvas.property("zoom")
    wheel(scene.window, point, 120)
    assert scene.canvas.property("zoom") == pytest.approx(zoom * 1.25)
    after = scene.at(192, 112)
    assert abs(after.x() - point.x()) <= 1 and abs(after.y() - point.y()) <= 1


def test_middle_drag_pans(scene: Scene) -> None:
    pan = scene.canvas.property("panX"), scene.canvas.property("panY")
    drag(scene.window, QPoint(500, 400), QPoint(560, 380), Qt.MouseButton.MiddleButton)
    assert scene.canvas.property("panX") == pytest.approx(pan[0] + 60)
    assert scene.canvas.property("panY") == pytest.approx(pan[1] - 20)


def test_click_selects_and_ctrl_click_toggles(scene: Scene) -> None:
    click(scene.window, scene.at(192, 112))
    assert scene.ctl.selection() == {("instance", 2)}
    click(scene.window, scene.at(304, 176), modifiers=Qt.KeyboardModifier.ControlModifier)
    assert scene.ctl.selection() == {("instance", 2), ("instance", 1)}
    corner = scene.viewport.mapToScene(QPointF(5, scene.viewport.height() - 5))
    click(scene.window, QPoint(round(corner.x()), round(corner.y())))  # empty: clears
    assert scene.ctl.selection() == set()


def test_rubber_band_selects_what_is_inside(scene: Scene) -> None:
    drag(scene.window, scene.at(250, 90), scene.at(345, 300))
    selection = scene.ctl.selection()
    assert ("instance", 1) in selection  # C1
    assert ("instance", 0) not in selection  # V1 is outside
    assert find_item(scene.canvas, "rubberBand").property("visible") is False


# ---------------------------------------------------------------- S6

PICKER_SCENE = """
RowLayout {
    anchors.fill: parent
    spacing: 0
    PartPicker {
        Layout.preferredWidth: 220; Layout.fillHeight: true
        onPlace: name => canvas.startPlace(name)
    }
    SchematicCanvas {
        id: canvas; objectName: "canvas"; Layout.fillWidth: true; Layout.fillHeight: true
    }
}
"""


@pytest.fixture
def picker(qapp: object, tmp_path: Path, qtbot) -> Iterator[Scene]:
    with qml_scene(tmp_path, PICKER_SCENE, 1200, 700) as (engine, window):
        s = Scene(engine, window, qtbot)
        s.ctl.load_schematic(None)
        qtbot.wait(20)
        yield s
        s.ctl.load_schematic(None)


def test_picker_search_filters_and_previews(picker: Scene) -> None:
    search = find_item(picker.window, "partSearch")
    search.setProperty("text", "ca")
    picker.qtbot.wait(20)
    names = [i.property("text") for i in find_items(picker.window, "part_cap")]
    assert names == ["cap"]
    assert find_items(picker.window, "part_res") == []
    assert find_item(picker.window, "partPicker").property("current") == "cap"
    assert "Capacitor" in find_item(picker.window, "partDescription").property("text")
    preview = find_item(picker.window, "partPreview")
    assert [i.property("symbolName") for i in find_items(preview, "symbolItem")] == ["cap"]


def test_click_a_part_then_place_it_repeatedly_until_esc(picker: Scene) -> None:
    click(picker.window, item_point(find_item(picker.window, "part_res")))
    assert picker.canvas.property("mode") == "place"
    for x in (0, 160, 320):
        p = picker.at(x, 0)
        QTest.mouseMove(picker.window, p)
        click(picker.window, p)
    key(picker.window, Qt.Key.Key_Escape)
    assert picker.canvas.property("mode") == "select"
    assert [i.name for i in picker.ctl.schematic().instances] == ["R1", "R2", "R3"]
    assert picker.ctl.undo_stack().count() == 3


def test_right_click_also_stops_placing(picker: Scene) -> None:
    picker.canvas.startPlace("cap")
    assert picker.ctl.instances.count == 1
    QTest.mouseClick(
        picker.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, picker.at(0, 0)
    )
    picker.qtbot.wait(10)
    assert picker.canvas.property("mode") == "select" and picker.ctl.instances.count == 0


def item_point(item: QQuickItem) -> QPoint:
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(p.x()), round(p.y()))
