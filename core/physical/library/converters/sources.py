"""B5: signal-controlled sources (signal -> physical converters).

Each source takes its value from a signal input at every controller tick and holds it
until the next one (an ngspice ``external`` source, C1). ``initial`` is the value
before the first tick (it sets the operating point the run starts from) and the
value in runs without a controller.

Sign conventions match the constant sources of B1-B4: electrical sources follow
SPICE (V(p) - V(n) = V; the current flows from p through the source to n);
mechanical and thermal through-sources deliver their value into the network at A;
across-sources set A - B. The signal is in SI; the Domain registry turns it into a
voltage or current (rule 12), ``initial`` included.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    ExternalSource,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    SignalInput,
    physical_component,
)
from core.physical.domains import (
    ELECTRICAL,
    ROTATIONAL,
    THERMAL,
    TRANSLATIONAL,
)
from core.physical.library._helpers import real


def _two(domain: str) -> tuple[Port, Port]:
    return Port("A", domain), Port("B", domain)


def _initial(unit: str, default: float = 0.0) -> tuple[Parameter, ...]:
    return (Parameter("initial", default, unit, "value before the first tick"),)


def _through(ports: Ports, signal: str, params: Mapping[str, object]) -> list[Primitive]:
    """Through value delivered into A (it flows from B through the source to A)."""
    return [ExternalSource("I", ports.B, ports.A, "i", signal, real(params, "initial"))]


def _across(ports: Ports, signal: str, params: Mapping[str, object]) -> list[Primitive]:
    """A - B = signal."""
    return [ExternalSource("V", ports.A, ports.B, "v", signal, real(params, "initial"))]


# ---- electrical


@physical_component
class ControlledVoltageSource(PhysicalComponent):
    """V(p) - V(n) = signal input V."""

    ports = (Port("p", ELECTRICAL, "+"), Port("n", ELECTRICAL, "-"), SignalInput("V", "V"))
    parameters = _initial("V")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [ExternalSource("V", ports.p, ports.n, "v", "V", real(params, "initial"))]


@physical_component
class ControlledCurrentSource(PhysicalComponent):
    """Current from signal input I, from p through the source to n."""

    ports = (Port("p", ELECTRICAL, "+"), Port("n", ELECTRICAL, "-"), SignalInput("I", "A"))
    parameters = _initial("A")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [ExternalSource("I", ports.p, ports.n, "i", "I", real(params, "initial"))]


# ---- rotational


@physical_component
class ControlledTorqueSource(PhysicalComponent):
    """Torque from signal input tau, delivered into the network at A."""

    ports = (*_two(ROTATIONAL), SignalInput("tau", "N*m"))
    parameters = _initial("N*m")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _through(ports, "tau", params)


@physical_component
class ControlledAngularVelocitySource(PhysicalComponent):
    """w(A) - w(B) = signal input w."""

    ports = (*_two(ROTATIONAL), SignalInput("w", "rad/s"))
    parameters = _initial("rad/s")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _across(ports, "w", params)


# ---- translational


@physical_component
class ControlledForceSource(PhysicalComponent):
    """Force from signal input F, delivered into the network at A."""

    ports = (*_two(TRANSLATIONAL), SignalInput("F", "N"))
    parameters = _initial("N")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _through(ports, "F", params)


@physical_component
class ControlledVelocitySource(PhysicalComponent):
    """v(A) - v(B) = signal input v."""

    ports = (*_two(TRANSLATIONAL), SignalInput("v", "m/s"))
    parameters = _initial("m/s")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _across(ports, "v", params)


# ---- thermal


@physical_component
class ControlledHeatFlowSource(PhysicalComponent):
    """Heat flow from signal input Q, delivered into the network at A."""

    ports = (*_two(THERMAL), SignalInput("Q", "W"))
    parameters = _initial("W")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _through(ports, "Q", params)


@physical_component
class ControlledTemperatureSource(PhysicalComponent):
    """T(A) - T(B) = signal input T (with B on the Thermal Reference: absolute T)."""

    ports = (*_two(THERMAL), SignalInput("T", "K"))
    parameters = _initial("K", 298.15)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _across(ports, "T", params)
