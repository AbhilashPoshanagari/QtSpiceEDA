"""M1: electrical parts with a thermal port.

The instantaneous electrical power loss of the part flows as heat into its thermal
port ``H`` (a heat flow from the thermal reference into H). Wire H to a thermal mass
and / or a conduction path to an ambient Temperature Source. The electrical values do
not depend on temperature.

- ``ThermalResistor``: resistor R between p and n; heat V(p, n)^2 / R.
- ``ThermalMOSFET``: switch between drain d and source s, closed (R_on) while the
  signal input ``gate`` is above ``threshold``, open (R_off) otherwise, with a body
  diode from s to d (the library ``Diode``). Heat is V(d, s) times the total drain
  current: conduction loss of the channel and the body diode. Switching losses are
  not modelled.

The power becomes a heat flow with the Domain registry (``coupling_gain``, rule 12).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    GROUND,
    Behavioural,
    ExternalSource,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    SignalInput,
    VoltageSource,
    compose,
    physical_component,
)
from core.physical.domains import ELECTRICAL, THERMAL, get_domain
from core.physical.library._helpers import coupling_gain, num, positive, real
from core.physical.library.electrical.components import Diode, ResistorPart

ELE = get_domain(ELECTRICAL)
THE = get_domain(THERMAL)


def _heat_gain() -> str:
    """Heat-flow current per (electrical volt * amp) of power."""
    return num(coupling_gain(THE.to_current, ELE.from_voltage, ELE.from_current(1.0)))


@physical_component
class ThermalResistor(PhysicalComponent):
    """Resistor R between p and n whose power loss V^2 / R flows as heat into the
    thermal port H."""

    ports = (
        Port("p", ELECTRICAL, "+"),
        Port("n", ELECTRICAL, "-"),
        Port("H", THERMAL, "heat"),
    )
    parameters = (Parameter("R", 10.0, "Ohm", "resistance"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        r = ELE.resistance_from(positive(params, "R"))
        v = ELE.across_expr(ports.p, ports.n)
        return [
            *compose(ResistorPart, "R", {"p": ports.p, "n": ports.n}, {"R": real(params, "R")}),
            Behavioural("Q", GROUND, ports.H, "i", f"{_heat_gain()} * {v} * {v} / {num(r)}"),
        ]


@physical_component
class ThermalMOSFET(PhysicalComponent):
    """MOSFET as a signal-controlled switch from drain d to source s with a body
    diode; its conduction loss flows as heat into the thermal port H.

    Closed (R_on) while the signal gate > threshold, open (R_off) otherwise.
    """

    ports = (
        Port("d", ELECTRICAL, "drain"),
        Port("s", ELECTRICAL, "source"),
        Port("H", THERMAL, "heat"),
        SignalInput("gate", "", "gate"),
    )
    parameters = (
        Parameter("threshold", 0.5, "", "gate threshold"),
        Parameter("R_on", 0.01, "Ohm", "on resistance"),
        Parameter("R_off", 1e6, "Ohm", "off resistance"),
        Parameter("Vf", 0.7, "V", "body diode forward voltage"),
        Parameter("R_d", 0.01, "Ohm", "body diode on resistance"),
    )
    internal = {"c": ELECTRICAL, "di": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        r_on = num(ELE.resistance_from(positive(params, "R_on")))
        r_off = num(ELE.resistance_from(positive(params, "R_off")))
        thr = num(ELE.to_voltage(real(params, "threshold")))
        d, di, s = ports.d, ports.di, ports.s
        gate = f"({ELE.across_expr(ports.c)} > {thr})"
        v = ELE.across_expr(di, s)
        power = f"{ELE.across_expr(d, s)} * {ELE.through_expr('Vi')}"
        diode = {"Vf": real(params, "Vf"), "R_on": real(params, "R_d")}
        return [
            ExternalSource("Vc", ports.c, ports.ground, "v", "gate", 0.0),
            VoltageSource("Vi", d, di, 0.0),  # drain current
            Behavioural("Ch", di, s, "i", f"{v} / ({gate} ? {r_on} : {r_off})"),
            *compose(Diode, "Db", {"p": s, "n": di}, diode),
            Behavioural("Q", GROUND, ports.H, "i", f"{_heat_gain()} * {power}"),
        ]
