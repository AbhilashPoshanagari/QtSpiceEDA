"""M1: switching half-bridge and H-bridge.

Ports: ``p`` / ``n`` (DC link + / -) and the leg outputs (``out``; ``a`` and ``b``
for the H-bridge); signal input ``u``.

Each leg is two signal-controlled switches (``R_on`` / ``R_off``) with antiparallel
diodes (the library ``Diode``, by ``compose``). While ``u`` > ``threshold`` the high
switch is closed and the low one open, otherwise the other way round (complementary,
no dead time). The H-bridge drives leg ``a`` with ``u`` and leg ``b`` with the
opposite, so V(a, b) is +V(p, n) or -V(p, n).

Switching losses and dead time are not modelled. ``u`` is held between controller
ticks (combined run); with no controller it is 0. PWM made inside ngspice is M4.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    Behavioural,
    ExternalSource,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    SignalInput,
    compose,
    physical_component,
)
from core.physical.domains import ELECTRICAL, get_domain
from core.physical.library._helpers import num, positive, real
from core.physical.library.electrical.components import Diode

ELE = get_domain(ELECTRICAL)

_PARAMETERS = (
    Parameter("threshold", 0.5, "", "gate threshold"),
    Parameter("R_on", 0.01, "Ohm", "switch on resistance"),
    Parameter("R_off", 1e6, "Ohm", "switch off resistance"),
    Parameter("Vf", 0.7, "V", "diode forward voltage"),
    Parameter("R_d", 0.01, "Ohm", "diode on resistance"),
)


def _switching_leg(
    prefix: str, p: str, n: str, out: str, ctrl: str, high_on: bool, params: Mapping[str, object]
) -> list[Primitive]:
    """One switching leg; ``high_on``: the high switch closes while ctrl > threshold."""
    r_on = num(ELE.resistance_from(positive(params, "R_on")))
    r_off = num(ELE.resistance_from(positive(params, "R_off")))
    thr = num(ELE.to_voltage(real(params, "threshold")))
    gate = f"({ELE.across_expr(ctrl)} > {thr})"
    high_r, low_r = (r_on, r_off) if high_on else (r_off, r_on)
    diode = {"Vf": real(params, "Vf"), "R_on": real(params, "R_d")}
    return [
        Behavioural(
            f"{prefix}Sh", p, out, "i", f"{ELE.across_expr(p, out)} / ({gate} ? {high_r} : {low_r})"
        ),
        Behavioural(
            f"{prefix}Sl", out, n, "i", f"{ELE.across_expr(out, n)} / ({gate} ? {low_r} : {high_r})"
        ),
        *compose(Diode, f"{prefix}Dh", {"p": out, "n": p}, diode),
        *compose(Diode, f"{prefix}Dl", {"p": n, "n": out}, diode),
    ]


@physical_component
class HalfBridge(PhysicalComponent):
    """Half-bridge leg between DC link p (+) and n (-), output out, gate u.

    u > threshold closes the high switch (out = p), otherwise the low one (out = n);
    switches R_on / R_off with antiparallel diodes.
    """

    ports = (
        Port("p", ELECTRICAL, "DC +"),
        Port("n", ELECTRICAL, "DC -"),
        Port("out", ELECTRICAL, "output"),
        SignalInput("u", "", "gate"),
    )
    parameters = _PARAMETERS
    internal = {"c": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        prims: list[Primitive] = [ExternalSource("Vc", ports.c, ports.ground, "v", "u", 0.0)]
        return prims + _switching_leg("", ports.p, ports.n, ports.out, ports.c, True, params)


@physical_component
class HBridge(PhysicalComponent):
    """H-bridge (two legs) between DC link p (+) and n (-), outputs a and b, gate u.

    u > threshold gives V(a, b) = +V(p, n), otherwise -V(p, n).
    """

    ports = (
        Port("p", ELECTRICAL, "DC +"),
        Port("n", ELECTRICAL, "DC -"),
        Port("a", ELECTRICAL, "output A"),
        Port("b", ELECTRICAL, "output B"),
        SignalInput("u", "", "gate"),
    )
    parameters = _PARAMETERS
    internal = {"c": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        p, n, c = ports.p, ports.n, ports.c
        prims: list[Primitive] = [ExternalSource("Vc", c, ports.ground, "v", "u", 0.0)]
        return (
            prims
            + _switching_leg("A", p, n, ports.a, c, True, params)
            + _switching_leg("B", p, n, ports.b, c, False, params)
        )
