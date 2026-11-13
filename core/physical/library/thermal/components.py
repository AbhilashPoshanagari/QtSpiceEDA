"""B4: basic thermal components (PhysicalComponent API).

Across is the absolute temperature T [K], through the heat flow Q [W]. Two-port parts
have ports A and B; their heat flow is positive flowing into A and out of B.

- The Thermal Reference is absolute zero (0 K). An ambient is a Temperature Source
  between the body side (A) and the reference (B), e.g. 298.15 K.
- A heat flow source delivers its heat into the network at A (taken from B).
- A thermal mass is a one-port element on the reference.

``T0`` of a thermal mass is its start value in a combined run (ngspice ``ic=`` with
``uic``), unless its Solver Configuration has ``steady_state`` set (M6).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    REFERENCE,
    Capacitor,
    CurrentSource,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    Resistor,
    VoltageSource,
    physical_component,
)
from core.physical.domains import THERMAL, get_domain
from core.physical.library._helpers import positive, real

THE = get_domain(THERMAL)

_PORTS = (Port("A", THERMAL), Port("B", THERMAL))


@physical_component
class ThermalMass(PhysicalComponent):
    """Thermal mass (heat capacity) C at port A; ``T0`` is its start temperature."""

    ports = (Port("A", THERMAL),)
    parameters = (
        Parameter("C", 100.0, "J/K", "heat capacity"),
        Parameter("T0", 298.15, "K", "initial temperature"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        c = THE.capacitance_from(real(params, "C"))
        return [Capacitor("C", ports.A, ports.ground, c, THE.to_voltage(real(params, "T0")))]


@physical_component
class Conduction(PhysicalComponent):
    """Conductive heat transfer through a slab: Q = k * area / thickness * (T(A) - T(B))."""

    ports = _PORTS
    parameters = (
        Parameter("k", 200.0, "W/(m*K)", "thermal conductivity"),
        Parameter("area", 1e-4, "m^2", "area"),
        Parameter("thickness", 1e-3, "m", "thickness"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        k, area = positive(params, "k"), positive(params, "area")
        r = positive(params, "thickness") / (k * area)
        return [Resistor("R", ports.A, ports.B, THE.resistance_from(r))]


@physical_component
class Convection(PhysicalComponent):
    """Convective heat transfer: Q = h * area * (T(A) - T(B))."""

    ports = _PORTS
    parameters = (
        Parameter("h", 10.0, "W/(m^2*K)", "heat transfer coefficient"),
        Parameter("area", 1e-2, "m^2", "area"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        r = 1.0 / (positive(params, "h") * positive(params, "area"))
        return [Resistor("R", ports.A, ports.B, THE.resistance_from(r))]


@physical_component
class HeatFlowSource(PhysicalComponent):
    """Constant heat flow Q, delivered into the network at A (and taken from B)."""

    ports = _PORTS
    parameters = (Parameter("Q", 1.0, "W", "heat flow"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [CurrentSource("I", ports.B, ports.A, THE.to_current(real(params, "Q")))]


@physical_component
class TemperatureSource(PhysicalComponent):
    """Ideal temperature source: T(A) - T(B) = T. With B on the Thermal Reference it is
    an ambient at absolute temperature T."""

    ports = _PORTS
    parameters = (Parameter("T", 298.15, "K", "temperature"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return [VoltageSource("V", ports.A, ports.B, THE.to_voltage(real(params, "T")))]


@physical_component
class ThermalReference(PhysicalComponent):
    """Thermal reference: absolute zero, 0 K."""

    role = REFERENCE
    ports = (Port("R", THERMAL, "reference"),)

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        return []
