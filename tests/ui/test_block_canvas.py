"""BlockCanvas, Block, Port, Wire and PropertiesPanel driven with the mouse and keys."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, Qt

from tests.ui.mouse import (
    canvas_point,
    click,
    drag,
    find_item,
    find_items,
    item_center,
    key,
    wheel,
)
from tests.ui.qml_harness import qml_scene

SCENE = """
RowLayout {
    anchors.fill: parent
    spacing: 0
    BlockLibrary { Layout.preferredWidth: 260; Layout.fillHeight: true }
    BlockCanvas { objectName: "canvas"; Layout.fillWidth: true; Layout.fillHeight: true }
    PropertiesPanel { Layout.preferredWidth: 220; Layout.fillHeight: true }
}
"""


class Scene:
    def __init__(self, engine: QObject, window: QObject) -> None:
        self.window = window
        self.ctl = engine.singletonInstance("QtSpice", "DiagramController")
        self.canvas = find_item(window, "canvas")

    def at(self, x: float, y: float) -> QPoint:
        return canvas_point(self.canvas, x, y)

    def row(self, block_id: str) -> dict:
        return next(r for r in self.ctl.blocks.rows() if r["blockId"] == block_id)

    def port(self, address: str, output: bool) -> QPoint:
        block_id, name = address.split(".")
        row = self.row(block_id)
        port = next(p for p in row["outputs" if output else "inputs"] if p["name"] == name)
        return self.at(row["blockX"] + port["x"], row["blockY"] + port["y"])

    def block_center(self, block_id: str) -> QPoint:
        row = self.row(block_id)
        return self.at(
            row["blockX"] + row["blockWidth"] / 2, row["blockY"] + row["blockHeight"] / 2
        )

    def block_item(self, block_id: str):
        return find_item(self.canvas, "block_" + block_id)


@pytest.fixture
def scene(qapp: object, tmp_path: Path, qtbot) -> Iterator[Scene]:
    with qml_scene(tmp_path, SCENE, width=1200, height=700) as (engine, window):
        window.requestActivate()
        qtbot.waitUntil(lambda: window.isActive(), timeout=2000)
        qtbot.wait(50)
        yield Scene(engine, window)


def test_drop_block_from_library(scene: Scene, qtbot) -> None:
    tile = next(
        t for t in find_items(scene.window, "blockTile") if t.property("blockName") == "Gain"
    )
    drag(scene.window, item_center(tile), scene.at(200, 160), steps=20)
    qtbot.waitUntil(lambda: scene.ctl.blocks.rowCount() == 1, timeout=2000)
    row = scene.ctl.blocks.rows()[0]
    assert row["specName"] == "Gain"
    assert row["blockX"] % 20 == 0 and row["blockY"] % 20 == 0
    assert abs(row["blockX"] - 170) <= 20 and abs(row["blockY"] - 130) <= 20
    qtbot.waitUntil(lambda: scene.block_item("Gain1") is not None)


def test_drag_block_snaps_and_keys_undo_redo(scene: Scene, qtbot) -> None:
    scene.ctl.addBlock("Gain", 100, 100)
    qtbot.wait(20)
    start = scene.block_center("Gain1")
    drag(scene.window, start, start + QPoint(47, 23))
    assert (scene.row("Gain1")["blockX"], scene.row("Gain1")["blockY"]) == (140, 120)
    assert scene.ctl.property("undoText") == "move Gain1"
    item = scene.block_item("Gain1")
    assert (item.x(), item.y()) == (140, 120)
    key(scene.window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert (scene.row("Gain1")["blockX"], scene.row("Gain1")["blockY"]) == (100, 100)
    key(scene.window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert (scene.row("Gain1")["blockX"], scene.row("Gain1")["blockY"]) == (140, 120)


def test_wire_from_output_to_input(scene: Scene, qtbot) -> None:
    scene.ctl.addBlock("Sine", 0, 0)
    scene.ctl.addBlock("Scope", 240, 100)
    qtbot.wait(20)
    drag(scene.window, scene.port("Sine1.y", True), scene.port("Scope1.u", False))
    assert [(w.src, w.dst) for w in scene.ctl.diagram.wires] == [("Sine1.y", "Scope1.u")]
    qtbot.waitUntil(lambda: find_items(scene.canvas, "wire_Sine1.y_Scope1.u") != [])
    # The wire follows its block.
    start = scene.block_center("Sine1")
    drag(scene.window, start, start + QPoint(0, 60))
    assert scene.ctl.wires.rows()[0]["y1"] == scene.row("Sine1")["blockY"] + 30
    # Undo the move, then the wire.
    scene.ctl.undo()
    scene.ctl.undo()
    assert scene.ctl.diagram.wires == []


def test_invalid_wire_shows_hint_and_is_rejected(scene: Scene, qtbot) -> None:
    scene.ctl.addBlock("Sine", 0, 0)
    scene.ctl.addBlock("Constant", 0, 160)
    scene.ctl.addBlock("Scope", 240, 80)
    scene.ctl.connectPorts("Sine1.y", "Scope1.u")
    qtbot.wait(20)
    seen: dict[str, object] = {}

    def look() -> None:
        hint = find_item(scene.canvas, "wireHint")
        seen["visible"] = hint.isVisible()
        seen["text"] = hint.property("text")
        seen["target"] = scene.canvas.property("wireTarget")

    drag(
        scene.window,
        scene.port("Constant1.y", True),
        scene.port("Scope1.u", False),
        before_release=look,
    )
    assert seen["target"] == "Scope1.u"
    assert seen["visible"] is True
    assert "already wired from Sine1.y" in str(seen["text"])
    assert len(scene.ctl.diagram.wires) == 1
    status = find_item(scene.canvas, "canvasStatus")
    assert "already wired" in status.property("text")


def test_rubber_band_select_delete_and_undo(scene: Scene, qtbot) -> None:
    for i, name in enumerate(("Sine", "Gain", "Scope")):
        scene.ctl.addBlock(name, 40 + 160 * i, 60)
    scene.ctl.connectPorts("Sine1.y", "Gain1.u")
    scene.ctl.connectPorts("Gain1.y", "Scope1.u")
    qtbot.wait(20)
    drag(scene.window, scene.at(20, 20), scene.at(300, 200))
    assert [r["selected"] for r in scene.ctl.blocks.rows()] == [True, True, False]
    key(scene.window, Qt.Key.Key_Delete)
    assert [r["blockId"] for r in scene.ctl.blocks.rows()] == ["Scope1"]
    assert scene.ctl.diagram.wires == []
    key(scene.window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(scene.ctl.blocks.rows()) == 3
    assert len(scene.ctl.diagram.wires) == 2


def test_click_selects_block_and_wire(scene: Scene, qtbot) -> None:
    scene.ctl.addBlock("Sine", 0, 0)
    scene.ctl.addBlock("Scope", 300, 200)
    scene.ctl.connectPorts("Sine1.y", "Scope1.u")
    qtbot.wait(20)
    click(scene.window, scene.block_center("Sine1"))
    assert scene.row("Sine1")["selected"] is True
    title = find_item(scene.window, "propertiesTitle")
    assert title.property("text").startswith("Sine1")
    w = scene.ctl.wires.rows()[0]
    click(scene.window, scene.at((w["x1"] + w["x2"]) / 2, (w["y1"] + w["y2"]) / 2))
    assert scene.ctl.wires.rows()[0]["selected"] is True
    assert scene.row("Sine1")["selected"] is False
    click(scene.window, scene.at(600, 20))  # empty space
    assert scene.ctl.wires.rows()[0]["selected"] is False


def test_wheel_zoom_keeps_point_under_cursor_and_middle_drag_pans(scene: Scene) -> None:
    canvas = scene.canvas
    anchor = scene.at(100, 100)
    wheel(scene.window, anchor, 240)
    assert canvas.property("zoom") > 1.3
    assert (scene.at(100, 100) - anchor).manhattanLength() <= 1
    wheel(scene.window, anchor, -2000)
    assert canvas.property("zoom") == pytest.approx(0.2)
    pan = (canvas.property("panX"), canvas.property("panY"))
    drag(
        scene.window,
        scene.at(300, 300),
        scene.at(300, 300) + QPoint(50, -30),
        Qt.MouseButton.MiddleButton,
    )
    assert canvas.property("panX") == pytest.approx(pan[0] + 50)
    assert canvas.property("panY") == pytest.approx(pan[1] - 30)


def test_properties_panel_edits_setting(scene: Scene, qtbot) -> None:
    scene.ctl.addBlock("Gain", 0, 0)
    qtbot.wait(20)
    click(scene.window, scene.block_center("Gain1"))
    field = find_item(scene.window, "setting_k")
    assert field.property("text") == "1.0"
    field.setProperty("text", "2.5")
    field.editingFinished.emit()
    assert scene.ctl.diagram.effective_settings("Gain1")["k"] == 2.5
    qtbot.waitUntil(lambda: find_item(scene.window, "setting_k").property("text") == "2.5")
    field = find_item(scene.window, "setting_k")
    field.setProperty("text", "lots")
    field.editingFinished.emit()
    hint = find_item(scene.window, "propertiesHint")
    assert hint.isVisible() and "not a number" in hint.property("text")
    assert scene.ctl.diagram.effective_settings("Gain1")["k"] == 2.5
    scene.ctl.undo()
    qtbot.waitUntil(lambda: find_item(scene.window, "setting_k").property("text") == "1.0")


def test_algebraic_loop_is_highlighted(scene: Scene, qtbot) -> None:
    scene.ctl.addBlock("Gain", 0, 0)
    scene.ctl.addBlock("Gain", 200, 0)
    scene.ctl.addBlock("Scope", 400, 0)
    scene.ctl.connectPorts("Gain1.y", "Gain2.u")
    scene.ctl.connectPorts("Gain2.y", "Gain1.u")
    scene.ctl.connectPorts("Gain2.y", "Scope1.u")
    qtbot.wait(20)
    click(scene.window, item_center(find_item(scene.canvas, "diagramRunButton")))
    for bid in ("Gain1", "Gain2"):
        assert scene.block_item(bid).property("inLoop") is True
        assert scene.block_item(bid).property("outline").name() == "#d73a49"
    assert scene.block_item("Scope1").property("inLoop") is False
    status = find_item(scene.canvas, "canvasStatus")
    assert "algebraic loop" in status.property("text")


def test_200_blocks_and_300_wires_stay_smooth(scene: Scene, qtbot) -> None:
    ctl = scene.ctl
    t0 = time.perf_counter()
    # 50 Constants and 150 Sums: 300 inputs, each fed by an earlier block.
    for i in range(50):
        ctl.addBlock("Constant", 0, i * 80)
    for i in range(150):
        ctl.addBlock("Sum", 120 + (i // 25) * 120, (i % 25) * 80)
    outputs = [f"Constant{i + 1}.y" for i in range(50)]
    for i in range(150):
        sid = f"Sum{i + 1}"
        assert ctl.connectPorts(outputs[(2 * i) % len(outputs)], f"{sid}.a")
        assert ctl.connectPorts(outputs[(2 * i + 1) % len(outputs)], f"{sid}.b")
        outputs.append(f"{sid}.y")
    scene.window.grabWindow()  # forces the delegates to be created and drawn
    build = time.perf_counter() - t0
    assert ctl.blocks.rowCount() == 200 and ctl.wires.rowCount() == 300

    # Drag one block that has five wires attached, rendering a frame per step.
    ctl.select("Constant1", False)
    frames = []
    for step in range(30):
        t = time.perf_counter()
        ctl.moveBlock("Constant1", 0, step * 20)
        scene.window.grabWindow()
        frames.append(time.perf_counter() - t)
    ctl.endMove()
    frames.sort()
    median = frames[len(frames) // 2]
    print(
        f"\nbuild 200 blocks + 300 wires: {build * 1000:.0f} ms; "
        f"drag frame median {median * 1000:.1f} ms, worst {frames[-1] * 1000:.1f} ms"
    )
    # Measured about 0.3 s and 9 ms here; the limits leave room for slow CI machines.
    assert build < 5.0
    assert median < 0.05


def test_placed_block_follows_function_edits(scene: Scene, qtbot) -> None:
    ctl = scene.ctl
    assert ctl.apply("@block\ndef mix(a, b):\n    return a + b\n")
    ctl.addBlock("Constant", 0, 0)
    ctl.addBlock("Constant", 0, 100)
    ctl.addBlock("mix", 200, 40)
    assert ctl.connectPorts("Constant1.y", "mix1.a")
    assert ctl.connectPorts("Constant2.y", "mix1.b")
    qtbot.wait(20)
    height = scene.block_item("mix1").height()

    assert ctl.apply("@block\ndef mix(a, b, c):\n    return a + b + c\n")
    qtbot.waitUntil(lambda: find_items(scene.canvas, "in_mix1.c") != [])
    assert scene.block_item("mix1").height() > height
    assert len(find_items(scene.canvas, "wire_Constant2.y_mix1.b")) == 1

    assert ctl.apply("@block\ndef mix(a):\n    return a\n")
    qtbot.waitUntil(lambda: find_items(scene.canvas, "wire_Constant2.y_mix1.b") == [])
    assert find_items(scene.canvas, "in_mix1.b") == []
    assert len(find_items(scene.canvas, "wire_Constant1.y_mix1.a")) == 1
    status = find_item(scene.canvas, "canvasStatus")
    assert "Canvas updated" in status.property("text")
    assert "Constant2.y→mix1.b" in status.property("text")
