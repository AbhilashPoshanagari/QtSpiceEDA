"""Physical domains and their mapping onto voltage and current. Qt-free.

This is the ONLY module that knows how a physical domain maps onto a circuit
(architecture rule 12). Everything else asks the registry:

    rot = DOMAINS[ROTATIONAL]
    r = rot.resistance_from(b)      # R = 1/b, the mapper never writes "1/b" itself

| Domain        | Across -> V | Through -> A | C from       | R from   | L from | Reference |
| electrical    | V           | A            | capacitor    | resistor | inductor | 0 V    |
| rotational    | w [rad/s]   | tau [N*m]    | inertia J    | 1/b      | 1/k    | 0 rad/s   |
| translational | v [m/s]     | F [N]        | mass m       | 1/b      | 1/k    | 0 m/s     |
| thermal       | T [K]       | Q [W]        | thermal mass | R [K/W]  | -      | 0 K       |

Sign convention: the through variable is positive flowing into port A and out of
port B of a component, exactly like the current of a SPICE two-terminal element
flowing from its first node to its second. With across -> V and through -> A both
identity maps in SI, a positive across difference A - B over a dissipative element
gives a positive through quantity into A.

Values are SI internally. Display units (rpm, degC, mm, ...) are for showing and
entering numbers only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

ELECTRICAL = "electrical"
ROTATIONAL = "rotational"
TRANSLATIONAL = "translational"
THERMAL = "thermal"

ANY_DOMAIN = "any"
"""Wildcard port domain, only for the Solver Configuration block: its port joins a
network of any domain and takes the domain of the node it is wired to. It is not a
domain: ``ANY_DOMAIN not in DOMAINS``."""


@dataclass(frozen=True)
class DisplayUnit:
    """A unit for showing a quantity: ``si = value * scale + offset``."""

    name: str
    scale: float = 1.0
    offset: float = 0.0

    def to_si(self, value: float) -> float:
        return value * self.scale + self.offset

    def from_si(self, value: float) -> float:
        return (value - self.offset) / self.scale


@dataclass(frozen=True)
class Quantity:
    """An across, through or integrated-across quantity of a domain.

    ``display_units[0]`` is always the SI unit itself.
    """

    name: str
    """Short name used in vector names, e.g. ``w`` in ``motor.shaft.w``."""
    label: str
    unit: str
    display_units: tuple[DisplayUnit, ...]

    def display_unit(self, name: str) -> DisplayUnit | None:
        return next((u for u in self.display_units if u.name == name), None)

    def _unit(self, name: str) -> DisplayUnit:
        unit = self.display_unit(name)
        if unit is None:
            raise KeyError(f"{self.label} has no display unit {name!r}")
        return unit

    def to_si(self, value: float, unit: str) -> float:
        return self._unit(unit).to_si(value)

    def from_si(self, value: float, unit: str) -> float:
        return self._unit(unit).from_si(value)


@dataclass(frozen=True)
class ParameterMap:
    """How one circuit element value (C, R or L) comes from a physical parameter."""

    element: str
    """"C", "R" or "L"."""
    parameter: str
    """Usual symbol of the physical parameter, e.g. ``J`` or ``b``."""
    label: str
    unit: str
    """SI unit of the physical parameter."""
    inverse: bool = False
    """True: element value = 1 / parameter (R = 1/b, L = 1/k)."""

    def value(self, parameter: float) -> float:
        if not math.isfinite(parameter) or parameter <= 0.0:
            raise ValueError(
                f"{self.label} {self.parameter} must be a positive finite number, not {parameter!r}"
            )
        return 1.0 / parameter if self.inverse else parameter


@dataclass(frozen=True)
class Domain:
    """One physical domain: its quantities, C/R/L mapping, reference and colour."""

    name: str
    label: str
    across: Quantity
    through: Quantity
    capacitance: ParameterMap
    resistance: ParameterMap
    inductance: ParameterMap | None
    color: str
    integral: Quantity | None = None
    """Integral of the across variable (angle, position), made inside the network."""
    reference: float = 0.0
    """Across value of the domain's reference (0 V, 0 rad/s, 0 m/s, 0 K)."""

    # ---- across/through <-> voltage/current (identity in SI, kept in one place)

    def to_voltage(self, across: float) -> float:
        return float(across)

    def from_voltage(self, voltage: float) -> float:
        return float(voltage)

    def to_current(self, through: float) -> float:
        return float(through)

    def from_current(self, current: float) -> float:
        return float(current)

    # ---- element values

    @property
    def has_inductance(self) -> bool:
        return self.inductance is not None

    def capacitance_from(self, parameter: float) -> float:
        return self.capacitance.value(parameter)

    def resistance_from(self, parameter: float) -> float:
        return self.resistance.value(parameter)

    def inductance_from(self, parameter: float) -> float:
        if self.inductance is None:
            raise ValueError(f"the {self.name} domain has no inductance element")
        return self.inductance.value(parameter)

    # ---- quantities

    def quantities(self) -> list[Quantity]:
        qs = [self.across, self.through]
        if self.integral is not None:
            qs.append(self.integral)
        return qs

    def quantity(self, name: str) -> Quantity | None:
        return next((q for q in self.quantities() if q.name == name), None)


class DomainRegistry:
    """A fixed set of domains, looked up by name. Immutable after construction."""

    def __init__(self, domains: Iterable[Domain]) -> None:
        by_name: dict[str, Domain] = {}
        for d in domains:
            if d.name in by_name:
                raise ValueError(f"domain {d.name!r} is defined twice")
            by_name[d.name] = d
        self._domains = by_name

    def get(self, name: str) -> Domain | None:
        return self._domains.get(name)

    def __getitem__(self, name: str) -> Domain:
        try:
            return self._domains[name]
        except KeyError:
            raise KeyError(f"unknown physical domain {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._domains

    def __iter__(self) -> Iterator[str]:
        return iter(self._domains)

    def names(self) -> list[str]:
        return list(self._domains)

    def domains(self) -> list[Domain]:
        return list(self._domains.values())


def _si(unit: str) -> DisplayUnit:
    return DisplayUnit(unit)


def builtin_domains() -> DomainRegistry:
    """A new registry with the four v0.3 domains."""
    electrical = Domain(
        name=ELECTRICAL,
        label="Electrical",
        across=Quantity("v", "voltage", "V", (_si("V"), DisplayUnit("mV", 1e-3))),
        through=Quantity("i", "current", "A", (_si("A"), DisplayUnit("mA", 1e-3))),
        capacitance=ParameterMap("C", "C", "capacitance", "F"),
        resistance=ParameterMap("R", "R", "resistance", "Ohm"),
        inductance=ParameterMap("L", "L", "inductance", "H"),
        color="#2b6cb0",
    )
    rotational = Domain(
        name=ROTATIONAL,
        label="Mechanical rotational",
        across=Quantity(
            "w",
            "angular velocity",
            "rad/s",
            (_si("rad/s"), DisplayUnit("rpm", 2.0 * math.pi / 60.0)),
        ),
        through=Quantity("tau", "torque", "N*m", (_si("N*m"),)),
        integral=Quantity("phi", "angle", "rad", (_si("rad"), DisplayUnit("deg", math.pi / 180))),
        capacitance=ParameterMap("C", "J", "inertia", "kg*m^2"),
        resistance=ParameterMap("R", "b", "rotational damping", "N*m*s/rad", inverse=True),
        inductance=ParameterMap("L", "k", "rotational stiffness", "N*m/rad", inverse=True),
        color="#2f855a",
    )
    translational = Domain(
        name=TRANSLATIONAL,
        label="Mechanical translational",
        across=Quantity("v", "velocity", "m/s", (_si("m/s"), DisplayUnit("mm/s", 1e-3))),
        through=Quantity("F", "force", "N", (_si("N"),)),
        integral=Quantity("x", "position", "m", (_si("m"), DisplayUnit("mm", 1e-3))),
        capacitance=ParameterMap("C", "m", "mass", "kg"),
        resistance=ParameterMap("R", "b", "damping", "N*s/m", inverse=True),
        inductance=ParameterMap("L", "k", "stiffness", "N/m", inverse=True),
        color="#b7791f",
    )
    thermal = Domain(
        name=THERMAL,
        label="Thermal",
        across=Quantity("T", "temperature", "K", (_si("K"), DisplayUnit("degC", 1.0, 273.15))),
        through=Quantity("Q", "heat flow", "W", (_si("W"),)),
        capacitance=ParameterMap("C", "C", "thermal mass", "J/K"),
        resistance=ParameterMap("R", "R", "thermal resistance", "K/W"),
        inductance=None,
        color="#c53030",
    )
    return DomainRegistry([electrical, rotational, translational, thermal])


DOMAINS = builtin_domains()
"""The built-in domains. Immutable: frozen Domains in a registry without setters."""


def get_domain(name: str) -> Domain:
    """``DOMAINS[name]``; raises KeyError for an unknown domain."""
    return DOMAINS[name]
