"""F3: PhysicalComponent API, primitives, network checks."""

from __future__ import annotations

import pytest

from core.blocks.spec import CONSERVING, PortSpec
from core.netlist.model import ERROR, WARNING
from core.physical.component import (
    CCCS,
    GROUND,
    VCCS,
    Behavioural,
    Capacitor,
    CurrentSource,
    ExternalSource,
    Parameter,
    PhysicalComponent,
    PhysicalComponentError,
    Port,
    Ports,
    Resistor,
    SignalInput,
    SignalOutput,
    VoltageSource,
    build_network,
    check_network,
    physical_component,
    physical_spec_of,
)
from core.physical.domains import (
    DOMAINS,
    ELECTRICAL,
    ROTATIONAL,
    THERMAL,
    get_domain,
)


@physical_component
class ToyResistor(PhysicalComponent):
    """Linear resistor between p and n."""

    ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL))
    parameters = (Parameter("R", 1000.0, "Ohm", "resistance"),)

    def network(self, ports, params):
        r = get_domain(ELECTRICAL).resistance_from(params["R"])
        return [Resistor("R", ports.p, ports.n, r)]


@physical_component
class ToyDamper(PhysicalComponent):
    ports = (Port("A", ROTATIONAL), Port("B", ROTATIONAL))
    parameters = (Parameter("b", 0.5, "N*m*s/rad"),)

    def network(self, ports, params):
        return [
            Resistor("R", ports.A, ports.B, get_domain(ROTATIONAL).resistance_from(params["b"]))
        ]


@physical_component(name="ToyMotor")
class _Motor(PhysicalComponent):
    """Electrical in, torque out: E = k*w, tau = k*i. Couples via controlled sources."""

    ports = (
        Port("p", ELECTRICAL),
        Port("n", ELECTRICAL),
        Port("shaft", ROTATIONAL),
        SignalInput("load", "N*m"),
        SignalOutput("speed", "rad/s"),
    )
    parameters = (Parameter("k", 0.05, "V*s/rad"),)
    internal = {"m": ELECTRICAL}

    def network(self, ports, params):
        k = params["k"]
        return [
            VoltageSource("Vsense", ports.p, ports.m, 0.0),
            Behavioural("Eback", ports.m, ports.n, "v", f"{k}*V(shaft)"),
            CCCS("Ftau", ports.ground, ports.shaft, "Vsense", k),
            ExternalSource("Xload", ports.shaft, ports.ground, "i", "load"),
        ]


# ---- the toy resistor (done-when criterion)


def test_toy_resistor_returns_the_right_primitives() -> None:
    result = build_network(ToyResistor)
    assert result.ok, result.diagnostics
    assert result.primitives == [Resistor("R", "p", "n", 1000.0)]


def test_parameter_override() -> None:
    result = build_network(ToyResistor, {"R": 47.0})
    assert result.primitives == [Resistor("R", "p", "n", 47.0)]


def test_rotational_damper_maps_through_the_registry() -> None:
    result = build_network(ToyDamper, {"b": 4.0})
    assert result.ok
    assert result.primitives == [Resistor("R", "A", "B", 0.25)]  # R = 1/b


def test_spec_and_block_spec() -> None:
    spec = physical_spec_of(ToyResistor)
    assert spec is not None
    assert spec.name == "ToyResistor"
    assert spec.doc == "Linear resistor between p and n."
    assert spec.defaults() == {"R": 1000.0}
    assert spec.parameters[0].unit == "Ohm"
    block = spec.block
    assert block.conserving == [
        PortSpec("p", CONSERVING, ELECTRICAL),
        PortSpec("n", CONSERVING, ELECTRICAL),
    ]
    assert block.inputs == [] and block.outputs == []
    assert block.settings == {"R": 1000.0}
    assert block.color == DOMAINS[ELECTRICAL].color


def test_motor_spec_has_signal_ports_and_named_decorator() -> None:
    spec = physical_spec_of(_Motor)
    assert spec is not None
    assert spec.name == "ToyMotor"
    assert spec.block.inputs == ["load"]
    assert spec.block.outputs == ["speed"]
    assert spec.node_domains() == {
        "p": ELECTRICAL,
        "n": ELECTRICAL,
        "shaft": ROTATIONAL,
        "m": ELECTRICAL,
    }


def test_cross_domain_coupling_through_controlled_sources_is_fine() -> None:
    result = build_network(_Motor)
    assert result.ok, result.diagnostics
    assert [p.name for p in result.primitives] == ["Vsense", "Eback", "Ftau", "Xload"]


def test_ports_namespace() -> None:
    ports = Ports(["A", "B"])
    assert ports.A == "A"
    assert ports["B"] == "B"
    assert ports.ground == GROUND
    assert list(ports) == ["A", "B"]
    with pytest.raises(AttributeError):
        _ = ports.C


# ---- build_network never raises


def test_unknown_parameter_is_a_diagnostic() -> None:
    result = build_network(ToyResistor, {"X": 1.0})
    assert not result.ok
    assert "unknown parameter 'X'" in result.diagnostics[0].message


def test_exception_in_network_is_a_diagnostic() -> None:
    @physical_component
    class Broken(PhysicalComponent):
        ports = (Port("p", ELECTRICAL),)

        def network(self, ports, params):
            return [Resistor("R", ports.p, ports.typo, 1.0)]

    result = build_network(Broken, block="b1")
    assert not result.ok
    assert result.diagnostics[0].block == "b1"
    assert "AttributeError" in result.diagnostics[0].message


def test_bad_mapping_value_is_a_diagnostic() -> None:
    result = build_network(ToyDamper, {"b": 0.0})
    assert not result.ok
    assert "positive" in result.diagnostics[0].message


def test_not_a_component_is_a_diagnostic() -> None:
    result = build_network(int)
    assert not result.ok


# ---- check_network


def _diag(primitives, cls=ToyResistor):
    spec = physical_spec_of(cls)
    assert spec is not None
    return check_network(spec, primitives)


def test_check_unknown_node_and_duplicate_names() -> None:
    diags = _diag([Resistor("R", "p", "x", 1.0), Resistor("R", "p", "n", 1.0)])
    messages = [d.message for d in diags]
    assert any("unknown node 'x'" in m for m in messages)
    assert any("used twice" in m for m in messages)


def test_check_ground_is_always_allowed() -> None:
    assert _diag([Capacitor("C", "p", GROUND, 1e-6), CurrentSource("I", GROUND, "n", 1.0)]) == []


def test_check_refuses_two_terminal_element_across_domains() -> None:
    diags = _diag([Resistor("R", "p", "shaft", 1.0)], _Motor)
    assert [d.severity for d in diags] == [ERROR]
    assert "only controlled sources may couple domains" in diags[0].message


def test_check_vccs_may_be_controlled_from_another_domain() -> None:
    assert _diag([VCCS("G", GROUND, "shaft", "p", "n", 0.1)], _Motor) == []


def test_check_controlled_source_control_and_signal() -> None:
    diags = _diag(
        [CCCS("F", "p", "n", "nope", 1.0), ExternalSource("X", "p", "n", "i", "nope")], _Motor
    )
    messages = [d.message for d in diags]
    assert any("is not a VoltageSource" in m for m in messages)
    assert any("is not a SignalInput" in m for m in messages)


def test_check_behavioural_expression_references() -> None:
    diags = _diag(
        [
            VoltageSource("V1", "p", "n", 0.0),
            Behavioural("B1", "p", "n", "i", "V(p,n)*2 + I(V1) + V(0)"),
            Behavioural("B2", "p", "n", "i", "V(zz) + I(R9)"),
            Behavioural("B3", "p", "n", "q", ""),
        ]
    )
    by_prim = {}
    for d in diags:
        by_prim.setdefault(d.primitive, []).append(d.message)
    assert "B1" not in by_prim
    assert len(by_prim["B2"]) == 2
    assert any("kind" in m for m in by_prim["B3"]) and any("empty" in m for m in by_prim["B3"])


def test_check_values_and_shorted_element() -> None:
    diags = _diag([Resistor("R", "p", "p", float("nan")), "junk"])
    severities = sorted(d.severity for d in diags)
    assert severities == [ERROR, ERROR, WARNING]


# ---- declaration errors raise at decoration time


def test_decorator_rejects_bad_declarations() -> None:
    with pytest.raises(PhysicalComponentError, match="must implement network"):

        @physical_component
        class NoNetwork(PhysicalComponent):
            ports = (Port("p", THERMAL),)

    with pytest.raises(PhysicalComponentError, match="unknown domain"):

        @physical_component
        class Hydraulic(PhysicalComponent):
            ports = (Port("p", "hydraulic"),)

            def network(self, ports, params):
                return []

    with pytest.raises(PhysicalComponentError, match="used twice"):

        @physical_component
        class Dup(PhysicalComponent):
            ports = (Port("p", THERMAL),)
            internal = {"p": THERMAL}

            def network(self, ports, params):
                return []

    with pytest.raises(PhysicalComponentError, match="at least one conserving"):

        @physical_component
        class OnlySignals(PhysicalComponent):
            ports = (SignalInput("u"),)

            def network(self, ports, params):
                return []

    with pytest.raises(PhysicalComponentError, match="number, bool or string"):

        @physical_component
        class BadDefault(PhysicalComponent):
            ports = (Port("p", THERMAL),)
            parameters = (Parameter("C", [1.0]),)

            def network(self, ports, params):
                return []

    with pytest.raises(PhysicalComponentError, match="PhysicalComponent"):
        physical_component(object)


def test_subclass_does_not_inherit_the_mark() -> None:
    class Child(ToyResistor):
        pass

    assert physical_spec_of(Child) is None
