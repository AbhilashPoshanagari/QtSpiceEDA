"""M1 / M2 API additions: compose(), composed_internal() and SubcircuitInstance."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

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
    VoltageSource,
    build_network,
    compose,
    composed_internal,
    physical_component,
)
from core.physical.domains import ELECTRICAL
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
