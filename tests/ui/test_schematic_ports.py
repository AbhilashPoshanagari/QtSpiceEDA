"""H1: port flags (controller, canvas tool, ports panel) and the netlist ports dialog."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSettings, Qt
from PySide6.QtTest import QTest

from bridge.schematic_controller import SchematicController
from core.schematic.model import PortFlag
from tests.ui.mouse import click, find_item, find_items, item_center, key
from tests.ui.qml_harness import qml_scene
from tests.ui.test_schematic_canvas import Scene, double_click, move_click, rc

RC_NETLIST = "* rc\nV1 in 0 AC 1\nR1 in out 1k\nC1 out 0 1u\n.ac dec 10 1 1k\n.end\n"


@pytest.fixture
def ctrl(qapp: object, tmp_path: Path) -> SchematicController:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    c = SchematicController(settings=settings)
    c.load_schematic(rc())
    return c


# ---------------------------------------------------------------- controller


def test_add_and_edit_ports_are_undoable(ctrl: SchematicController) -> None:
    assert ctrl.addPort(64, 112, "IN", "in") is True
    assert ctrl.undo_stack().count() == 1
    assert ctrl.schematic().ports == [PortFlag(64, 112, "IN", "in")]
    assert ctrl.setPort(0, "vin", "bidir") is True
    assert ctrl.schematic().ports == [PortFlag(64, 112, "vin", "bidir")]
    ctrl.undo()
    assert ctrl.schematic().ports == [PortFlag(64, 112, "IN", "in")]
    ctrl.undo()
    assert ctrl.schematic().ports == []
    # bad names and directions change nothing
    assert ctrl.addPort(0, 0, "two words", "in") is False
    assert ctrl.addPort(0, 0, "gnd", "in") is False
    assert ctrl.addPort(0, 0, "x", "sideways") is False
    assert ctrl.setPort(5, "x", "in") is False
    assert "ground" in ctrl.portNameProblem("0")
    assert ctrl.portNameProblem("vout") == ""


def test_ports_rows_list_the_flags_with_nets_and_problems(ctrl: SchematicController) -> None:
    ctrl.addPort(64, 112, "IN", "in")
    ctrl.addPort(304, 112, "OUT", "out")
    ctrl.addPort(900, 900, "lost", "bidir")
    rows = ctrl.property("ports")
    assert [(r["name"], r["direction"], r["net"], r["severity"]) for r in rows] == [
        ("IN", "in", "IN", ""),
        ("OUT", "out", "OUT", ""),
        ("lost", "bidir", "", "error"),
    ]
    assert rows[2]["message"] == "not connected to anything"
    ctrl.select("port", 1, False)
    assert [r["selected"] for r in ctrl.property("ports")] == [False, True, False]
    assert ctrl.portAt(1) == {"index": 1, "name": "OUT", "direction": "out", "x": 304, "y": 112}
    assert ctrl.portAt(7) == {}


def test_netlist_nodes_and_port_checks(ctrl: SchematicController) -> None:
    assert ctrl.netlistNodes(RC_NETLIST) == ["in", "out"]
    assert ctrl.checkNetlistPorts(RC_NETLIST, [{"node": "in", "direction": "in"}]) == []
    [problem] = ctrl.checkNetlistPorts(RC_NETLIST, [])
    assert problem["severity"] == "error" and "at least one" in problem["message"]
    [problem] = ctrl.checkNetlistPorts(RC_NETLIST, [{"node": "nope", "direction": "in"}])
    assert problem["message"] == "port nope: no element uses this node"


# ---------------------------------------------------------------- canvas and panel

SCENE = """
RowLayout {
    anchors.fill: parent
    SchematicCanvas {
        id: canvas
        objectName: "canvas"
        Layout.fillWidth: true
        Layout.fillHeight: true
    }
    PortsPanel {
        objectName: "panel"
        Layout.preferredWidth: 240
        Layout.fillHeight: true
        onEditPort: index => canvas.editPort(index)
    }
}
"""


@pytest.fixture
def scene(qapp: object, tmp_path: Path, qtbot) -> Iterator[Scene]:
    with qml_scene(tmp_path, SCENE, 1200, 700) as (engine, window):
        s = Scene(engine, window, qtbot)
        s.ctl.load_schematic(rc())
        qtbot.wait(20)
        s.fit()
        yield s
        s.ctl.load_schematic(None)


def test_port_tool_asks_name_and_direction_then_places(scene: Scene) -> None:
    find_item(scene.window, "portButton").clicked.emit()
    scene.qtbot.wait(20)
    assert scene.canvas.findChild(QObject, "portPopup").property("visible") is True
    field = scene.canvas.findChild(QObject, "portField")
    field.setProperty("text", "gnd")
    scene.qtbot.wait(10)
    ok = scene.canvas.findChild(QObject, "portOk")
    assert ok.property("enabled") is False
    assert "ground" in scene.canvas.findChild(QObject, "portProblem").property("text")
    field.setProperty("text", "vout")
    scene.canvas.findChild(QObject, "portDirection").setProperty("currentIndex", 1)  # out
    ok.clicked.emit()
    scene.qtbot.wait(20)
    assert scene.canvas.property("mode") == "port"
    move_click(scene, 302, 114)  # snaps to the OUT net point
    key(scene.window, Qt.Key.Key_Escape)
    assert scene.ctl.schematic().ports == [PortFlag(304, 112, "vout", "out")]
    assert scene.canvas.property("mode") == "select"


def test_panel_lists_ports_and_double_click_edits(scene: Scene) -> None:
    panel = find_item(scene.window, "panel")
    assert find_item(panel, "portsEmpty").isVisible()
    scene.ctl.addPort(64, 112, "IN", "in")
    scene.ctl.addPort(900, 900, "lost", "bidir")
    scene.qtbot.wait(20)
    row = find_item(panel, "portRow_IN")
    assert row is not None
    messages = [m.property("text") for m in find_items(panel, "portMessage") if m.isVisible()]
    assert messages == ["✖ not connected to anything"]
    click(scene.window, item_center(row))
    scene.qtbot.wait(20)
    assert scene.ctl.selection() == {("port", 0)}
    row = find_item(panel, "portRow_IN")  # the rows were rebuilt for the selection
    QTest.mouseDClick(
        scene.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, item_center(row)
    )
    scene.qtbot.wait(20)
    assert scene.canvas.findChild(QObject, "portPopup").property("visible") is True
    assert scene.canvas.findChild(QObject, "portField").property("text") == "IN"
    scene.canvas.findChild(QObject, "portDirection").setProperty("currentIndex", 2)
    scene.canvas.findChild(QObject, "portOk").clicked.emit()
    scene.qtbot.wait(20)
    assert scene.ctl.schematic().ports[0] == PortFlag(64, 112, "IN", "bidir")


def test_double_click_a_port_on_the_sheet_edits_it(scene: Scene) -> None:
    s = rc()
    s.labels = []  # a label on the same point would be hit first
    s.ports = [PortFlag(304, 112, "OUT", "out")]
    scene.ctl.load_schematic(s)
    scene.qtbot.wait(20)
    double_click(scene, 306, 112)
    assert scene.canvas.findChild(QObject, "portPopup").property("visible") is True
    assert scene.canvas.property("editPortIndex") == 0


# ---------------------------------------------------------------- netlist dialog

DIALOG = """
NetlistPortsDialog {
    objectName: "dlg"
    property string made: ""
    onMakeSymbol: (name, ports) => made = JSON.stringify({ name: name, ports: ports })
}
"""


def test_netlist_dialog_ticks_nodes_and_gives_directions(
    qapp: object, tmp_path: Path, qtbot
) -> None:
    with qml_scene(tmp_path, DIALOG, 800, 700) as (_engine, window):
        dialog = window.findChild(QObject, "dlg")
        dialog.openFor(RC_NETLIST)
        qtbot.wait(50)
        ok = find_item(window, "makeSymbolOk")
        assert ok.property("enabled") is False
        find_item(window, "symbolNameField").setProperty("text", "rclp")
        click(window, item_center(find_item(window, "portTick_out")))
        click(window, item_center(find_item(window, "portTick_in")))
        qtbot.wait(20)
        dialog.setDirection(1, "out")  # row 1 is "out"
        dialog.setDirection(0, "in")
        qtbot.wait(20)
        assert ok.property("enabled") is True
        ok.clicked.emit()
        qtbot.wait(20)
        made = json.loads(dialog.property("made"))
        assert made == {
            "name": "rclp",
            "ports": [{"node": "out", "direction": "out"}, {"node": "in", "direction": "in"}],
        }
        assert dialog.property("visible") is False
