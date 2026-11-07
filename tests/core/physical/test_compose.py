"""M1 / M2 API additions: compose(), composed_internal() and SubcircuitInstance."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.netlist.model import ERROR
from core.physical.component import (
    CCCS,
    GROUND,
    Behavioural,
    ExternalSource,
    Parameter,
    PhysicalComponent,
    PhysicalComponentError,
    Port,
    Ports,
    Primitive,
    Sensor,
    SignalInput,
    SignalOutput,
    SubcircuitInstance,
    VoltageSource,
    build_network,
    compose,
    composed_internal,
    physical_component,
    physical_spec_of,
)
from core.physical.domains import ELECTRICAL, ROTATIONAL
from core.physical.library.electrical.components import ControlledSwitch, Diode, ResistorPart


@physical_component
class Inner(PhysicalComponent):
    """Test part with every renamed field."""

    ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL), SignalInput("u"), SignalOutput("y"))
    parameters = (Parameter("g", 2.0),)
    internal = {"c": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [
            ExternalSource("Vc", ports.c, ports.ground, "v", "u"),
            VoltageSource("Vi", ports.p, ports.c, 0.0),
            CCCS("F", ports.n, GROUND, "Vi", float(params["g"])),  # type: ignore[arg-type]
            Behavioural("B", ports.p, ports.n, "i", "V(p,n) * V(c) + I(Vi)"),
            Sensor("S", "y", "V(c)"),
        ]


def test_compose_renames_names_nodes_controls_expressions_and_signals() -> None:
    prims = compose(Inner, "X", {"p": "a", "n": GROUND}, {"g": 3.0}, {"u": "ctrl", "y": "out"})
    by_name = {p.name: p for p in prims}
    assert set(by_name) == {"X_Vc", "X_Vi", "X_F", "X_B", "X_S"}
    assert by_name["X_Vc"] == ExternalSource("X_Vc", "X_c", GROUND, "v", "ctrl")
    assert by_name["X_Vi"] == VoltageSource("X_Vi", "a", "X_c", 0.0)
    assert by_name["X_F"] == CCCS("X_F", GROUND, GROUND, "X_Vi", 3.0)
    assert by_name["X_B"].expression == "V(a,0) * V(X_c) + I(X_Vi)"  # type: ignore[union-attr]
    assert by_name["X_S"] == Sensor("X_S", "out", "V(X_c)")
    assert composed_internal(Inner, "X") == {"X_c": ELECTRICAL}


def test_compose_reports_bad_ports_and_parameters() -> None:
    with pytest.raises(ValueError, match="missing n"):
        compose(ResistorPart, "R1", {"p": "a"})
    with pytest.raises(ValueError, match="unknown q"):
        compose(ResistorPart, "R1", {"p": "a", "n": "b", "q": "c"})
    with pytest.raises(ValueError, match="unknown parameter"):
        compose(ResistorPart, "R1", {"p": "a", "n": "b"}, {"nope": 1})
    with pytest.raises(PhysicalComponentError):
        compose(int, "x", {})


@physical_component
class Outer(PhysicalComponent):
    """Two switches and a diode, composed."""

    ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL), SignalInput("u"))
    internal = {"m": ELECTRICAL, **composed_internal(ControlledSwitch, "S1")}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [
            *compose(ControlledSwitch, "S1", {"p": ports.p, "n": ports.m}, signals={"ctrl": "u"}),
            *compose(Diode, "D1", {"p": ports.m, "n": ports.n}),
        ]


def test_a_composed_part_passes_the_network_checks() -> None:
    result = build_network(Outer, block="O")
    assert result.ok, result.diagnostics
    assert result.diagnostics == []


@physical_component
class Broken(PhysicalComponent):
    """Composes a part with a bad parameter."""

    ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return compose(ResistorPart, "R1", {"p": ports.p, "n": ports.n}, {"R": "oops"})


def test_a_failing_compose_is_a_diagnostic_of_the_outer_block() -> None:
    result = build_network(Broken, block="B7")
    assert not result.ok
    (diag,) = result.diagnostics
    assert diag.block == "B7"
    assert "R1 (Resistor)" in diag.message


# ---- SubcircuitInstance checks

_TEXT = "* sub\n.subckt rc in out\nR1 in out 1k\nC1 out 0 1u\n.ends rc\n.end\n"


def _sub_part(nodes: tuple[str, ...], text: str = _TEXT, subckt: str = "rc") -> type:
    class Part(PhysicalComponent):
        """Subcircuit test part."""

        ports = (Port("a", ELECTRICAL), Port("b", ELECTRICAL), Port("w", ROTATIONAL))

        def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
            return [SubcircuitInstance("X", nodes, subckt, text)]

    return physical_component(Part)


def test_subcircuit_instance_checks_its_nodes_and_text() -> None:
    assert build_network(_sub_part(("a", "b")), block="P").diagnostics == []
    messages = {
        ("a", "zz"): "unknown node 'zz'",
        ("a", "w"): "only electrical nodes",
        (): "non-empty tuple",
    }
    for nodes, message in messages.items():
        diags = build_network(_sub_part(nodes), block="P").diagnostics
        assert any(d.severity == ERROR and message in d.message for d in diags), (nodes, diags)
    diags = build_network(_sub_part(("a", "b"), text="* nothing\n"), block="P").diagnostics
    assert any("must hold the .subckt" in d.message for d in diags)
    diags = build_network(_sub_part(("a", "b"), subckt=" "), block="P").diagnostics
    assert any("must name a .subckt" in d.message for d in diags)


def test_compose_renames_subcircuit_nodes() -> None:
    part = _sub_part(("a", "b"))
    assert physical_spec_of(part) is not None
    (prim,) = compose(part, "U", {"a": "x", "b": GROUND, "w": "r"})
    assert prim == SubcircuitInstance("U_X", ("x", GROUND), "rc", _TEXT)


def _electrical_sub(text: str) -> type:
    class Part(PhysicalComponent):
        """Subcircuit test part."""

        ports = (Port("a", ELECTRICAL), Port("b", ELECTRICAL))

        def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
            return [SubcircuitInstance("X", (ports.a, ports.b), "rc", text)]

    return Part


def test_the_mapper_refuses_two_different_subckts_of_one_name() -> None:
    from core.physical.library import physical_registry
    from tests.fixtures.physical.library import add_solver, new_diagram, wire

    other = _TEXT.replace("1k", "2k")
    reg = physical_registry()
    reg.add(physical_component(_electrical_sub(_TEXT), name="SubA"))
    reg.add(physical_component(_electrical_sub(other), name="SubB"))
    d, _ = new_diagram(reg)
    d.add_block("ElectricalReference", block_id="eref")
    add_solver(d, reg, "eref.G")
    d.add_block("SubA", block_id="A1")
    d.add_block("SubB", block_id="B1")
    wire(d, "A1.a", "B1.a")
    wire(d, "A1.b", "eref.G")
    wire(d, "B1.b", "eref.G")
    from core.physical.mapper import NetworkMapper
    from core.physical.network import NetworkExtractor

    (network,) = NetworkExtractor(reg).extract(d).networks
    out = NetworkMapper(reg).map(network, ".tran 1m 1m", external=False)
    assert not out.ok
    assert any("different .subckt rc" in diag.message for diag in out.diagnostics)
    assert sum(line.startswith(".subckt") for line in out.deck.splitlines()) == 1
