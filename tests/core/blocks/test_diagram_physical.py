"""F4: physical wires in the diagram."""

from __future__ import annotations

import json

import pytest

from core.blocks.diagram import Diagram, DiagramError, PhysicalWire
from core.blocks.library import builtin_specs
from core.blocks.spec import CONSERVING, BlockSpec, BlockSpecError, PortSpec
from core.physical.domains import ANY_DOMAIN, ELECTRICAL, ROTATIONAL, THERMAL


def _two_port(name: str, domain: str) -> BlockSpec:
    return BlockSpec(
        name=name,
        inputs=[],
        outputs=[],
        conserving=[PortSpec("A", CONSERVING, domain), PortSpec("B", CONSERVING, domain)],
    )


SPECS = {s.name: s for s in builtin_specs()}
SPECS["Damper"] = _two_port("Damper", ROTATIONAL)
SPECS["Res"] = _two_port("Res", ELECTRICAL)
SPECS["Heat"] = _two_port("Heat", THERMAL)
SPECS["Solver"] = BlockSpec(
    name="Solver", inputs=[], outputs=[], conserving=[PortSpec("port", CONSERVING, ANY_DOMAIN)]
)
SPECS["Sensor"] = BlockSpec(
    name="Sensor",
    inputs=["u"],
    outputs=["w"],
    conserving=[PortSpec("A", CONSERVING, ROTATIONAL)],
)


def new_diagram() -> Diagram:
    d = Diagram(SPECS.get)
    for spec in ("Damper", "Damper", "Res", "Heat", "Solver", "Sensor", "Gain"):
        d.add_block(spec)
    return d


def test_wire_is_undirected() -> None:
    assert PhysicalWire("Damper2", "A", "Damper1", "B") == PhysicalWire(
        "Damper1", "B", "Damper2", "A"
    )
    assert hash(PhysicalWire("x", "A", "y", "B")) == hash(PhysicalWire("y", "B", "x", "A"))
    d = new_diagram()
    w = d.add_physical_wire("Damper2", "A", "Damper1", "B")
    assert (w.a, w.b) == ("Damper1.B", "Damper2.A")
    assert d.physical_wires == [w]
    assert d.check_physical_wire("Damper1", "B", "Damper2", "A") == (
        "Damper1.B and Damper2.A are already wired"
    )


def test_a_port_takes_many_wires() -> None:
    d = new_diagram()
    d.add_physical_wire("Damper1", "A", "Damper2", "A")
    d.add_physical_wire("Damper1", "A", "Damper2", "B")
    d.add_physical_wire("Damper1", "A", "Sensor1", "A")
    assert len(d.physical_wires_at("Damper1", "A")) == 3
    assert len(d.physical_wires_at("Damper2", "A")) == 1


@pytest.mark.parametrize(
    ("a", "b", "reason"),
    [
        (
            ("Damper1", "A"),
            ("Res1", "A"),
            "cannot connect Damper1.A (rotational) to Res1.A (electrical): "
            "ports of different domains never connect",
        ),
        (("Damper1", "A"), ("Damper1", "A"), "cannot wire Damper1.A to itself"),
        (("Damper1", "A"), ("Sensor1", "w"), "Sensor1.w is a signal port"),
        (("Gain1", "u"), ("Damper1", "A"), "Gain1.u is a signal port"),
        (("Damper1", "C"), ("Damper2", "A"), "block 'Damper1' has no port 'C'"),
        (("Nope", "A"), ("Damper2", "A"), "no block with id 'Nope'"),
    ],
)
def test_refused_wires_give_a_reason(a: tuple, b: tuple, reason: str) -> None:
    d = new_diagram()
    assert d.check_physical_wire(*a, *b).startswith(reason)
    with pytest.raises(DiagramError, match="^" + reason.replace("(", r"\(").replace(")", r"\)")):
        d.add_physical_wire(*a, *b)
    assert d.physical_wires == []


def test_solver_port_joins_any_domain() -> None:
    d = new_diagram()
    assert d.check_physical_wire("Solver1", "port", "Heat1", "A") == ""
    assert d.check_physical_wire("Damper1", "B", "Solver1", "port") == ""


def test_any_domain_is_only_a_port_wildcard() -> None:
    assert PortSpec("p", CONSERVING, ANY_DOMAIN).domain == ANY_DOMAIN
    with pytest.raises(BlockSpecError, match="needs a domain"):
        PortSpec("p", CONSERVING, "hydraulic")


def test_signal_wires_refuse_conserving_ports_and_keep_v01_rules() -> None:
    d = new_diagram()
    with pytest.raises(DiagramError, match="physical .rotational. port: use a physical wire"):
        d.add_wire("Gain1", "y", "Damper1", "A")
    # A physical block's signal ports wire as in v0.1.
    w = d.add_wire("Sensor1", "w", "Gain1", "u")
    assert d.wire_into("Gain1", "u") == w
    with pytest.raises(DiagramError, match="already wired"):
        d.add_wire("Sensor1", "w", "Gain1", "u")


def test_remove_block_and_wire() -> None:
    d = new_diagram()
    w1 = d.add_physical_wire("Damper1", "A", "Damper2", "A")
    d.add_physical_wire("Damper2", "B", "Sensor1", "A")
    d.remove_block("Sensor1")
    assert d.physical_wires == [w1]
    d.remove_physical_wire(PhysicalWire("Damper2", "A", "Damper1", "A"))
    assert d.physical_wires == []
    with pytest.raises(DiagramError, match="no physical wire"):
        d.remove_physical_wire(w1)


def test_round_trip_and_v01_json_unchanged() -> None:
    d = new_diagram()
    assert "physical_wires" not in d.to_dict()
    d.add_physical_wire("Damper1", "A", "Damper2", "B")
    d.add_physical_wire("Solver1", "port", "Damper2", "B")
    data = json.loads(json.dumps(d.to_dict()))
    assert data["physical_wires"] == [
        {"a": "Damper1.A", "b": "Damper2.B"},
        {"a": "Damper2.B", "b": "Solver1.port"},
    ]
    again = Diagram.from_dict(data, SPECS.get)
    assert again.physical_wires == d.physical_wires
    assert again.to_dict() == d.to_dict()


def test_from_dict_checks_physical_wires() -> None:
    data = new_diagram().to_dict()
    data["physical_wires"] = [{"a": "Damper1.A", "b": "Res1.B"}]
    with pytest.raises(DiagramError, match="different domains"):
        Diagram.from_dict(data, SPECS.get)
    data["physical_wires"] = {"a": "x"}
    with pytest.raises(DiagramError, match="must be lists"):
        Diagram.from_dict(data, SPECS.get)
    data["physical_wires"] = ["Damper1.A"]
    with pytest.raises(DiagramError, match="JSON object"):
        Diagram.from_dict(data, SPECS.get)
