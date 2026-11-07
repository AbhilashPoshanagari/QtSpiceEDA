"""B5: sensors (physical -> signal converters).

Every sensor has ports A and B (p and n for electrical) and one signal output, in SI.
The output is computed inside ngspice and saved as ``<block>.<signal>``; in a
combined run the controller sees the value of the previous tick (rule 14).

- Across sensors (voltage, angular velocity, velocity, temperature) read A - B and
  draw no through flow; wire B to the reference for an absolute value.
- Through sensors (current, torque, force, heat flow) sit in series: the flow goes in
  at A and out at B, through a 0 V source, and is positive in that direction.
- Angle and position integrate the speed A - B inside the network (rule: position and
  angle are made by integrating velocity). They start at 0. The integrator has a
  1e12 shunt so the operating point exists; the run must therefore start at rest
  (A - B = 0 at t = 0), or the start value is wrong. Starting from a non-zero
  speed belongs to M6.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    VCCS,
    Capacitor,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    Resistor,
    Sensor,
    SignalOutput,
    VoltageSource,
    physical_component,
)
from core.physical.domains import (
    ELECTRICAL,
    ROTATIONAL,
    THERMAL,
    TRANSLATIONAL,
    get_domain,
)

_SHUNT = 1e12
"""Shunt across the integrator node: leak time constant 1e12 s, negligible."""


def _across(domain: str, a: str, b: str, signal: str) -> list[Primitive]:
    return [Sensor("S", signal, get_domain(domain).across_expr(a, b))]


def _through(domain: str, a: str, b: str, signal: str) -> list[Primitive]:
    return [
        VoltageSource("Vs", a, b, 0.0),
        Sensor("S", signal, get_domain(domain).through_expr("Vs")),
    ]


def _integral(domain: str, ports: Ports, signal: str) -> list[Primitive]:
    """d(node)/dt = A - B on internal node ``integ`` (a 1 F capacitor fed by a G
    source of gain 1), read as ``signal``."""
    dom = get_domain(domain)
    return [
        VCCS("G", ports.ground, ports.integ, ports.A, ports.B, 1.0),
        Capacitor("C", ports.integ, ports.ground, 1.0, 0.0),
        Resistor("Rs", ports.integ, ports.ground, _SHUNT),
        Sensor("S", signal, dom.across_expr(ports.integ)),
    ]


# ---- electrical


@physical_component
class VoltageSensor(PhysicalComponent):
    """Signal output V = V(p) - V(n)."""

    ports = (Port("p", ELECTRICAL, "+"), Port("n", ELECTRICAL, "-"), SignalOutput("V", "V"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _across(ELECTRICAL, ports.p, ports.n, "V")


@physical_component
class CurrentSensor(PhysicalComponent):
    """Signal output I: the current flowing in at p and out at n (in series)."""

    ports = (Port("p", ELECTRICAL, "+"), Port("n", ELECTRICAL, "-"), SignalOutput("I", "A"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _through(ELECTRICAL, ports.p, ports.n, "I")


# ---- rotational


@physical_component
class AngularVelocitySensor(PhysicalComponent):
    """Signal output w = w(A) - w(B)."""

    ports = (Port("A", ROTATIONAL), Port("B", ROTATIONAL), SignalOutput("w", "rad/s"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _across(ROTATIONAL, ports.A, ports.B, "w")


@physical_component
class AngleSensor(PhysicalComponent):
    """Signal output phi: the integral of w(A) - w(B), from 0. Start at rest (see the
    module notes)."""

    ports = (Port("A", ROTATIONAL), Port("B", ROTATIONAL), SignalOutput("phi", "rad"))
    internal = {"integ": ROTATIONAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _integral(ROTATIONAL, ports, "phi")


@physical_component
class TorqueSensor(PhysicalComponent):
    """Signal output tau: the torque flowing in at A and out at B (in series)."""

    ports = (Port("A", ROTATIONAL), Port("B", ROTATIONAL), SignalOutput("tau", "N*m"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _through(ROTATIONAL, ports.A, ports.B, "tau")


# ---- translational


@physical_component
class VelocitySensor(PhysicalComponent):
    """Signal output v = v(A) - v(B)."""

    ports = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL), SignalOutput("v", "m/s"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _across(TRANSLATIONAL, ports.A, ports.B, "v")


@physical_component
class PositionSensor(PhysicalComponent):
    """Signal output x: the integral of v(A) - v(B), from 0. Start at rest (see the
    module notes)."""

    ports = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL), SignalOutput("x", "m"))
    internal = {"integ": TRANSLATIONAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _integral(TRANSLATIONAL, ports, "x")


@physical_component
class ForceSensor(PhysicalComponent):
    """Signal output F: the force flowing in at A and out at B (in series)."""

    ports = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL), SignalOutput("F", "N"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _through(TRANSLATIONAL, ports.A, ports.B, "F")


# ---- thermal


@physical_component
class TemperatureSensor(PhysicalComponent):
    """Signal output T = T(A) - T(B); with B on the Thermal Reference, absolute T."""

    ports = (Port("A", THERMAL), Port("B", THERMAL), SignalOutput("T", "K"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _across(THERMAL, ports.A, ports.B, "T")


@physical_component
class HeatFlowSensor(PhysicalComponent):
    """Signal output Q: the heat flowing in at A and out at B (in series)."""

    ports = (Port("A", THERMAL), Port("B", THERMAL), SignalOutput("Q", "W"))

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return _through(THERMAL, ports.A, ports.B, "Q")
