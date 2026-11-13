"""M4: PWM generator on the physical side.

The duty cycle comes from a signal input (an ngspice ``external`` source, held between
controller ticks) and is compared with a sawtooth carrier inside ngspice. The
controller never makes an edge (CLAUDE.md: PWM edges are generated inside ngspice).

Timing (trailing-edge PWM, carrier period T = 1 / f_c): the output goes high at every
n*T (the carrier's reset, an ngspice breakpoint of its ``pulse``) and low at
n*T + d*T, where the carrier ramp crosses the held duty d. That crossing is not a
breakpoint of its own, so the component adds a ``Crossing`` hint: in a combined run the
CoSimulator sets a breakpoint there and the edge lands on an accepted time point.
Without a controller the duty is ``initial`` and the falling edge is only as exact as
ngspice's step.

d <= 0 keeps the output low, d >= 1 keeps it high. The output is an ideal voltage
source between ``out`` and ``ref``: ``amplitude`` while high, 0 while low. It drives
the gate port ``g`` of the switching half-bridge and H-bridge (M1 / M3).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    Behavioural,
    Crossing,
    ExternalSource,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    Pulse,
    SignalInput,
    VoltageSource,
    physical_component,
)
from core.physical.domains import ELECTRICAL, get_domain
from core.physical.library._helpers import num, positive, real

ELE = get_domain(ELECTRICAL)

FLYBACK = 1e-5
"""The sawtooth's reset takes this fraction of the period (ngspice needs a ramp)."""


def sawtooth(f_c: float) -> Pulse:
    """Carrier from 0 to 1 (duty units, as a voltage) over each period 1 / f_c."""
    period = 1.0 / f_c
    fly = period * FLYBACK
    return Pulse(
        v1=ELE.to_voltage(0.0),
        v2=ELE.to_voltage(1.0),
        delay=0.0,
        rise=period - fly,
        fall=fly,
        width=0.0,
        period=period,
    )


@physical_component
class PWMGenerator(PhysicalComponent):
    """PWM: out - ref is ``amplitude`` while the duty is above a sawtooth carrier at
    ``f_c``, else 0. High at n/f_c, low at (n + duty)/f_c; duty 0..1 from signal input
    ``duty``, held between ticks."""

    ports = (
        Port("out", ELECTRICAL, "output +"),
        Port("ref", ELECTRICAL, "output -"),
        SignalInput("duty", "", "duty cycle 0..1"),
    )
    parameters = (
        Parameter("f_c", 20e3, "Hz", "carrier frequency"),
        Parameter("amplitude", 1.0, "V", "output high level"),
        Parameter("initial", 0.0, "", "duty before the first tick"),
    )
    internal = {"d": ELECTRICAL, "c": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        f_c = positive(params, "f_c")
        high = num(ELE.to_voltage(real(params, "amplitude")))
        on = f"{ELE.across_expr(ports.d)} > {ELE.across_expr(ports.c)}"
        return [
            ExternalSource("Vd", ports.d, ports.ground, "v", "duty", real(params, "initial")),
            VoltageSource("Vc", ports.c, ports.ground, 0.0, waveform=sawtooth(f_c)),
            Behavioural("Vo", ports.out, ports.ref, "v", f"({on}) ? {high} : 0"),
            Crossing("X", "Vc", "Vd"),
        ]
