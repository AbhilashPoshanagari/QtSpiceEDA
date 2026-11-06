"""H2: SymbolGenerator writes <name>.sub and <name>.asy from a schematic or netlist."""

from __future__ import annotations

import json
from pathlib import Path

from core.hierarchy.ports import Port
from core.hierarchy.symbolgen import (
    GRID,
    SUB_HEADER,
    SymbolGenerator,
    is_generated,
    symbol_name_problem,
)
from core.netlist.model import ERROR, WARNING
from core.netlist.parser import parse
from core.schematic.model import Instance, PortFlag, Schematic
from core.symbols.asy import AsyParser
from core.symbols.library import SymbolLibrary

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
LIB = SymbolLibrary()


def lookup(name: str):
    return LIB.lookup(name)[0]


def rc_sheet() -> Schematic:
    data = json.loads((FIXTURES / "schematics" / "rc" / "rc.json").read_text("utf-8"))
    s = Schematic.from_dict(data)[0]
    s.ports = [PortFlag(64, 112, "IN", "in"), PortFlag(304, 112, "OUT", "out")]
    return s


FIVE = """* five nodes
R1 a b 1k
R2 b c 1k
R3 c d 1k
R4 d e 1k
R5 e 0 1k
.tran 1u 1m
.end
"""


def test_schematic_symbol_reads_back_without_warnings(tmp_path: Path) -> None:
    made = SymbolGenerator.from_schematic("rclp", rc_sheet(), lookup)
    assert made.ok, made.diagnostics
    assert made.diagnostics == []
    assert made.ports == [Port("IN", "IN", "in"), Port("OUT", "OUT", "out")]
    assert made.write(tmp_path) == []
    symbol, diagnostics = AsyParser.parse(tmp_path / "rclp.asy")
    assert diagnostics == []
    assert symbol.type == "BLOCK"
    assert symbol.attr("Prefix") == "X"
    assert symbol.attr("SpiceModel") == "rclp" and symbol.attr("Value") == "rclp"
    assert symbol.attr("ModelFile") == "rclp.sub"
    assert is_generated(symbol)
    assert [(p.name, p.spice_order) for p in symbol.pins] == [("IN", 1), ("OUT", 2)]
    rect = symbol.rects[0]
    left, right = symbol.pins
    assert left.x < rect.x1 and right.x > rect.x2  # inputs left, outputs right
    assert all(v % GRID == 0 for p in symbol.pins for v in (p.x, p.y))


def test_schematic_sub_is_a_clean_subckt(tmp_path: Path) -> None:
    made = SymbolGenerator.from_schematic("rclp", rc_sheet(), lookup)
    lines = made.sub_text.splitlines()
    assert lines[0].startswith(f"{SUB_HEADER} rclp")
    assert ".subckt rclp IN OUT" in lines and lines[-1] == ".ends rclp"
    assert not any(line.startswith((".ac", ".end ", ".tran")) for line in lines)
    assert "AC RC filter" not in [line.strip() for line in lines]  # no title line
    result = parse(f"* use it\n{made.sub_text}X1 a b rclp\nR1 b 0 1k\n.end\n")
    assert [d for d in result.diagnostics if d.severity == ERROR] == []
    [sub] = result.circuit.subcircuits
    assert sub.ports == ["in", "out"] and len(sub.elements) == 3
    made.write(tmp_path)
    raw = (tmp_path / "rclp.sub").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert b"\r\n" in (tmp_path / "rclp.asy").read_bytes()


def test_pin_sides_and_order_follow_the_directions() -> None:
    chosen = [("c", "bidir"), ("b", "out"), ("a", "in"), ("d", "bidir"), ("e", "in")]
    made = SymbolGenerator.from_netlist("five", FIVE, chosen)
    assert made.ok, made.diagnostics
    assert [p.name for p in made.ports] == ["a", "e", "b", "c", "d"]
    assert ".subckt five a e b c d" in made.sub_text.splitlines()
    symbol, diagnostics = AsyParser.parse_text(made.asy_text)
    assert diagnostics == []
    rect = symbol.rects[0]
    sides = {}
    for pin in symbol.pins:
        if pin.x < rect.x1:
            sides[pin.name] = "left"
        elif pin.x > rect.x2:
            sides[pin.name] = "right"
        elif pin.y < rect.y1:
            sides[pin.name] = "top"
    assert sides == {"a": "left", "e": "left", "b": "right", "c": "top", "d": "top"}
    assert [p.name for p in symbol.pins] == ["a", "e", "b", "c", "d"]  # SpiceOrder
    assert all(v % GRID == 0 for p in symbol.pins for v in (p.x, p.y))
    assert {p.align for p in symbol.pins} == {"LEFT", "RIGHT", "TOP"}


def test_netlist_head_body_and_left_out_cards(tmp_path: Path) -> None:
    (tmp_path / "models.lib").write_text(".model dmod D(Is=1e-14)\n", "utf-8")
    text = """* clipper
.include models.lib
.subckt buf i o
R1 i o 1
.ends buf
.param rval=1k
R1 in mid {rval}
+ tc=0
X1 mid out buf
D1 out 0 dmod
.options reltol=1e-4
.ac dec 10 1 1k
.end
R9 after end 1
"""
    made = SymbolGenerator.from_netlist("clip", text, [("in", "in"), ("out", "out")], tmp_path)
    assert made.ok, made.diagnostics
    lines = made.sub_text.splitlines()
    start = lines.index(".subckt clip in out")
    head, body = lines[2:start], lines[start + 1 : -1]
    assert head == [".include models.lib", ".subckt buf i o", "R1 i o 1", ".ends buf"]
    assert body == [
        ".param rval=1k",
        "R1 in mid {rval}",
        "+ tc=0",
        "X1 mid out buf",
        "D1 out 0 dmod",
    ]
    [warning] = made.diagnostics
    assert warning.severity == WARNING and ".options" in warning.message


def test_refusals_are_errors_and_write_nothing(tmp_path: Path) -> None:
    def errors(made) -> list[str]:
        return [d.message for d in made.diagnostics if d.severity == ERROR]

    no_ports = rc_sheet()
    no_ports.ports = []
    made = SymbolGenerator.from_schematic("rclp", no_ports, lookup)
    assert not made.ok and "no ports" in errors(made)[-1]
    assert made.write(tmp_path) and not any(tmp_path.iterdir())

    assert not SymbolGenerator.from_schematic("bad name", rc_sheet(), lookup).ok
    assert "no element uses this node" in " ".join(
        errors(SymbolGenerator.from_netlist("five", FIVE, [("zz", "in")]))
    )
    assert "tick" in errors(SymbolGenerator.from_netlist("five", FIVE, []))[-1]
    broken = SymbolGenerator.from_netlist("x", "* t\nR1 a\n.end\n", [("a", "in")])
    assert not broken.ok and "netlist's errors" in errors(broken)[-1]

    bad_port = rc_sheet()
    bad_port.ports.append(PortFlag(900, 900, "lost"))
    assert "fix the ports first" in errors(SymbolGenerator.from_schematic("r", bad_port, lookup))


def test_one_level_only(tmp_path: Path) -> None:
    made = SymbolGenerator.from_schematic("rclp", rc_sheet(), lookup)
    made.write(tmp_path)
    child = AsyParser.parse(tmp_path / "rclp.asy")[0]

    def with_child(name: str):
        return child if name == "rclp" else lookup(name)

    parent = rc_sheet()
    parent.instances.append(Instance("rclp", "X1", 600, 600))
    again = SymbolGenerator.from_schematic("big", parent, with_child)
    assert not again.ok and "one level only" in again.diagnostics[-1].message

    text = f"* uses it\n.include {tmp_path / 'rclp.sub'}\nX1 a b rclp\nR1 b 0 1k\n.end\n"
    again = SymbolGenerator.from_netlist("big", text, [("a", "in")])
    assert not again.ok and "one level only" in again.diagnostics[-1].message


def test_symbol_names() -> None:
    assert symbol_name_problem("rc_lp-2") == ""
    for bad in ("", "2rc", "a b", "a.b", "con", "x" * 65, "µ"):
        assert symbol_name_problem(bad), bad


def test_a_body_without_ground_and_single_connection_ports_is_fine() -> None:
    # Ports connect outside the symbol, so a lone series resistor is a valid symbol even
    # though, as a whole netlist, it has no ground and two one-connection nodes.
    made = SymbolGenerator.from_netlist(
        "series", "* r\nR1 a b 1k\n.end\n", [("a", "in"), ("b", "out")]
    )
    assert made.ok, made.diagnostics
    sheet = Schematic(
        instances=[Instance("res", "R1", 0, 0, attrs={"Value": "1k"})],
        ports=[PortFlag(16, 0, "A", "in"), PortFlag(16, 96, "B", "out")],
    )
    made = SymbolGenerator.from_schematic("series2", sheet, lookup)
    assert made.ok, made.diagnostics
    assert ".subckt series2 A B" in made.sub_text.splitlines()
