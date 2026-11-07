"""M1: DC motor, built from the library's own parts with ``compose`` (rule 13).

Ports: ``p`` / ``n`` (armature, electrical) and ``A`` / ``B`` (rotational: shaft and
case; wire B to the rotational reference for a motor on a fixed frame).

Equations (armature current i into p, speed w = w(A) - w(B)):

    L di/dt = V(p, n) - R i - k w          back EMF   E   = k w
    J dw/dt = k i - b w - tau_load         torque     tau = k i

Circuit: p -R- m -L- e -Vi(0 V)- f -E- n, with E = k * (w(A) - w(B)) a VCVS. The
torque k * i is a CCCS on ``Vi`` that drives torque into A and out of B (the reaction
on the case). The rotor inertia J sits on A against the reference (an inertia is a
one-port element on the reference) and the viscous friction b between A and B.

The cross-domain gains come from the Domain registry (``coupling_gain``), never from a
hard-coded "E = k*w" in volts (rule 12). Unloaded, the speed settles at
w = k V / (k^2 + R b); the two time constants are close to L / R and J R / k^2.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    CCCS,
    VCVS,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    VoltageSource,
    compose,
    physical_component,
)
from core.physical.domains import ELECTRICAL, ROTATIONAL, get_domain
from core.physical.library._helpers import coupling_gain, positive, real
from core.physical.library.electrical.components import InductorPart, ResistorPart
from core.physical.library.rotational.components import Inertia, RotationalDamper

ELE = get_domain(ELECTRICAL)
ROT = get_domain(ROTATIONAL)


@physical_component
class DCMotor(PhysicalComponent):
    """Permanent-magnet DC motor: armature R and L, motor constant k (back EMF k*w,
    torque k*i), rotor inertia J and viscous friction b.

    Electrical ports p (+) and n (-); rotational ports A (shaft) and B (case).
    """

    ports = (
        Port("p", ELECTRICAL, "+"),
        Port("n", ELECTRICAL, "-"),
        Port("A", ROTATIONAL, "shaft"),
        Port("B", ROTATIONAL, "case"),
    )
    parameters = (
        Parameter("R", 1.0, "Ohm", "armature resistance"),
        Parameter("L", 1e-3, "H", "armature inductance"),
        Parameter("k", 0.05, "N*m/A", "motor constant (also V*s/rad)"),
        Parameter("J", 1e-4, "kg*m^2", "rotor inertia"),
        Parameter("b", 1e-5, "N*m*s/rad", "viscous friction"),
    )
    internal = {"m": ELECTRICAL, "e": ELECTRICAL, "f": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        k = positive(params, "k")
        emf = coupling_gain(ELE.to_voltage, ROT.from_voltage, k)
        torque = coupling_gain(ROT.to_current, ELE.from_current, k)
        return [
            *compose(ResistorPart, "Ra", {"p": ports.p, "n": ports.m}, {"R": real(params, "R")}),
            *compose(InductorPart, "La", {"p": ports.m, "n": ports.e}, {"L": real(params, "L")}),
            VoltageSource("Vi", ports.e, ports.f, 0.0),
            VCVS("Emf", ports.f, ports.n, ports.A, ports.B, emf),
            # k*i flows from B through the source into A: torque into the shaft.
            CCCS("Tq", ports.B, ports.A, "Vi", torque),
            *compose(Inertia, "Jr", {"A": ports.A}, {"J": real(params, "J")}),
            *compose(
                RotationalDamper, "Br", {"A": ports.A, "B": ports.B}, {"b": real(params, "b")}
            ),
        ]
