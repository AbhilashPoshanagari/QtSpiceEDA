"""B1: basic electrical components, written with the public PhysicalComponent API.

Ports are ``p`` (+) and ``n`` (-). Sign conventions are those of SPICE and LTspice:

- a voltage source sets V(p) - V(n);
- a current source drives its current from ``p`` through the source to ``n``, so it
  leaves the source at ``n``;
- the current of a passive part is positive flowing into ``p``.

Initial values (``v0`` of a capacitor, ``i0`` of an inductor) are used by runs with
``uic``; the combined run starts from the operating point and ignores them until M6.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    REFERENCE,
    Behavioural,
    Capacitor,
    CurrentSource,
    ExternalSource,
    Inductor,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    Resistor,
    SignalInput,
    VoltageSource,
    physical_component,
)
from core.physical.domains import ELECTRICAL, get_domain
from core.physical.library._helpers import (
    num,
    positive,
    pulse,
    pulse_parameters,
    real,
    sine,
    sine_parameters,
)

ELE = get_domain(ELECTRICAL)

_PORTS = (Port("p", ELECTRICAL, "+"), Port("n", ELECTRICAL, "-"))


# ---- passive parts


@physical_component(name="Resistor")
class ResistorPart(PhysicalComponent):
    """Linear resistor R between p and n."""

    ports = _PORTS
    parameters = (Parameter("R", 1e3, "Ohm", "resistance"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [Resistor("R", ports.p, ports.n, ELE.resistance_from(real(params, "R")))]


@physical_component(name="Capacitor")
class CapacitorPart(PhysicalComponent):
    """Linear capacitor C between p and n; ``v0`` is V(p) - V(n) at the start."""

    ports = _PORTS
    parameters = (
        Parameter("C", 1e-6, "F", "capacitance"),
        Parameter("v0", 0.0, "V", "initial voltage"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        c = ELE.capacitance_from(real(params, "C"))
        return [Capacitor("C", ports.p, ports.n, c, ELE.to_voltage(real(params, "v0")))]


@physical_component(name="Inductor")
class InductorPart(PhysicalComponent):
    """Linear inductor L between p and n; ``i0`` is the start current from p to n."""

    ports = _PORTS
    parameters = (
        Parameter("L", 1e-3, "H", "inductance"),
        Parameter("i0", 0.0, "A", "initial current"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        ind = ELE.inductance_from(real(params, "L"))
        return [Inductor("L", ports.p, ports.n, ind, ELE.to_current(real(params, "i0")))]


@physical_component
class Diode(PhysicalComponent):
    """Piecewise-linear diode, anode p and cathode n.

    With V = V(p) - V(n): above the forward voltage ``Vf`` it conducts through
    ``R_on``, i = (V - Vf) / R_on + Vf * G_off; below it only the leakage
    i = G_off * V flows. The current is continuous at Vf.
    """

    ports = (Port("p", ELECTRICAL, "anode"), Port("n", ELECTRICAL, "cathode"))
    parameters = (
        Parameter("Vf", 0.6, "V", "forward voltage"),
        Parameter("R_on", 0.3, "Ohm", "on resistance"),
        Parameter("G_off", 1e-8, "1/Ohm", "off conductance"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        vf = num(ELE.to_voltage(real(params, "Vf")))
        r_on = num(ELE.resistance_from(real(params, "R_on")))
        g_off = 1.0 / ELE.resistance_from(1.0 / positive(params, "G_off"))
        v = ELE.across_expr(ports.p, ports.n)
        on = f"({v} - {vf}) / {r_on} + {vf} * {num(g_off)}"
        expr = f"({v} > {vf}) ? ({on}) : ({v} * {num(g_off)})"
        return [Behavioural("D", ports.p, ports.n, "i", expr)]


@physical_component
class ControlledSwitch(PhysicalComponent):
    """Switch between p and n, closed while signal input ``ctrl`` > ``threshold``.

    Closed it is ``R_on``, open ``R_off``. ``ctrl`` is held between controller ticks;
    outside a combined run (no controller) it is 0, so the switch is open.
    """

    ports = (*_PORTS, SignalInput("ctrl", "", "control"))
    parameters = (
        Parameter("threshold", 0.5, "", "threshold"),
        Parameter("R_on", 1e-3, "Ohm", "closed resistance"),
        Parameter("R_off", 1e6, "Ohm", "open resistance"),
    )
    internal = {"c": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        r_on = num(ELE.resistance_from(real(params, "R_on")))
        r_off = num(ELE.resistance_from(real(params, "R_off")))
        thr = num(ELE.to_voltage(real(params, "threshold")))
        v = ELE.across_expr(ports.p, ports.n)
        ctrl = ELE.across_expr(ports.c)
        expr = f"{v} / (({ctrl} > {thr}) ? {r_on} : {r_off})"
        return [
            ExternalSource("Vc", ports.c, ports.ground, "v", "ctrl", 0.0),
            Behavioural("S", ports.p, ports.n, "i", expr),
        ]


# ---- sources


@physical_component
class DCVoltageSource(PhysicalComponent):
    """V(p) - V(n) = V."""

    ports = _PORTS
    parameters = (Parameter("V", 1.0, "V", "voltage"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [VoltageSource("V", ports.p, ports.n, ELE.to_voltage(real(params, "V")))]


@physical_component
class SineVoltageSource(PhysicalComponent):
    """V(p) - V(n) = offset + amplitude * sin(2*pi*freq*(t - delay) + phase), damped by
    theta after the delay (LTspice SINE)."""

    ports = _PORTS
    parameters = sine_parameters("V")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        wave = sine(params, ELE.to_voltage)
        return [VoltageSource("V", ports.p, ports.n, wave.offset, wave)]


@physical_component
class PulseVoltageSource(PhysicalComponent):
    """V(p) - V(n) is a pulse from v1 to v2 (LTspice PULSE)."""

    ports = _PORTS
    parameters = pulse_parameters("V")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        wave = pulse(params, ELE.to_voltage)
        return [VoltageSource("V", ports.p, ports.n, wave.v1, wave)]


@physical_component
class DCCurrentSource(PhysicalComponent):
    """Current I from p through the source to n."""

    ports = _PORTS
    parameters = (Parameter("I", 1e-3, "A", "current"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [CurrentSource("I", ports.p, ports.n, ELE.to_current(real(params, "I")))]


@physical_component
class SineCurrentSource(PhysicalComponent):
    """Sine current from p through the source to n (LTspice SINE)."""

    ports = _PORTS
    parameters = sine_parameters("A")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        wave = sine(params, ELE.to_current)
        return [CurrentSource("I", ports.p, ports.n, wave.offset, wave)]


@physical_component
class PulseCurrentSource(PhysicalComponent):
    """Pulse current from p through the source to n (LTspice PULSE)."""

    ports = _PORTS
    parameters = pulse_parameters("A")

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        wave = pulse(params, ELE.to_current)
        return [CurrentSource("I", ports.p, ports.n, wave.v1, wave)]


# ---- reference


@physical_component
class ElectricalReference(PhysicalComponent):
    """Electrical ground, 0 V."""

    role = REFERENCE
    ports = (Port("G", ELECTRICAL, "ground"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return []
