from __future__ import annotations

import pytest

from core.grid.engines.opendss import units

# (code, metres in one unit): exact definitions of each unit.
EXACT = [
    (1, 1609.344),  # international mile
    (2, 304.8),  # 1000 international feet
    (3, 1000.0),
    (4, 1.0),
    (5, 0.3048),
    (6, 0.0254),
    (7, 0.01),
    (8, 0.001),
]


@pytest.mark.parametrize(("code", "metres"), EXACT)
def test_every_unit_code(code: int, metres: float) -> None:
    assert units.metres_per_unit(code) == pytest.approx(metres, rel=1e-15)
    assert units.length_to_m(2.5, code) == pytest.approx(2.5 * metres, rel=1e-15)
    # 1 ohm per unit is 1000 / metres ohm per km.
    assert units.per_unit_to_per_km(1.0, code) == pytest.approx(1000.0 / metres, rel=1e-15)
    assert units.matrix_to_per_km([1.0, 2.0], code) == pytest.approx(
        (1000.0 / metres, 2000.0 / metres), rel=1e-15
    )


def test_all_codes_have_names() -> None:
    assert sorted(units.UNIT_NAMES) == list(range(9))
    assert sorted(units.METRES_PER_UNIT) == list(range(1, 9))


@pytest.mark.parametrize("code", [0, 9, -1])
def test_none_and_unknown_codes_give_none(code: int) -> None:
    assert units.metres_per_unit(code) is None
    assert units.length_to_m(1.0, code) is None
    assert units.per_unit_to_per_km(1.0, code) is None
    assert units.matrix_to_per_km([1.0], code) is None


def test_ohm_per_mile_to_ohm_per_km() -> None:
    # 0.3465 ohm/mi (IEEE 13 mtx601 r11) = 0.215305... ohm/km
    assert units.per_unit_to_per_km(0.3465, 1) == pytest.approx(0.3465 / 1.609344)


def test_none_is_read_as_metres() -> None:
    assert units.NONE_AS == 4
