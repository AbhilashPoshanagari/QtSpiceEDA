"""M1 / M3: half-bridge and H-bridge, switching or averaged.

Ports: ``p`` / ``n`` (DC link + / -) and the leg outputs (``out``; ``a`` and ``b``
for the H-bridge); signal input ``u``.

``model`` chooses the version (M3):

- ``"switching"`` (default): each leg is two signal-controlled switches (``R_on`` /
  ``R_off``) with antiparallel diodes (the library ``Diode``, by ``compose``). While
  ``u`` > ``threshold`` the high switch is closed and the low one open, otherwise the
  other way round (complementary, no dead time). The H-bridge drives leg ``a`` with
  ``u`` and leg ``b`` with the opposite, so V(a, b) is +V(p, n) or -V(p, n).
- ``"averaged"``: ``u`` is the duty cycle d (clamped to 0..1). A leg is a source
  d * V(p, n) behind ``R_on``, and the DC link carries d times the leg current, so
  the power taken from the link equals the power delivered. H-bridge: leg ``a`` has
  duty d, leg ``b`` 1 - d, so V(a, b) = (2d - 1) * V(p, n) on average.

Feeding the switching bridge a PWM gate and the averaged one the PWM's duty cycle
gives the same average voltage, which is what M3 checks. Switching losses and dead
time are not modelled. ``u`` is held between controller ticks (combined run); with
no controller it is 0. PWM made inside ngspice is M4.
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
    Resistor,
    SignalInput,
    VoltageSource,
    compose,
    physical_component,
)
from core.physical.domains import ELECTRICAL, get_domain
from core.physical.library._helpers import num, positive, real, text
from core.physical.library.electrical.components import Diode

ELE = get_domain(ELECTRICAL)

SWITCHING = "switching"
AVERAGED = "averaged"
MODELS = (SWITCHING, AVERAGED)

_PARAMETERS = (
    Parameter("model", SWITCHING, "", "switching or averaged"),
    Parameter("threshold", 0.5, "", "gate threshold (switching)"),
    Parameter("R_on", 0.01, "Ohm", "switch on resistance"),
    Parameter("R_off", 1e6, "Ohm", "switch off resistance (switching)"),
    Parameter("Vf", 0.7, "V", "diode forward voltage (switching)"),
    Parameter("R_d", 0.01, "Ohm", "diode on resistance (switching)"),
)


def _model(params: Mapping[str, object]) -> str:
    model = text(params, "model").strip().lower()
    if model not in MODELS:
        raise ValueError(f"model must be {' or '.join(MODELS)}, not {params['model']!r}")
    return model


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


def _averaged_leg(
    prefix: str,
    p: str,
    n: str,
    out: str,
    mid: str,
    src: str,
    duty: str,
    params: Mapping[str, object],
) -> list[Primitive]:
    """One averaged leg: out -R_on- mid -Vi- src, V(src, n) = duty * V(p, n)."""
    r_on = ELE.resistance_from(positive(params, "R_on"))
    vi = f"{prefix}Vi"
    current_in = ELE.through_expr(vi)  # into the out port
    return [
        Resistor(f"{prefix}R", out, mid, r_on),
        VoltageSource(vi, mid, src, 0.0),
        Behavioural(f"{prefix}E", src, n, "v", f"({duty}) * {ELE.across_expr(p, n)}"),
        # the link supplies duty * (current out of the leg): it flows into p
        Behavioural(f"{prefix}G", p, n, "i", f"({duty}) * (-{current_in})"),
    ]


def _duty(ctrl: str) -> str:
    return f"max(0, min(1, {ELE.across_expr(ctrl)}))"


@physical_component
class HalfBridge(PhysicalComponent):
    """Half-bridge leg between DC link p (+) and n (-), output out, controlled by u.

    model "switching": u > threshold closes the high switch (out = p), otherwise the
    low one (out = n); switches R_on / R_off with antiparallel diodes.
    model "averaged": u is the duty cycle, out = u * V(p, n) behind R_on.
    """

    ports = (
        Port("p", ELECTRICAL, "DC +"),
        Port("n", ELECTRICAL, "DC -"),
        Port("out", ELECTRICAL, "output"),
        SignalInput("u", "", "gate or duty"),
    )
    parameters = _PARAMETERS
    internal = {"c": ELECTRICAL, "m": ELECTRICAL, "s": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        prims: list[Primitive] = [ExternalSource("Vc", ports.c, ports.ground, "v", "u", 0.0)]
        if _model(params) == SWITCHING:
            return prims + _switching_leg("", ports.p, ports.n, ports.out, ports.c, True, params)
        return prims + _averaged_leg(
            "", ports.p, ports.n, ports.out, ports.m, ports.s, _duty(ports.c), params
        )


@physical_component
class HBridge(PhysicalComponent):
    """H-bridge (two legs) between DC link p (+) and n (-), outputs a and b, control u.

    model "switching": u > threshold gives V(a, b) = +V(p, n), otherwise -V(p, n).
    model "averaged": u is the duty cycle d, V(a, b) = (2d - 1) * V(p, n).
    """

    ports = (
        Port("p", ELECTRICAL, "DC +"),
        Port("n", ELECTRICAL, "DC -"),
        Port("a", ELECTRICAL, "output A"),
        Port("b", ELECTRICAL, "output B"),
        SignalInput("u", "", "gate or duty"),
    )
    parameters = _PARAMETERS
    internal = {
        "c": ELECTRICAL,
        "ma": ELECTRICAL,
        "sa": ELECTRICAL,
        "mb": ELECTRICAL,
        "sb": ELECTRICAL,
    }

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        p, n, c = ports.p, ports.n, ports.c
        prims: list[Primitive] = [ExternalSource("Vc", c, ports.ground, "v", "u", 0.0)]
        if _model(params) == SWITCHING:
            return (
                prims
                + _switching_leg("A", p, n, ports.a, c, True, params)
                + _switching_leg("B", p, n, ports.b, c, False, params)
            )
        duty = _duty(c)
        return (
            prims
            + _averaged_leg("A", p, n, ports.a, ports.ma, ports.sa, duty, params)
            + _averaged_leg("B", p, n, ports.b, ports.mb, ports.sb, f"1 - {duty}", params)
        )
