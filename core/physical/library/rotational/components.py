"""B2: basic mechanical rotational components (PhysicalComponent API).

Across is the angular velocity w [rad/s], through the torque tau [N*m]. Two-port parts
have ports A and B; their torque is positive flowing into A and out of B, and their
relative speed is w(A) - w(B).

- Sources deliver their torque into the network at A (it flows from B through the
  source to A); a speed source sets w(A) - w(B).
- An inertia is a one-port element on the reference (rule: mass-like elements are
  connected to the reference).

Initial values (``w0``, ``tau0``) are the start values of a combined run (ngspice
``ic=`` with ``uic``), unless its Solver Configuration has ``steady_state`` set (M6).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    REFERENCE,
    Capacitor,
    CurrentSource,
    Inductor,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    Resistor,
    VoltageSource,
    physical_component,
)
from core.physical.domains import ROTATIONAL, get_domain
from core.physical.library._helpers import real

ROT = get_domain(ROTATIONAL)

_PORTS = (Port("A", ROTATIONAL), Port("B", ROTATIONAL))


@physical_component
class Inertia(PhysicalComponent):
    """Rotating inertia J at port A; ``w0`` is its start speed."""

    ports = (Port("A", ROTATIONAL),)
    parameters = (
        Parameter("J", 0.01, "kg*m^2", "inertia"),
        Parameter("w0", 0.0, "rad/s", "initial speed"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        c = ROT.capacitance_from(real(params, "J"))
        return [Capacitor("C", ports.A, ports.ground, c, ROT.to_voltage(real(params, "w0")))]


@physical_component
class RotationalDamper(PhysicalComponent):
    """Viscous damper: tau = b * (w(A) - w(B))."""

    ports = _PORTS
    parameters = (Parameter("b", 0.001, "N*m*s/rad", "damping"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [Resistor("R", ports.A, ports.B, ROT.resistance_from(real(params, "b")))]


@physical_component
class RotationalSpring(PhysicalComponent):
    """Torsion spring: d(tau)/dt = k * (w(A) - w(B)); ``tau0`` is its start torque."""

    ports = _PORTS
    parameters = (
        Parameter("k", 10.0, "N*m/rad", "stiffness"),
        Parameter("tau0", 0.0, "N*m", "initial torque"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        ind = ROT.inductance_from(real(params, "k"))
        tau0 = ROT.to_current(real(params, "tau0"))
        return [Inductor("L", ports.A, ports.B, ind, tau0)]


@physical_component
class TorqueSource(PhysicalComponent):
    """Constant torque tau, delivered into the network at A (and taken from B)."""

    ports = _PORTS
    parameters = (Parameter("tau", 1.0, "N*m", "torque"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [CurrentSource("I", ports.B, ports.A, ROT.to_current(real(params, "tau")))]


@physical_component
class AngularVelocitySource(PhysicalComponent):
    """Ideal speed source: w(A) - w(B) = w."""

    ports = _PORTS
    parameters = (Parameter("w", 1.0, "rad/s", "angular velocity"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [VoltageSource("V", ports.A, ports.B, ROT.to_voltage(real(params, "w")))]


@physical_component
class MechanicalRotationalReference(PhysicalComponent):
    """Rotational reference (a fixed frame), 0 rad/s."""

    role = REFERENCE
    ports = (Port("R", ROTATIONAL, "reference"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return []
