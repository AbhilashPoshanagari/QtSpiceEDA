"""H1: ports of a schematic (port flags) or a netlist (ticked nodes)."""

from __future__ import annotations

import json
from pathlib import Path

from core.hierarchy.ports import (
    Port,
    name_problem,
    netlist_ports,
    ordered,
    schematic_ports,
)
from core.netlist.model import ERROR, WARNING
from core.schematic.connectivity import connect
from core.schematic.model import PortFlag, Schematic, Wire
from core.symbols.library import SymbolLibrary

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
LIB = SymbolLibrary()


def lookup(name: str):
    return LIB.lookup(name)[0]


def rc(*ports: PortFlag) -> Schematic:
    data = json.loads((FIXTURES / "schematics" / "rc" / "rc.json").read_text("utf-8"))
    s = Schematic.from_dict(data)[0]
    s.ports = list(ports)
    return s


def ports_of(s: Schematic):
    return schematic_ports(s, connect(s, lookup))


def test_port_flags_on_the_rc_nets_are_the_ports() -> None:
    found = ports_of(rc(PortFlag(64, 112, "IN", "in"), PortFlag(304, 112, "OUT", "out")))
    assert found.ok and found.diagnostics == []
    assert found.ports == [Port("IN", "IN", "in"), Port("OUT", "OUT", "out")]
    assert [(r.name, r.net, r.severity) for r in found.rows] == [
        ("IN", "IN", ""),
        ("OUT", "OUT", ""),
    ]


def test_connect_gives_the_net_of_each_port_flag() -> None:
    s = rc(PortFlag(64, 112, "a"), PortFlag(900, 900, "lonely"))
    nets = connect(s, lookup)
    assert nets.ports == ["IN", ""]  # the label IN comes first, so it names the net


def test_a_port_on_a_labelled_net_uses_the_net_as_its_node() -> None:
    found = ports_of(rc(PortFlag(64, 112, "vin", "in")))
    assert found.ports == [Port("vin", "IN", "in")]


def test_unconnected_ground_and_partless_ports_are_errors() -> None:
    s = rc(
        PortFlag(900, 900, "lonely"),
        PortFlag(64, 288, "gndport"),
        PortFlag(500, 0, "dangling"),
    )
    s.wires.append(Wire(500, 0, 600, 0))
    found = ports_of(s)
    assert not found.ok and found.ports == []
    messages = {r.name: (r.severity, r.message) for r in found.rows}
    assert messages["lonely"] == (ERROR, "not connected to anything")
    assert messages["gndport"][0] == ERROR and "ground" in messages["gndport"][1]
    assert messages["dangling"] == (ERROR, "no part is connected to its net")


def test_bad_names_are_errors() -> None:
    for name in ("0", "GND", "a(b)", "x=1"):
        assert name_problem(name), name
    assert name_problem("in+") == "" and name_problem("vout") == ""
    found = ports_of(rc(PortFlag(64, 112, "gnd")))
    assert not found.ok and "ground" in found.rows[0].message


def test_same_name_is_one_port_and_a_second_name_on_a_net_is_an_error() -> None:
    found = ports_of(
        rc(
            PortFlag(64, 112, "IN", "in"),
            PortFlag(64, 160, "IN", "out"),  # V1's top pin: the same net, same name
            PortFlag(144, 112, "other"),  # also on IN, another name
        )
    )
    assert found.ports == [Port("IN", "IN", "in")]
    severities = [(r.severity, r.message) for r in found.rows]
    assert severities[1][0] == WARNING and "in is used" in severities[1][1]
    assert severities[2] == (ERROR, "it is on the same net as port IN")
    assert found.diagnostics[-1].message == "port other: it is on the same net as port IN"


def test_ordered_puts_inputs_then_outputs_then_bidir() -> None:
    ports = [Port("b", "b", "bidir"), Port("o", "o", "out"), Port("i", "i", "in"), Port("j", "j")]
    assert [p.name for p in ordered(ports)] == ["i", "o", "b", "j"]


def test_netlist_ports_check_the_ticked_nodes() -> None:
    found = netlist_ports(["in", "out"], [("out", "out"), ("IN", "in")])
    assert found.ok
    assert found.ports == [Port("out", "out", "out"), Port("IN", "in", "in")]
    assert [p.name for p in found.ordered()] == ["IN", "out"]

    bad = netlist_ports(["in"], [("nope", "in"), ("in", "up"), ("in", "in"), ("in", "out")])
    assert [(r.severity, r.message) for r in bad.rows] == [
        (ERROR, "no element uses this node"),
        (ERROR, "direction must be in, out or bidir, not 'up'"),
        ("", ""),
        (ERROR, "ticked twice"),
    ]
    assert not netlist_ports(["in"], [("0", "in")]).ok
