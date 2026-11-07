"""Test-only physical components for the F5/F6 tests.

These are NOT the B1 library: they are the smallest parts needed to build a
mass-spring-damper, a thermal RC network and a two-domain network, written with
the public PhysicalComponent API only. Element values always come from the
Domain registry (rule 12).
"""

from __future__ import annotations

from core.blocks.diagram import Diagram
from core.blocks.library import builtin_specs
from core.blocks.spec import BlockSpec
from core.physical.component import (
    CCCS,
    REFERENCE,
    SOLVER,
    VCCS,
    VCVS,
    Capacitor,
    CurrentSource,
    ExternalSource,
    Inductor,
    Parameter,
    PhysicalComponent,
    Port,
    Resistor,
    Sensor,
    SignalInput,
    SignalOutput,
    VoltageSource,
    physical_component,
)
from core.physical.domains import (
    ANY_DOMAIN,
    ELECTRICAL,
    ROTATIONAL,
    THERMAL,
    TRANSLATIONAL,
    get_domain,
)
from core.physical.registry import PhysicalRegistry

TRA = get_domain(TRANSLATIONAL)
ROT = get_domain(ROTATIONAL)
THE = get_domain(THERMAL)
ELE = get_domain(ELECTRICAL)


# ---- references and solver


@physical_component
class Solver(PhysicalComponent):
    role = SOLVER
    ports = (Port("port", ANY_DOMAIN),)

    def network(self, ports, params):
        return []


@physical_component
class TransRef(PhysicalComponent):
    role = REFERENCE
    ports = (Port("V", TRANSLATIONAL),)

    def network(self, ports, params):
        return []


@physical_component
class RotRef(PhysicalComponent):
    role = REFERENCE
    ports = (Port("W", ROTATIONAL),)

    def network(self, ports, params):
        return []


@physical_component
class ThermalRef(PhysicalComponent):
    role = REFERENCE
    ports = (Port("T", THERMAL),)

    def network(self, ports, params):
        return []


@physical_component
class ElecRef(PhysicalComponent):
    role = REFERENCE
    ports = (Port("G", ELECTRICAL),)

    def network(self, ports, params):
        return []


# ---- translational


@physical_component
class Mass(PhysicalComponent):
    """Mass at port A (velocity relative to the reference). ``v0`` is its start velocity."""

    ports = (Port("A", TRANSLATIONAL),)
    parameters = (Parameter("m", 1.0, "kg"), Parameter("v0", 0.0, "m/s"))

    def network(self, ports, params):
        return [
            Capacitor("C", ports.A, ports.ground, TRA.capacitance_from(params["m"]), params["v0"])
        ]


@physical_component
class Spring(PhysicalComponent):
    ports = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL))
    parameters = (Parameter("k", 1.0, "N/m"),)

    def network(self, ports, params):
        return [Inductor("L", ports.A, ports.B, TRA.inductance_from(params["k"]), 0.0)]


@physical_component
class Damper(PhysicalComponent):
    ports = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL))
    parameters = (Parameter("b", 1.0, "N*s/m"),)

    def network(self, ports, params):
        return [Resistor("R", ports.A, ports.B, TRA.resistance_from(params["b"]))]


@physical_component
class Force(PhysicalComponent):
    """Constant force F into port A (out of port B)."""

    ports = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL))
    parameters = (Parameter("F", 1.0, "N"),)

    def network(self, ports, params):
        return [CurrentSource("I", ports.B, ports.A, TRA.to_current(params["F"]))]


@physical_component
class ForceInput(PhysicalComponent):
    """Force into port A taken from signal input ``F`` (external source)."""

    ports = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL), SignalInput("F", "N"))

    def network(self, ports, params):
        return [ExternalSource("I", ports.B, ports.A, "i", "F", 0.5)]


# ---- thermal


@physical_component
class ThermalMass(PhysicalComponent):
    ports = (Port("A", THERMAL),)
    parameters = (Parameter("C", 1.0, "J/K"), Parameter("T0", 298.15, "K"))

    def network(self, ports, params):
        return [
            Capacitor("C", ports.A, ports.ground, THE.capacitance_from(params["C"]), params["T0"])
        ]


@physical_component
class ThermalResistance(PhysicalComponent):
    ports = (Port("A", THERMAL), Port("B", THERMAL))
    parameters = (Parameter("R", 1.0, "K/W"),)

    def network(self, ports, params):
        return [Resistor("R", ports.A, ports.B, THE.resistance_from(params["R"]))]


@physical_component
class HeatFlow(PhysicalComponent):
    """Constant heat flow Q into port A (out of port B)."""

    ports = (Port("A", THERMAL), Port("B", THERMAL))
    parameters = (Parameter("Q", 1.0, "W"),)

    def network(self, ports, params):
        return [CurrentSource("I", ports.B, ports.A, THE.to_current(params["Q"]))]


@physical_component
class TemperatureSource(PhysicalComponent):
    """T(A) - T(B) = T."""

    ports = (Port("A", THERMAL), Port("B", THERMAL))
    parameters = (Parameter("T", 298.15, "K"),)

    def network(self, ports, params):
        return [VoltageSource("V", ports.A, ports.B, THE.to_voltage(params["T"]))]


# ---- two domains


@physical_component
class RotDamper(PhysicalComponent):
    ports = (Port("A", ROTATIONAL), Port("B", ROTATIONAL))
    parameters = (Parameter("b", 0.01, "N*m*s/rad"),)

    def network(self, ports, params):
        return [Resistor("R", ports.A, ports.B, ROT.resistance_from(params["b"]))]


@physical_component
class Machine(PhysicalComponent):
    """Toy DC machine: E = k*w, tau = k*i, coupled only through controlled sources."""

    ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL), Port("shaft", ROTATIONAL))
    parameters = (Parameter("k", 0.05, "V*s/rad"),)
    internal = {"m": ELECTRICAL}

    def network(self, ports, params):
        k = params["k"]
        return [
            VoltageSource("Vs", ports.p, ports.m, 0.0),
            VCVS("E", ports.m, ports.n, ports.shaft, ports.ground, k),
            CCCS("T", ports.ground, ports.shaft, "Vs", k),
        ]


@physical_component
class ElecResistor(PhysicalComponent):
    ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL))
    parameters = (Parameter("R", 1.0, "Ohm"),)

    def network(self, ports, params):
        return [Resistor("R", ports.p, ports.n, ELE.resistance_from(params["R"]))]


@physical_component
class Integrator(PhysicalComponent):
    """Position sensor: integrates the velocity at A into internal node ``pos`` (G source
    into 1 F) and reports it as signal output ``x``."""

    ports = (Port("A", TRANSLATIONAL), SignalOutput("x", "m"))
    internal = {"pos": TRANSLATIONAL}

    def network(self, ports, params):
        return [
            VCCS("G", ports.ground, ports.pos, ports.A, ports.ground, 1.0),
            Capacitor("C", ports.pos, ports.ground, 1.0, 0.0),
            Sensor("S", "x", "V(pos)"),
        ]


# ---- helpers


def registry() -> PhysicalRegistry:
    import sys

    reg = PhysicalRegistry()
    reg.add_module(sys.modules[__name__])
    return reg


def new_diagram(reg: PhysicalRegistry | None = None) -> tuple[Diagram, PhysicalRegistry]:
    """An empty diagram that knows the built-in signal blocks and these components."""
    reg = reg or registry()
    signal = {s.name: s for s in builtin_specs()}

    def lookup(name: str) -> BlockSpec | None:
        spec = reg.spec(name)
        return spec.block if spec is not None else signal.get(name)

    return Diagram(lookup), reg


def wire(d: Diagram, a: str, b: str) -> None:
    d.add_physical_wire(*a.split("."), *b.split("."))
