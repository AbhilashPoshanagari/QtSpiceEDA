"""OpenDSS length unit codes -> GridModel units (m, per km). Qt-free.

The only place with conversion factors (rule 17). Unit codes (OpenDSS LineUnits):
0 none, 1 mi, 2 kft, 3 km, 4 m, 5 ft, 6 in, 7 cm, 8 mm.
"""

from __future__ import annotations

from collections.abc import Iterable

UNIT_NONE = 0

METRES_PER_UNIT: dict[int, float] = {
    1: 1609.344,  # mi
    2: 304.8,  # kft
    3: 1000.0,  # km
    4: 1.0,  # m
    5: 0.3048,  # ft
    6: 0.0254,  # in
    7: 0.01,  # cm
    8: 0.001,  # mm
}

UNIT_NAMES: dict[int, str] = {
    0: "none",
    1: "mi",
    2: "kft",
    3: "km",
    4: "m",
    5: "ft",
    6: "in",
    7: "cm",
    8: "mm",
}

NONE_AS = 4
"""A line with no length unit (code 0) is read as metres; the extractor warns.
Length and impedance share the unit, so the total impedance does not change."""


def metres_per_unit(code: int) -> float | None:
    """Metres in one unit of ``code``; None for 0 (none) or an unknown code."""
    return METRES_PER_UNIT.get(int(code))


def length_to_m(value: float, code: int) -> float | None:
    m = metres_per_unit(code)
    return None if m is None else value * m


def per_unit_to_per_km(value: float, code: int) -> float | None:
    """A per-length value (ohm/unit, nF/unit) converted to per km."""
    m = metres_per_unit(code)
    return None if m is None else value * 1000.0 / m


def matrix_to_per_km(values: Iterable[float], code: int) -> tuple[float, ...] | None:
    m = metres_per_unit(code)
    if m is None:
        return None
    factor = 1000.0 / m
    return tuple(float(v) * factor for v in values)
