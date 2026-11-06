"""S5-S8: SchematicCanvas, SymbolItem, PartPicker and the tools, driven with mouse and keys."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from core.schematic.connectivity import connect
from core.schematic.model import Instance, Schematic, TextItem, Wire, pin_points
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


# ---------------------------------------------------------------- 300 parts + 600 wires


def big_sheet(n: int = 300) -> Schematic:
    s = Schematic()
    for i in range(n):
        x, y = (i % 20) * 160, (i // 20) * 192
        s.instances.append(Instance("res", f"R{i + 1}", x, y, attrs={"Value": "1k"}))
        s.wires.append(Wire(x + 16, y, x + 16, y - 48))
        s.wires.append(Wire(x + 16, y + 96, x + 176, y + 96))
    return s


def test_big_sheet_pans_and_zooms_without_touching_the_parts(scene: Scene) -> None:
    """Pan and zoom only move/scale one item: no part is rebuilt, no model row changes.
    (Frame rates are measured on screen by scratch/schematic_demo/bench.py.)"""
    scene.ctl.load_schematic(big_sheet())
    scene.qtbot.wait(50)
    scene.fit()
    parts = scene.symbols()
    assert len(parts) == 300 and scene.ctl.wires.count == 600
    changed: list[int] = []
    scene.ctl.instances.dataChanged.connect(lambda *_: changed.append(1))
    start = time.perf_counter()
    for k in range(30):
        scene.canvas.zoomAt(500, 350, 1.1 if k < 15 else 1 / 1.1)
        scene.canvas.setProperty("panX", scene.canvas.property("panX") + 5)
        scene.qtbot.wait(1)
    elapsed = time.perf_counter() - start
    scene.qtbot.wait(150)  # line widths settle
    assert changed == []
    assert scene.symbols() == parts  # the very same delegate objects
    # Wall time offscreen varies a lot with machine load (0.5-10 s seen); this only
    # catches a catastrophic slowdown. The checks above are the real guard.
    assert elapsed < 20.0, f"30 pan/zoom steps took {elapsed:.2f} s"


def test_big_sheet_edits_stay_fast(scene: Scene) -> None:
    scene.ctl.load_schematic(big_sheet())

    def timed(fn) -> float:  # noqa: ANN001
        start = time.perf_counter()
        fn()
        return (time.perf_counter() - start) * 1e3

    assert timed(lambda: scene.ctl.addGround(5000, 5000)) < 250
    assert timed(scene.ctl.undo) < 250
    scene.ctl.select("instance", 150, False)
    scene.ctl.beginMove(0, 0, True)
    moves = [timed(lambda x=x: scene.ctl.moveTo(x, 0)) for x in range(16, 330, 16)]
    scene.ctl.endMove()
    assert sorted(moves)[len(moves) // 2] < 100, moves


# ---------------------------------------------------------------- S9


def popup_item(scene: Scene, name: str) -> QQuickItem:
    """An item inside a popup: popups live in the window overlay, not under the canvas."""
    return find_item(scene.window, name)


def double_click(scene: Scene, x: float, y: float) -> None:
    p = scene.at(x, y)
    QTest.mouseDClick(scene.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    scene.qtbot.wait(30)


def test_double_click_a_part_edits_its_attributes(scene: Scene) -> None:
    double_click(scene, 192, 112)  # R2
    dialog = scene.canvas.findChild(QObject, "attributeDialog")
    assert dialog.property("visible") is True
    value = popup_item(scene, "attr_Value")
    assert value.property("text") == "10k"
    value.setProperty("text", "2k")
    value.textEdited.emit()
    assert dialog.property("problem") == ""
    dialog.accept()
    scene.qtbot.wait(20)
    assert scene.ctl.schematic().instances[2].attrs["Value"] == "2k"
    assert "2k" in {t.property("text") for t in find_items(scene.canvas, "symbolText")}
    scene.ctl.undo()
    assert scene.ctl.schematic() == rc()


def test_attribute_dialog_blocks_ok_on_a_bad_value(scene: Scene) -> None:
    double_click(scene, 192, 112)
    dialog = scene.canvas.findChild(QObject, "attributeDialog")
    value = popup_item(scene, "attr_Value")
    value.setProperty("text", "1kz!")
    value.textEdited.emit()
    assert "bad number" in dialog.property("problem")
    assert scene.canvas.findChild(QObject, "attributeOk").property("enabled") is False
    assert scene.canvas.findChild(QObject, "attributeProblem").property("visible") is True
    dialog.reject()


def test_double_click_a_label_renames_it(scene: Scene) -> None:
    double_click(scene, 70, 112)  # the IN label
    assert scene.canvas.findChild(QObject, "labelPopup").property("visible") is True
    field = scene.canvas.findChild(QObject, "labelField")
    assert field.property("text") == "IN"
    field.setProperty("text", "VIN")
    scene.canvas.findChild(QObject, "labelOk").clicked.emit()
    assert scene.ctl.schematic().labels[0].name == "VIN"


def test_text_tool_places_a_directive_and_double_click_edits_it(scene: Scene) -> None:
    find_item(scene.window, "textButton").clicked.emit()
    scene.qtbot.wait(20)
    assert scene.canvas.findChild(QObject, "textPopup").property("visible") is True
    scene.canvas.findChild(QObject, "textArea").setProperty("text", ".tran 5m")
    scene.canvas.findChild(QObject, "textOk").clicked.emit()
    assert scene.canvas.property("mode") == "text"
    move_click(scene, 160, 300)
    assert scene.canvas.property("mode") == "select"
    assert scene.ctl.schematic().texts[-1] == TextItem(160, 304, ".tran 5m", True)
    double_click(scene, 175, 304)
    area = scene.canvas.findChild(QObject, "textArea")
    assert area.property("text") == ".tran 5m"
    area.setProperty("text", ".tran 10m")
    scene.canvas.findChild(QObject, "commentBox").setProperty("checked", True)
    scene.canvas.findChild(QObject, "textOk").clicked.emit()
    assert scene.ctl.schematic().texts[-1] == TextItem(160, 304, ".tran 10m", False)


# ---------------------------------------------------------------- S10


def test_letter_keys_place_parts_when_the_canvas_has_focus(empty: Scene) -> None:
    move_click(empty, 0, 0)  # focus the canvas
    for k, symbol in (
        (Qt.Key.Key_R, "res"),
        (Qt.Key.Key_C, "cap"),
        (Qt.Key.Key_L, "ind"),
        (Qt.Key.Key_D, "diode"),
    ):
        key(empty.window, k)
        assert empty.canvas.property("mode") == "place"
        assert empty.ctl.property("placing") == symbol
    key(empty.window, Qt.Key.Key_G)
    assert empty.canvas.property("mode") == "ground"
    key(empty.window, Qt.Key.Key_Escape)


def test_letter_keys_do_nothing_while_typing_elsewhere(picker: Scene) -> None:
    search = find_item(picker.window, "partSearch")
    search.forceActiveFocus()
    key(picker.window, Qt.Key.Key_R)
    assert picker.canvas.property("mode") == "select"
    assert search.property("text") == "r"


def test_function_keys_pick_the_tools(empty: Scene) -> None:
    for k, mode in (
        (Qt.Key.Key_F3, "wire"),
        (Qt.Key.Key_F5, "delete"),
        (Qt.Key.Key_F7, "move"),
        (Qt.Key.Key_F8, "drag"),
    ):
        key(empty.window, k)
        assert empty.canvas.property("mode") == mode
    key(empty.window, Qt.Key.Key_F4)
    assert empty.canvas.findChild(QObject, "labelPopup").property("visible") is True


def test_f2_asks_for_the_part_picker(empty: Scene) -> None:
    with empty.qtbot.waitSignal(empty.canvas.partPickerRequested):
        key(empty.window, Qt.Key.Key_F2)


def test_delete_tool_and_f9_undo_shift_f9_redo(scene: Scene) -> None:
    key(scene.window, Qt.Key.Key_F5)
    move_click(scene, 304, 176)  # C1
    assert [i.name for i in scene.ctl.schematic().instances] == ["V1", "R2"]
    key(scene.window, Qt.Key.Key_F9)
    assert scene.ctl.schematic() == rc()
    key(scene.window, Qt.Key.Key_F9, Qt.KeyboardModifier.ShiftModifier)
    assert len(scene.ctl.schematic().instances) == 2


def test_drag_tool_drags_with_wires_following(scene: Scene) -> None:
    key(scene.window, Qt.Key.Key_F8)
    drag(scene.window, scene.at(304, 176), scene.at(368, 176))
    assert connect(scene.ctl.schematic(), scene.ctl.lookup).pins[1] == ["OUT", "0"]
    assert scene.ctl.property("undoText") == "drag"


def test_help_panel_lists_the_shortcuts(scene: Scene) -> None:
    key(scene.window, Qt.Key.Key_F1)
    assert scene.canvas.findChild(QObject, "helpPanel").property("visible") is True
    keys = [i.property("text") for i in find_items(scene.window, "shortcutKey")]
    for wanted in ("R", "C", "L", "D", "G", "F2", "F3", "F4", "F5", "F7", "F8", "F9 / Ctrl+Z"):
        assert wanted in keys


def test_problems_show_under_the_sheet(scene: Scene) -> None:
    strip = find_item(scene.canvas, "problemList")
    assert strip.property("visible") is False
    scene.ctl._edit("cut", lambda s: s.wires.pop(4))
    scene.ctl.check()
    scene.qtbot.wait(20)
    assert strip.property("visible") is True
    texts = [i.property("text") for i in find_items(scene.canvas, "problemItem")]
    assert any("C1: pin B is not connected" in t for t in texts)


@pytest.mark.parametrize("width", [300, 500])
def test_picker_keeps_search_results_visible_in_a_short_pane(
    qapp: object, tmp_path: Path, qtbot, width: int
) -> None:
    """A short part picker (the Ports panel below it) still shows the matching parts."""
    body = f"PartPicker {{ width: {width}; height: 200 }}"
    with qml_scene(tmp_path, body, 600, 300) as (_engine, window):
        find_item(window, "partSearch").setProperty("text", "vol")
        qtbot.wait(30)
        part_list = find_item(window, "partList")
        assert part_list.property("height") >= 90
        rows = [i for i in find_items(window, "part_voltage") if i.property("visible")]
        assert rows and rows[0].property("y") + rows[0].property("height") <= part_list.property(
            "height"
        )
