"""M1: gearbox and ideal transformer: lossless two-port ratios.

Both are the same circuit, an ideal transformer between port pair 1 and port pair 2
with ratio N = (across 1) / (across 2):

    across2 = across1 / N           a VCVS behind a 0 V sense source Vi at port 2
    through1 = -through2 / N        a CCCS on Vi into port 1

so the power into port 1 equals the power out of port 2. Through variables are
positive flowing into a port, as everywhere in the library.

- ``Gearbox`` (rotational): ports A (input) and B (output) against the reference,
  with ratio ``N`` = w(A) / w(B); torque is multiplied by N. A reduction gear has
  N > 1; a negative N reverses the direction.
- ``IdealTransformer`` (electrical): ports p1 / n1 and p2 / n2 with turns ratio
  ``n`` = V(p1, n1) / V(p2, n2). Ideal: it passes DC and has no magnetising current.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    CCCS,
    GROUND,
    VCVS,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    VoltageSource,
    physical_component,
)
from core.physical.domains import ELECTRICAL, ROTATIONAL, Domain, get_domain
from core.physical.library._helpers import coupling_gain, real

ELE = get_domain(ELECTRICAL)
ROT = get_domain(ROTATIONAL)


def _ratio(params: Mapping[str, object], name: str) -> float:
    value = real(params, name)
    if value == 0.0:
        raise ValueError(f"{name} must not be 0")
    return value


def ideal_ratio(
    domain: Domain, p1: str, n1: str, p2: str, n2: str, x: str, ratio: float
) -> list[Primitive]:
    """Primitives of an ideal ratio ``ratio`` = across1 / across2; ``x`` is a free
    internal node of the domain."""
    across = coupling_gain(domain.to_voltage, domain.from_voltage, 1.0 / ratio)
    through = coupling_gain(domain.to_current, domain.from_current, -1.0 / ratio)
    return [
        VoltageSource("Vi", p2, x, 0.0),
        VCVS("E", x, n2, p1, n1, across),
        CCCS("F", p1, n1, "Vi", through),
    ]


@physical_component
class Gearbox(PhysicalComponent):
    """Ideal gearbox, ratio N = w(A) / w(B): output speed w(A) / N, output torque
    N times the input torque. Ports A (input) and B (output) turn against the
    reference (the gearbox case is fixed)."""

    ports = (Port("A", ROTATIONAL, "input"), Port("B", ROTATIONAL, "output"))
    parameters = (Parameter("N", 10.0, "", "ratio w(A) / w(B)"),)
    internal = {"x": ROTATIONAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return ideal_ratio(ROT, ports.A, GROUND, ports.B, GROUND, ports.x, _ratio(params, "N"))


@physical_component
class IdealTransformer(PhysicalComponent):
    """Ideal transformer, turns ratio n = V(p1, n1) / V(p2, n2); the currents are in
    the inverse ratio. Lossless, no magnetising inductance (it passes DC)."""

    ports = (
        Port("p1", ELECTRICAL, "primary +"),
        Port("n1", ELECTRICAL, "primary -"),
        Port("p2", ELECTRICAL, "secondary +"),
        Port("n2", ELECTRICAL, "secondary -"),
    )
    parameters = (Parameter("n", 10.0, "", "turns ratio N1 / N2"),)
    internal = {"x": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        n = _ratio(params, "n")
        return ideal_ratio(ELE, ports.p1, ports.n1, ports.p2, ports.n2, ports.x, n)
