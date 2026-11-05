"""S2: union-find connectivity, T-joins, labels, ground and net numbering."""

from __future__ import annotations

from pathlib import Path

from core.netlist.model import ERROR, WARNING
from core.schematic.connectivity import connect
from core.schematic.model import (
    GroundFlag,
    Instance,
    NetLabel,
    PortFlag,
    Schematic,
    Wire,
    pin_points,
)
from core.symbols.asy import AsyParser, Symbol
from core.symbols.library import BUILTIN_DIR, SymbolLibrary

ASY = Path(__file__).resolve().parents[2] / "fixtures" / "asy"
BUILTIN = SymbolLibrary(builtin_dir=BUILTIN_DIR)
OPAMP5, _ = AsyParser.parse(ASY / "opamp5.asy")


def lookup(name: str) -> Symbol | None:
    if name == "opamp5":
        return OPAMP5
    return BUILTIN.lookup(name)[0]


def test_t_join_merges_nets() -> None:
    s = Schematic(wires=[Wire(0, 0, 64, 0), Wire(32, 0, 32, 64)])
    nets = connect(s, lookup)
    assert nets.wires[0] == nets.wires[1] == "N001"
    assert nets.junctions == [(32, 0)]


def test_crossing_wires_without_a_junction_stay_separate() -> None:
    s = Schematic(wires=[Wire(0, 32, 64, 32), Wire(32, 0, 32, 64)])
    nets = connect(s, lookup)
    assert nets.wires == ["N001", "N002"]
    assert nets.junctions == []


def test_wire_ends_at_the_same_point_connect_without_a_dot() -> None:
    s = Schematic(wires=[Wire(0, 0, 32, 0), Wire(32, 0, 32, 32)])
    nets = connect(s, lookup)
    assert nets.wires == ["N001", "N001"]
    assert nets.junctions == []


def test_three_wire_ends_at_one_point_get_a_dot() -> None:
    s = Schematic(wires=[Wire(0, 0, 32, 0), Wire(32, 0, 32, 32), Wire(32, 0, 64, 0)])
    assert connect(s, lookup).junctions == [(32, 0)]


def test_diagonal_wire_t_join() -> None:
    s = Schematic(wires=[Wire(0, 0, 64, 64), Wire(32, 32, 96, 32)])
    nets = connect(s, lookup)
    assert nets.wires[0] == nets.wires[1]


def test_same_named_labels_merge() -> None:
    s = Schematic(
        wires=[Wire(0, 0, 32, 0), Wire(0, 64, 32, 64)],
        labels=[NetLabel(0, 0, "IN"), NetLabel(32, 64, "in")],
    )
    nets = connect(s, lookup)
    assert nets.wires == ["IN", "IN"]
    assert nets.diagnostics == []


def test_label_on_the_middle_of_a_wire_names_it() -> None:
    s = Schematic(wires=[Wire(0, 0, 64, 0)], labels=[NetLabel(32, 0, "out")])
    assert connect(s, lookup).wires == ["out"]


def test_port_flag_names_a_net_and_joins_a_same_named_label() -> None:
    s = Schematic(
        wires=[Wire(0, 0, 32, 0), Wire(0, 64, 32, 64)],
        labels=[NetLabel(0, 0, "a")],
        ports=[PortFlag(0, 64, "A", "in")],
    )
    assert connect(s, lookup).wires == ["a", "a"]


def test_ground_is_0_and_all_grounds_are_one_net() -> None:
    s = Schematic(
        wires=[Wire(0, 0, 32, 0), Wire(0, 64, 32, 64)],
        grounds=[GroundFlag(0, 0), GroundFlag(32, 64)],
    )
    assert connect(s, lookup).wires == ["0", "0"]


def test_ground_wins_over_a_label_with_a_warning() -> None:
    s = Schematic(
        wires=[Wire(0, 0, 32, 0)], labels=[NetLabel(32, 0, "gndx")], grounds=[GroundFlag(0, 0)]
    )
    nets = connect(s, lookup)
    assert nets.wires == ["0"]
    assert [d.severity for d in nets.diagnostics] == [WARNING]


def test_two_names_on_one_net_warn_and_the_first_wins() -> None:
    s = Schematic(
        wires=[Wire(0, 0, 32, 0)], labels=[NetLabel(0, 0, "first"), NetLabel(32, 0, "second")]
    )
    nets = connect(s, lookup)
    assert nets.wires == ["first"]
    assert "first and second" in nets.diagnostics[0].message


def test_pins_connect_to_wires_after_placement() -> None:
    # res pins at (16,0) and (16,96); placed at (0,0) R0 -> (16,0), (16,96).
    s = Schematic(
        instances=[Instance("res", "R1", 0, 0)],
        wires=[Wire(16, 0, 16, -32), Wire(16, 96, 64, 96)],
        labels=[NetLabel(16, -32, "top")],
        grounds=[GroundFlag(64, 96)],
    )
    nets = connect(s, lookup)
    assert nets.pins == [["top", "0"]]


def test_pin_on_a_wire_middle_connects_with_a_dot() -> None:
    s = Schematic(
        instances=[Instance("res", "R1", 0, 0)],
        wires=[Wire(0, 0, 32, 0)],
        labels=[NetLabel(0, 0, "a")],
    )
    nets = connect(s, lookup)
    assert nets.pins[0][0] == "a"
    assert nets.junctions == [(16, 0)]


def test_pins_touching_directly_connect() -> None:
    # R1 pin B at (16,96) is R2 pin A when R2 sits at (0,96).
    s = Schematic(instances=[Instance("res", "R1", 0, 0), Instance("res", "R2", 0, 96)])
    nets = connect(s, lookup)
    assert nets.pins[0][1] == nets.pins[1][0] != ""


def test_unconnected_pin_has_no_net() -> None:
    s = Schematic(
        instances=[Instance("res", "R1", 0, 0)],
        wires=[Wire(16, 0, 16, -32)],
    )
    assert connect(s, lookup).pins == [["N001", ""]]


def test_numbering_is_stable_instances_first_then_wires() -> None:
    s = Schematic(
        instances=[Instance("res", "R1", 0, 0), Instance("res", "R2", 64, 0)],
        wires=[Wire(200, 0, 232, 0), Wire(16, 96, 80, 96), Wire(16, 0, 16, -16)],
    )
    nets = connect(s, lookup)
    # R1.A N001, R1.B=R2.B N002, R2.A has nothing -> "", lone wire N003.
    assert nets.pins == [["N001", "N002"], ["", "N002"]]
    assert nets.wires == ["N003", "N002", "N001"]
    assert nets.nets == ["N001", "N002", "N003"]
    assert connect(s, lookup) == nets


def test_generated_numbers_skip_names_used_by_labels() -> None:
    s = Schematic(wires=[Wire(0, 0, 32, 0), Wire(0, 64, 32, 64)], labels=[NetLabel(0, 64, "N001")])
    assert connect(s, lookup).wires == ["N002", "N001"]


def test_missing_symbol_is_an_error_not_a_crash() -> None:
    s = Schematic(instances=[Instance("nosuch", "X1", 0, 0)])
    nets = connect(s, lookup)
    assert nets.pins == [[]]
    assert [d.severity for d in nets.diagnostics] == [ERROR]


def test_rotated_and_mirrored_opamp_keeps_the_right_pin_order() -> None:
    inst = Instance("opamp5", "U1", 320, 160, 90, True)
    points = pin_points(inst, OPAMP5)
    # Each pin gets a short wire to a label named after the pin; the labels are
    # listed in drawing order, not SpiceOrder, so only correct pin mapping passes.
    by_name = dict(zip([p.name for p in OPAMP5.pins], points, strict=True))
    drawing = ["OUT", "In+", "V+", "In-", "V-"]
    wires = [Wire(x, y, x + 1000, y + 1000) for x, y in (by_name[n] for n in drawing)]
    labels = [
        NetLabel(w.x2, w.y2, n.replace("+", "p").replace("-", "m"))
        for w, n in zip(wires, drawing, strict=True)
    ]
    s = Schematic(instances=[inst], wires=wires, labels=labels)
    nets = connect(s, lookup)
    assert nets.diagnostics == []
    assert nets.pins == [["Inp", "Inm", "Vp", "Vm", "OUT"]]
