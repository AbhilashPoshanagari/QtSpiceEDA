"""F1: the Domain registry. Sign tests first, then mapping and display units."""

from __future__ import annotations

import math

import pytest

from core.physical.domains import (
    DOMAINS,
    ELECTRICAL,
    ROTATIONAL,
    THERMAL,
    TRANSLATIONAL,
    DisplayUnit,
    Domain,
    DomainRegistry,
    builtin_domains,
    get_domain,
)


def through_into_a(domain: Domain, across_a: float, across_b: float, param: float) -> float:
    """Through quantity into port A of the domain's dissipative element.

    Goes the way the mapper will: across -> voltage, Ohm's law on the mapped
    resistance (current positive into node A, out of node B), current -> through.
    """
    v_a = domain.to_voltage(across_a)
    v_b = domain.to_voltage(across_b)
    r = domain.resistance_from(param)
    return domain.from_current((v_a - v_b) / r)


# ---- one sign test per domain: positive through flows into A when across(A) > across(B)


def test_sign_electrical() -> None:
    # Resistor R = 100 Ohm, V(A) = 5 V, V(B) = 1 V: I = +40 mA into A.
    i = through_into_a(DOMAINS[ELECTRICAL], 5.0, 1.0, 100.0)
    assert i == pytest.approx(0.04)
    assert through_into_a(DOMAINS[ELECTRICAL], 1.0, 5.0, 100.0) == pytest.approx(-0.04)


def test_sign_rotational() -> None:
    # Damper b = 0.5 N*m*s/rad, w(A) = 10 rad/s, w(B) = 2 rad/s: tau = b*(wA - wB) = +4 N*m.
    tau = through_into_a(DOMAINS[ROTATIONAL], 10.0, 2.0, 0.5)
    assert tau == pytest.approx(4.0)
    assert through_into_a(DOMAINS[ROTATIONAL], 2.0, 10.0, 0.5) == pytest.approx(-4.0)


def test_sign_translational() -> None:
    # Damper b = 20 N*s/m, v(A) = 0.3 m/s, v(B) = -0.1 m/s: F = b*(vA - vB) = +8 N.
    f = through_into_a(DOMAINS[TRANSLATIONAL], 0.3, -0.1, 20.0)
    assert f == pytest.approx(8.0)
    assert through_into_a(DOMAINS[TRANSLATIONAL], -0.1, 0.3, 20.0) == pytest.approx(-8.0)


def test_sign_thermal() -> None:
    # Thermal resistance 2 K/W, T(A) = 350 K, T(B) = 300 K: Q = (TA - TB)/R = +25 W.
    q = through_into_a(DOMAINS[THERMAL], 350.0, 300.0, 2.0)
    assert q == pytest.approx(25.0)
    assert through_into_a(DOMAINS[THERMAL], 300.0, 350.0, 2.0) == pytest.approx(-25.0)


# ---- C, R, L mapping


@pytest.mark.parametrize(
    ("name", "c_param", "c", "r_param", "r", "l_param", "ind"),
    [
        (ELECTRICAL, 1e-6, 1e-6, 1e3, 1e3, 1e-3, 1e-3),
        (ROTATIONAL, 0.01, 0.01, 0.5, 2.0, 4.0, 0.25),  # C = J, R = 1/b, L = 1/k
        (TRANSLATIONAL, 2.0, 2.0, 20.0, 0.05, 100.0, 0.01),  # C = m, R = 1/b, L = 1/k
    ],
)
def test_c_r_l_mapping(
    name: str, c_param: float, c: float, r_param: float, r: float, l_param: float, ind: float
) -> None:
    domain = DOMAINS[name]
    assert domain.capacitance_from(c_param) == pytest.approx(c)
    assert domain.resistance_from(r_param) == pytest.approx(r)
    assert domain.inductance_from(l_param) == pytest.approx(ind)
    assert domain.has_inductance


def test_thermal_mapping_and_no_inductance() -> None:
    thermal = DOMAINS[THERMAL]
    assert thermal.capacitance_from(450.0) == pytest.approx(450.0)  # thermal mass J/K
    assert thermal.resistance_from(2.0) == pytest.approx(2.0)  # K/W
    assert not thermal.has_inductance
    assert thermal.inductance is None
    with pytest.raises(ValueError, match="no inductance"):
        thermal.inductance_from(1.0)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan])
def test_mapping_rejects_non_positive_or_non_finite(bad: float) -> None:
    with pytest.raises(ValueError):
        DOMAINS[ROTATIONAL].resistance_from(bad)


def test_parameter_maps_describe_the_physical_parameter() -> None:
    rot = DOMAINS[ROTATIONAL]
    assert rot.capacitance.parameter == "J"
    assert rot.capacitance.unit == "kg*m^2"
    assert rot.resistance.parameter == "b" and rot.resistance.inverse
    assert rot.inductance is not None and rot.inductance.parameter == "k"
    assert not DOMAINS[THERMAL].resistance.inverse


def test_voltage_and_current_mapping_is_identity_in_si() -> None:
    for domain in DOMAINS.domains():
        assert domain.to_voltage(3.5) == 3.5
        assert domain.from_voltage(3.5) == 3.5
        assert domain.to_current(-2.0) == -2.0
        assert domain.from_current(-2.0) == -2.0


# ---- quantities, units, reference, colour


def test_across_and_through_names_and_units() -> None:
    expected = {
        ELECTRICAL: ("v", "V", "i", "A"),
        ROTATIONAL: ("w", "rad/s", "tau", "N*m"),
        TRANSLATIONAL: ("v", "m/s", "F", "N"),
        THERMAL: ("T", "K", "Q", "W"),
    }
    for name, (across, au, through, tu) in expected.items():
        d = DOMAINS[name]
        assert (d.across.name, d.across.unit, d.through.name, d.through.unit) == (
            across,
            au,
            through,
            tu,
        )


def test_reference_is_zero_everywhere() -> None:
    # Thermal reference is absolute zero; ambient is a Temperature Source.
    for domain in DOMAINS.domains():
        assert domain.reference == 0.0


def test_colours_are_distinct_hex() -> None:
    colours = [d.color for d in DOMAINS.domains()]
    assert len(set(colours)) == 4
    assert all(len(c) == 7 and c.startswith("#") for c in colours)


def test_display_units_rpm_degc_mm() -> None:
    rot, thermal, trans = DOMAINS[ROTATIONAL], DOMAINS[THERMAL], DOMAINS[TRANSLATIONAL]
    assert rot.across.to_si(60.0, "rpm") == pytest.approx(2 * math.pi)
    assert rot.across.from_si(2 * math.pi, "rpm") == pytest.approx(60.0)
    assert thermal.across.to_si(25.0, "degC") == pytest.approx(298.15)
    assert thermal.across.from_si(273.15, "degC") == pytest.approx(0.0)
    assert trans.integral is not None
    assert trans.integral.name == "x"
    assert trans.integral.to_si(12.0, "mm") == pytest.approx(0.012)
    assert rot.integral is not None and rot.integral.from_si(math.pi, "deg") == pytest.approx(180)


def test_si_unit_is_always_a_display_unit() -> None:
    for domain in DOMAINS.domains():
        for q in domain.quantities():
            assert q.to_si(1.25, q.unit) == 1.25
            assert q.display_units[0].name == q.unit


def test_unknown_display_unit() -> None:
    assert DOMAINS[THERMAL].across.display_unit("degF") is None
    with pytest.raises(KeyError):
        DOMAINS[THERMAL].across.to_si(1.0, "degF")


def test_display_unit_round_trip() -> None:
    unit = DisplayUnit("degC", 1.0, 273.15)
    assert unit.from_si(unit.to_si(-40.0)) == pytest.approx(-40.0)


def test_quantity_lookup() -> None:
    rot = DOMAINS[ROTATIONAL]
    assert rot.quantity("w") is rot.across
    assert rot.quantity("tau") is rot.through
    assert rot.quantity("phi") is rot.integral
    assert rot.quantity("nope") is None
    assert DOMAINS[ELECTRICAL].integral is None


# ---- registry


def test_registry_lookup() -> None:
    assert DOMAINS.names() == [ELECTRICAL, ROTATIONAL, TRANSLATIONAL, THERMAL]
    assert ROTATIONAL in DOMAINS
    assert "hydraulic" not in DOMAINS
    assert DOMAINS.get("hydraulic") is None
    assert get_domain(THERMAL) is DOMAINS[THERMAL]
    with pytest.raises(KeyError):
        DOMAINS["hydraulic"]


def test_registry_rejects_duplicates() -> None:
    d = DOMAINS[THERMAL]
    with pytest.raises(ValueError, match="thermal"):
        DomainRegistry([d, d])


def test_builtin_domains_is_a_fresh_equal_registry() -> None:
    other = builtin_domains()
    assert other is not DOMAINS
    assert other.names() == DOMAINS.names()
    assert other[ROTATIONAL] == DOMAINS[ROTATIONAL]
