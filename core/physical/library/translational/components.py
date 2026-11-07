"""B3: basic mechanical translational components (PhysicalComponent API).

Across is the velocity v [m/s], through the force F [N]. Two-port parts have ports A
and B; their force is positive flowing into A and out of B, and their relative
velocity is v(A) - v(B).

- Sources deliver their force into the network at A (it flows from B through the
  source to A); a velocity source sets v(A) - v(B).
- A mass is a one-port element on the reference.

Initial values (``v0``, ``F0``) are used by runs with ``uic``; the combined run
starts from the operating point and ignores them until M6.
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
from core.physical.domains import TRANSLATIONAL, get_domain
from core.physical.library._helpers import real

TRA = get_domain(TRANSLATIONAL)

_PORTS = (Port("A", TRANSLATIONAL), Port("B", TRANSLATIONAL))


@physical_component
class Mass(PhysicalComponent):
    """Mass m at port A; ``v0`` is its start velocity."""

    ports = (Port("A", TRANSLATIONAL),)
    parameters = (
        Parameter("m", 1.0, "kg", "mass"),
        Parameter("v0", 0.0, "m/s", "initial velocity"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        c = TRA.capacitance_from(real(params, "m"))
        return [Capacitor("C", ports.A, ports.ground, c, TRA.to_voltage(real(params, "v0")))]


@physical_component
class TranslationalDamper(PhysicalComponent):
    """Viscous damper: F = b * (v(A) - v(B))."""

    ports = _PORTS
    parameters = (Parameter("b", 1.0, "N*s/m", "damping"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [Resistor("R", ports.A, ports.B, TRA.resistance_from(real(params, "b")))]


@physical_component
class TranslationalSpring(PhysicalComponent):
    """Linear spring: dF/dt = k * (v(A) - v(B)); ``F0`` is its start force."""

    ports = _PORTS
    parameters = (
        Parameter("k", 100.0, "N/m", "stiffness"),
        Parameter("F0", 0.0, "N", "initial force"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        ind = TRA.inductance_from(real(params, "k"))
        return [Inductor("L", ports.A, ports.B, ind, TRA.to_current(real(params, "F0")))]


@physical_component
class ForceSource(PhysicalComponent):
    """Constant force F, delivered into the network at A (and taken from B)."""

    ports = _PORTS
    parameters = (Parameter("F", 1.0, "N", "force"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [CurrentSource("I", ports.B, ports.A, TRA.to_current(real(params, "F")))]


@physical_component
class VelocitySource(PhysicalComponent):
    """Ideal velocity source: v(A) - v(B) = v."""

    ports = _PORTS
    parameters = (Parameter("v", 1.0, "m/s", "velocity"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [VoltageSource("V", ports.A, ports.B, TRA.to_voltage(real(params, "v")))]


@physical_component
class MechanicalTranslationalReference(PhysicalComponent):
    """Translational reference (a fixed frame), 0 m/s."""

    role = REFERENCE
    ports = (Port("R", TRANSLATIONAL, "reference"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return []
