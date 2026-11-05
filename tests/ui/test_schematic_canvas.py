"""S5-S8: SchematicCanvas, SymbolItem, PartPicker and the tools, driven with mouse and keys."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from core.schematic.connectivity import connect
from core.schematic.model import Schematic, Wire, pin_points
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


# ---------------------------------------------------------------- S7


@pytest.fixture
def empty(qapp: object, tmp_path: Path, qtbot) -> Iterator[Scene]:
    with qml_scene(tmp_path, SCENE, 1000, 700) as (engine, window):
        s = Scene(engine, window, qtbot)
        s.ctl.load_schematic(None)
        s.canvas.setProperty("zoom", 1.5)
        s.canvas.setProperty("panX", 100)
        s.canvas.setProperty("panY", 100)
        qtbot.wait(20)
        yield s
        s.ctl.load_schematic(None)


def move_click(scene: Scene, x: float, y: float) -> None:
    p = scene.at(x, y)
    QTest.mouseMove(scene.window, p)
    scene.qtbot.wait(5)
    click(scene.window, p)


def test_wire_tool_draws_orthogonal_segments_and_esc_ends(empty: Scene) -> None:
    empty.canvas.startTool("wire")
    move_click(empty, 3, 2)  # snaps to (0, 0)
    QTest.mouseMove(empty.window, empty.at(60, 10))  # mostly sideways: horizontal first
    move_click(empty, 96, 64)
    assert empty.ctl.schematic().wires == [Wire(0, 0, 96, 0), Wire(96, 0, 96, 64)]
    assert find_item(empty.canvas, "rubberWire").property("visible") is True
    move_click(empty, 96, 128)  # straight on: one segment (the other is zero length)
    assert empty.ctl.schematic().wires[-1] == Wire(96, 64, 96, 128)
    key(empty.window, Qt.Key.Key_Escape)  # ends the wire, the tool stays
    assert empty.canvas.property("mode") == "wire"
    assert find_item(empty.canvas, "rubberWire").property("visible") is False
    key(empty.window, Qt.Key.Key_Escape)  # leaves the tool
    assert empty.canvas.property("mode") == "select"
    assert empty.ctl.undo_stack().count() == 2


def test_wire_ending_on_a_wire_stops_and_shows_a_junction(empty: Scene) -> None:
    empty.ctl.addWires([[0, 0, 160, 0]])
    empty.canvas.startTool("wire")
    move_click(empty, 80, 96)
    move_click(empty, 80, 0)  # on the middle of the first wire
    assert empty.canvas.findChild(QObject, "wireTool").property("active") is False
    dots = find_items(empty.canvas, "junctionDot")
    assert len(dots) == 1


def test_label_tool_asks_for_a_name_then_places(empty: Scene) -> None:
    empty.ctl.addWires([[0, 0, 160, 0]])
    find_item(empty.window, "labelButton").clicked.emit()
    empty.qtbot.wait(20)
    popup = empty.canvas.findChild(QObject, "labelPopup")
    assert popup.property("visible") is True
    empty.canvas.findChild(QObject, "labelField").setProperty("text", "vout")
    empty.canvas.findChild(QObject, "labelOk").clicked.emit()
    empty.qtbot.wait(20)
    assert empty.canvas.property("mode") == "label"
    move_click(empty, 82, 3)
    [label] = empty.ctl.schematic().labels
    assert (label.x, label.y, label.name) == (80, 0, "vout")


def test_ground_tool_places_on_each_click(empty: Scene) -> None:
    find_item(empty.window, "groundButton").clicked.emit()
    move_click(empty, 0, 0)
    move_click(empty, 64, 0)
    key(empty.window, Qt.Key.Key_Escape)
    assert [(g.x, g.y) for g in empty.ctl.schematic().grounds] == [(0, 0), (64, 0)]


def pins(scene: Scene, name: str) -> list[tuple[int, int]]:
    inst = scene.ctl.schematic().instance(name)
    return pin_points(inst, scene.ctl.lookup(inst.symbol))


def test_draw_the_rc_fixture_by_hand(empty: Scene) -> None:
    """Parts, wires, labels and ground drawn with the mouse give the fixture's nets.
    (Values are set with the attribute dialog, S9; here the defaults stay.)"""
    c = empty.canvas
    c.startPlace("voltage")
    move_click(empty, 0, 200)
    key(empty.window, Qt.Key.Key_Escape)
    c.startPlace("res")
    key(empty.window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)  # horizontal
    move_click(empty, 160, 64)
    key(empty.window, Qt.Key.Key_Escape)
    c.startPlace("cap")
    move_click(empty, 320, 200)
    key(empty.window, Qt.Key.Key_Escape)
    v_plus, v_minus = pins(empty, "V1")
    # After Ctrl+R (R90) pin A is on the right: wire V1 to the left pin, C1 to the right.
    r_left, r_right = sorted(pins(empty, "R1"))
    c_a, c_b = pins(empty, "C1")

    c.startTool("wire")
    move_click(empty, *v_plus)
    move_click(empty, *r_left)  # a pin ends the wire
    move_click(empty, *r_right)
    move_click(empty, *c_a)
    key(empty.window, Qt.Key.Key_Escape)

    c.startTool("ground")
    move_click(empty, *v_minus)
    move_click(empty, *c_b)
    c.startLabel("IN")
    move_click(empty, *v_plus)
    c.startLabel("OUT")
    move_click(empty, *c_a)
    key(empty.window, Qt.Key.Key_Escape)

    drawn = empty.ctl.schematic()
    nets = connect(drawn, empty.ctl.lookup)
    got = {inst.name: nets.pins[i] for i, inst in enumerate(drawn.instances)}
    fixture = rc()
    want_nets = connect(fixture, empty.ctl.lookup)
    want = {inst.name: want_nets.pins[i] for i, inst in enumerate(fixture.instances)}
    assert got["V1"] == want["V1"] == ["IN", "0"]
    assert got["C1"] == want["C1"] == ["OUT", "0"]
    assert sorted(got["R1"]) == sorted(want["R2"]) == ["IN", "OUT"]
    # Every step undoes back to an empty sheet and redoes to the drawing.
    steps = empty.ctl.undo_stack().count()
    assert steps == 3 + 2 + 2 + 2
    for _ in range(steps):
        empty.ctl.undo()
    assert empty.ctl.schematic() == Schematic()
    for _ in range(steps):
        empty.ctl.redo()
    assert empty.ctl.schematic() == drawn


# ---------------------------------------------------------------- S8


def test_mouse_drag_moves_a_part_and_its_wires_follow(scene: Scene) -> None:
    before = scene.ctl.schematic()
    drag(scene.window, scene.at(304, 176), scene.at(368, 176))  # C1
    after = scene.ctl.schematic()
    assert after.instances[1].x == before.instances[1].x + 64
    assert connect(after, scene.ctl.lookup).pins[1] == ["OUT", "0"]
    assert scene.ctl.undo_stack().count() == 1
    key(scene.window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert scene.ctl.schematic() == before
    key(scene.window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert scene.ctl.schematic() == after


def test_move_tool_detaches_wires(scene: Scene) -> None:
    click(scene.window, scene.at(304, 176))
    find_item(scene.window, "moveButton").clicked.emit()
    assert scene.canvas.property("mode") == "move"
    drag(scene.window, scene.at(304, 176), scene.at(368, 176))
    after = scene.ctl.schematic()
    assert after.wires == rc().wires
    assert scene.ctl.property("undoText") == "move"


def test_keys_rotate_mirror_and_delete_the_selection(scene: Scene) -> None:
    click(scene.window, scene.at(304, 176))  # C1
    key(scene.window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert scene.ctl.schematic().instances[1].rotation == 90
    key(scene.window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    assert scene.ctl.schematic().instances[1].mirror is True
    key(scene.window, Qt.Key.Key_Delete)
    assert [i.name for i in scene.ctl.schematic().instances] == ["V1", "R2"]
    for _ in range(3):
        key(scene.window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert scene.ctl.schematic() == rc()


def test_copy_paste_with_keys_and_a_click(scene: Scene) -> None:
    click(scene.window, scene.at(192, 112))  # R2
    key(scene.window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    QTest.mouseMove(scene.window, scene.at(192, 220))
    key(scene.window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert scene.canvas.property("mode") == "paste"
    move_click(scene, 192, 240)
    assert scene.canvas.property("mode") == "select"
    names = [i.name for i in scene.ctl.schematic().instances]
    assert names == ["V1", "C1", "R2", "R1"]
    assert scene.ctl.property("undoText") == "paste"
    # Esc while a copy floats leaves nothing.
    key(scene.window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    key(scene.window, Qt.Key.Key_Escape)
    assert [i.name for i in scene.ctl.schematic().instances] == names


def test_toolbar_undo_and_redo(scene: Scene) -> None:
    undo, redo = find_item(scene.window, "undoButton"), find_item(scene.window, "redoButton")
    assert undo.property("enabled") is False
    scene.ctl.addGround(400, 400)
    assert undo.property("enabled") is True
    undo.clicked.emit()
    assert scene.ctl.schematic() == rc() and redo.property("enabled") is True
    redo.clicked.emit()
    assert len(scene.ctl.schematic().grounds) == 3
