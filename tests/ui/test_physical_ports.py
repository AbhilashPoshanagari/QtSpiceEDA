"""U1: conserving ports, physical wires and junction dots on the block canvas."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject

from bridge.diagram_controller import (
    ANY_COLOR,
    DiagramController,
    DiagramState,
    block_geometry,
    physical_route,
)
from core.physical.domains import DOMAINS, ELECTRICAL, ROTATIONAL
from core.physical.library import physical_registry
from tests.ui.mouse import canvas_point, click, drag, find_item
from tests.ui.qml_harness import qml_scene

ROT = DOMAINS[ROTATIONAL].color
ELE = DOMAINS[ELECTRICAL].color


@pytest.fixture
def ctl(qapp: object) -> DiagramController:
    return DiagramController()


def row(ctl: DiagramController, block_id: str) -> dict:
    return next(r for r in ctl.blocks.rows() if r["blockId"] == block_id)


def port(ctl: DiagramController, address: str) -> dict:
    block_id, name = address.split(".")
    return next(p for p in row(ctl, block_id)["conserving"] if p["name"] == name)


# ---- library and geometry


def test_library_lists_physical_parts_with_conserving_ports(ctl: DiagramController) -> None:
    library = {b["name"]: b for b in ctl.property("library")}
    assert set(physical_registry().names()) <= set(library)
    damper = library["RotationalDamper"]
    assert damper["physical"] is True
    assert damper["conserving"] == [
        {"name": "A", "domain": ROTATIONAL, "color": ROT},
        {"name": "B", "domain": ROTATIONAL, "color": ROT},
    ]
    assert library["Gain"]["conserving"] == [] and library["Gain"]["physical"] is False
    solver = library["SolverConfiguration"]["conserving"]
    assert solver == [{"name": "port", "domain": "any", "color": ANY_COLOR}]


def test_domain_colors_come_from_the_registry(ctl: DiagramController) -> None:
    colors = ctl.property("domainColors")
    assert colors == {**{d.name: d.color for d in DOMAINS.domains()}, "any": ANY_COLOR}


def test_signal_blocks_keep_their_v01_geometry() -> None:
    from core.blocks.library import builtin_specs

    for spec in builtin_specs():
        geo = block_geometry(spec)
        assert geo.conserving == []


def test_conserving_ports_split_left_and_right() -> None:
    reg = physical_registry()
    damper = block_geometry(reg.spec("RotationalDamper").block)  # type: ignore[union-attr]
    [a, b] = damper.conserving
    assert (a["name"], a["side"], a["x"]) == ("A", "left", 0.0)
    assert (b["name"], b["side"], b["x"]) == ("B", "right", damper.width)
    inertia = block_geometry(reg.spec("Inertia").block)  # type: ignore[union-attr]
    assert [p["side"] for p in inertia.conserving] == ["left"]
    # A controlled source: signal input first on the left, then port A below it.
    source = block_geometry(reg.spec("ControlledTorqueSource").block)  # type: ignore[union-attr]
    [tau] = source.inputs
    left = next(p for p in source.conserving if p["side"] == "left")
    assert tau["x"] == left["x"] == 0.0 and tau["y"] < left["y"]
    # A sensor: output w on the right above port B.
    sensor = block_geometry(reg.spec("AngularVelocitySensor").block)  # type: ignore[union-attr]
    [w] = sensor.outputs
    right = next(p for p in sensor.conserving if p["side"] == "right")
    assert w["x"] == right["x"] == sensor.width and w["y"] < right["y"]


def test_physical_route_is_right_angled() -> None:
    pts = physical_route(0, 0, "right", 100, 40, "left")
    assert pts[0] == {"x": 0, "y": 0} and pts[-1] == {"x": 100, "y": 40}
    for p, q in zip(pts, pts[1:], strict=False):
        assert p["x"] == q["x"] or p["y"] == q["y"]
    assert pts[1] == {"x": 10.0, "y": 0}  # leaves the port straight out
    assert pts[-2] == {"x": 90.0, "y": 40}


# ---- placing and wiring


def test_placed_physical_block_row_has_its_ports(ctl: DiagramController) -> None:
    bid = ctl.addBlock("Inertia", 100, 100)
    r = row(ctl, bid)
    assert r["inputs"] == [] and r["outputs"] == []
    [a] = r["conserving"]
    assert a["domain"] == ROTATIONAL and a["color"] == ROT


def test_physical_wire_connect_undo_redo(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    d = ctl.addBlock("RotationalDamper", 300, 100)
    assert ctl.canConnectPhysical(f"{j}.A", f"{d}.A") == ""
    assert ctl.connectPhysical(f"{d}.A", f"{j}.A") is True
    [w] = ctl.physicalWires.rows()
    assert {w["a"], w["b"]} == {f"{j}.A", f"{d}.A"}
    assert w["domain"] == ROTATIONAL and w["color"] == ROT
    assert len(ctl.diagram.physical_wires) == 1
    ctl.undo()
    assert ctl.physicalWires.rowCount() == 0 and ctl.diagram.physical_wires == []
    ctl.redo()
    assert ctl.physicalWires.rowCount() == 1
    # Undirected: the same two ports the other way round are the same wire.
    assert "already wired" in ctl.canConnectPhysical(f"{j}.A", f"{d}.A")


def test_refused_physical_wire_gives_the_reason(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    r = ctl.addBlock("Resistor", 300, 100)
    g = ctl.addBlock("Gain", 300, 300)
    reason = ctl.canConnectPhysical(f"{j}.A", f"{r}.p")
    assert "ports of different domains never connect" in reason
    assert ctl.connectPhysical(f"{j}.A", f"{r}.p") is False
    assert ctl.property("hint") == reason
    assert "signal port" in ctl.canConnectPhysical(f"{j}.A", f"{g}.u")
    assert ctl.physicalWires.rowCount() == 0
    # A signal wire never touches a conserving port either.
    assert "physical" in ctl.canConnect(f"{g}.y", f"{j}.A")


def test_solver_port_takes_the_domain_it_joins(ctl: DiagramController) -> None:
    s = ctl.addBlock("SolverConfiguration", 100, 100)
    ref = ctl.addBlock("MechanicalRotationalReference", 300, 100)
    assert ctl.connectPhysical(f"{s}.port", f"{ref}.R")
    [w] = ctl.physicalWires.rows()
    assert w["domain"] == ROTATIONAL and w["color"] == ROT


def test_wire_follows_a_moved_block(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    d = ctl.addBlock("RotationalDamper", 300, 100)
    ctl.connectPhysical(f"{j}.A", f"{d}.A")
    ctl.moveBlock(d, 300, 200)
    ctl.endMove()
    pts = ctl.physicalWires.rows()[0]["points"]
    ends = {(pts[0]["x"], pts[0]["y"]), (pts[-1]["x"], pts[-1]["y"])}
    r = row(ctl, d)
    pa = port(ctl, f"{d}.A")
    assert (r["blockX"] + pa["x"], r["blockY"] + pa["y"]) in ends


def test_junction_dot_on_a_port_with_two_wires(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    d = ctl.addBlock("RotationalDamper", 300, 100)
    sensor = ctl.addBlock("AngularVelocitySensor", 300, 300)
    ctl.connectPhysical(f"{j}.A", f"{d}.A")
    assert ctl.property("junctions") == []
    ctl.connectPhysical(f"{j}.A", f"{sensor}.A")
    [dot] = ctl.property("junctions")
    r, p = row(ctl, j), port(ctl, f"{j}.A")
    assert dot == {
        "port": f"{j}.A",
        "x": r["blockX"] + p["x"],
        "y": r["blockY"] + p["y"],
        "color": ROT,
    }
    ctl.undo()
    assert ctl.property("junctions") == []


def test_remove_block_takes_its_physical_wires_and_undo_restores_them(
    ctl: DiagramController,
) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    d = ctl.addBlock("RotationalDamper", 300, 100)
    ctl.connectPhysical(f"{j}.A", f"{d}.A")
    ctl.select(d, False)
    ctl.removeSelected()
    assert ctl.physicalWires.rowCount() == 0 and ctl.diagram.physical_wires == []
    ctl.undo()
    assert ctl.physicalWires.rowCount() == 1 and len(ctl.diagram.physical_wires) == 1


def test_select_and_delete_a_physical_wire(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    d = ctl.addBlock("RotationalDamper", 300, 100)
    ctl.connectPhysical(f"{j}.A", f"{d}.A")
    pts = ctl.physicalWires.rows()[0]["points"]
    mid_x, mid_y = pts[2]["x"], (pts[2]["y"] + pts[3]["y"]) / 2
    assert ctl.physicalWireAt(mid_x, mid_y, 4) == 0
    assert ctl.physicalWireAt(mid_x, mid_y + 300, 4) == -1
    ctl.selectPhysicalWire(0, False)
    assert ctl.physicalWires.rows()[0]["selected"] is True
    assert ctl.property("selectedBlock") == {}
    ctl.removeSelected()
    assert ctl.physicalWires.rowCount() == 0
    assert len(ctl.diagram.blocks) == 2
    ctl.undo()
    assert ctl.physicalWires.rowCount() == 1


def test_disconnect_physical_wire(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    d = ctl.addBlock("RotationalDamper", 300, 100)
    ctl.connectPhysical(f"{j}.A", f"{d}.A")
    assert ctl.disconnectPhysicalWire(0) is True
    assert ctl.physicalWires.rowCount() == 0
    ctl.undo()
    assert ctl.physicalWires.rowCount() == 1
    assert ctl.disconnectPhysicalWire(5) is False


def test_conserving_port_hit_test(ctl: DiagramController) -> None:
    d = ctl.addBlock("RotationalDamper", 300, 100)
    r, b = row(ctl, d), port(ctl, f"{d}.B")
    assert ctl.conservingPortAt(r["blockX"] + b["x"] + 3, r["blockY"] + b["y"]) == f"{d}.B"
    assert ctl.portAt(r["blockX"] + b["x"], r["blockY"] + b["y"]) == ""
    assert ctl.conservingPortAt(0, 0) == ""


def test_physical_wires_survive_export_and_load(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 100, 100)
    d = ctl.addBlock("RotationalDamper", 300, 100)
    ctl.connectPhysical(f"{j}.A", f"{d}.A")
    state = ctl.export_state()
    assert state.diagram["physical_wires"] == [{"a": f"{j}.A", "b": f"{d}.A"}]
    other = DiagramController()
    assert other.load_state(state) == []
    assert other.physicalWires.rowCount() == 1
    assert {b.spec_name for b in other.diagram.blocks} == {"Inertia", "RotationalDamper"}


def test_load_skips_a_bad_physical_wire(ctl: DiagramController) -> None:
    state = DiagramState(
        diagram={
            "blocks": [
                {"id": "J1", "spec": "Inertia", "x": 0, "y": 0, "settings": {}},
                {"id": "R1", "spec": "Resistor", "x": 200, "y": 0, "settings": {}},
            ],
            "wires": [],
            "physical_wires": [{"a": "J1.A", "b": "R1.p"}, {"a": "nonsense", "b": "J1.A"}],
        }
    )
    warnings = ctl.load_state(state)
    assert len(warnings) == 2
    assert ctl.physicalWires.rowCount() == 0


# ---- with the mouse

SCENE = """
BlockCanvas { objectName: "canvas"; anchors.fill: parent }
"""


class Scene:
    def __init__(self, engine: QObject, window: QObject) -> None:
        self.window = window
        self.ctl = engine.singletonInstance("QtSpice", "DiagramController")
        self.canvas = find_item(window, "canvas")

    def port(self, address: str):
        block_id, name = address.split(".")
        r = row(self.ctl, block_id)
        p = next(q for q in r["conserving"] if q["name"] == name)
        return canvas_point(self.canvas, r["blockX"] + p["x"], r["blockY"] + p["y"])


@pytest.fixture
def scene(qapp: object, tmp_path: Path, qtbot) -> Iterator[Scene]:
    with qml_scene(tmp_path, SCENE, width=1000, height=600) as (engine, window):
        window.requestActivate()
        qtbot.waitUntil(lambda: window.isActive(), timeout=2000)
        qtbot.wait(50)
        yield Scene(engine, window)


def test_conserving_ports_are_round_in_their_domain_colour(scene: Scene, qtbot) -> None:
    d = scene.ctl.addBlock("RotationalDamper", 200, 100)
    g = scene.ctl.addBlock("Gain", 200, 300)
    qtbot.wait(30)
    item = find_item(scene.canvas, f"phys_{d}.A")
    assert item.property("conserving") is True
    assert item.property("tint").name() == ROT
    signal = find_item(scene.canvas, f"in_{g}.u")
    assert signal.property("conserving") is False


def test_drag_draws_a_physical_wire_between_ports(scene: Scene, qtbot) -> None:
    j = scene.ctl.addBlock("Inertia", 100, 100)
    d = scene.ctl.addBlock("RotationalDamper", 300, 100)
    qtbot.wait(30)
    # From the damper's left port to the inertia: no direction needed.
    drag(scene.window, scene.port(f"{d}.A"), scene.port(f"{j}.A"), steps=15)
    qtbot.waitUntil(lambda: scene.ctl.physicalWires.rowCount() == 1, timeout=2000)
    qtbot.wait(30)
    [w] = scene.ctl.physicalWires.rows()
    assert find_item(scene.canvas, f"pwire_{w['a']}_{w['b']}") is not None


def test_refused_drop_shows_the_reason(scene: Scene, qtbot) -> None:
    j = scene.ctl.addBlock("Inertia", 100, 100)
    r = scene.ctl.addBlock("Resistor", 300, 100)
    qtbot.wait(30)
    seen: list[str] = []

    def peek() -> None:
        seen.append(find_item(scene.canvas, "wireHint").property("text"))

    drag(scene.window, scene.port(f"{j}.A"), scene.port(f"{r}.p"), steps=15, before_release=peek)
    qtbot.wait(30)
    assert scene.ctl.physicalWires.rowCount() == 0
    assert "different domains" in seen[0]
    status = find_item(scene.canvas, "canvasStatus").property("text")
    assert "different domains" in status


def test_click_selects_a_physical_wire(scene: Scene, qtbot) -> None:
    j = scene.ctl.addBlock("Inertia", 100, 100)
    d = scene.ctl.addBlock("RotationalDamper", 300, 200)
    scene.ctl.connectPhysical(f"{j}.A", f"{d}.A")
    qtbot.wait(30)
    pts = scene.ctl.physicalWires.rows()[0]["points"]
    mid = canvas_point(scene.canvas, pts[2]["x"], (pts[2]["y"] + pts[3]["y"]) / 2)
    click(scene.window, mid)
    qtbot.waitUntil(lambda: scene.ctl.physicalWires.rows()[0]["selected"], timeout=2000)
