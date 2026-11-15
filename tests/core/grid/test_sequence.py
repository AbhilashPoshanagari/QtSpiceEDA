"""D2: sequence -> phase matrices against OpenDSS's own matrices.

tests/fixtures/grid/sequence_reference.json (scratch/phase5/make_sequence_reference.py)
holds 78 cases (26 input sets x 1, 2, 3 phases): r1 x1 r0 x0 c1 c0 in a linecode unit
and the matrices OpenDSS made of them for a Line. Inputs and matrices share that unit;
both are converted to per km with units.py (rule 17) before the comparison.

Tolerance: S4 measured at most 5.7e-14 abs / 3.8e-16 rel between the textbook rule and
OpenDSS (n >= 2; z1 only for n = 1); with the conversion to per km the largest
difference is 4.4e-16 of the matrix's largest entry. Each entry must agree within
1e-15 of it (a few float roundings).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.grid.engines.opendss import units
from core.grid.sequence import sequence_to_phase
from tests.core.grid.helpers import FIXTURES

REL_TOL = 1e-15

REFERENCE = json.loads((FIXTURES / "sequence_reference.json").read_text(encoding="utf-8"))


def _code(units_code: int) -> int:
    return units.NONE_AS if units_code == units.UNIT_NONE else units_code


def _per_km(value: float, units_code: int) -> float:
    out = units.per_unit_to_per_km(value, _code(units_code))
    assert out is not None
    return out


def _worst(ours: tuple[float, ...], theirs: tuple[float, ...]) -> float:
    scale = max(abs(v) for v in theirs) or 1.0
    return max(abs(a - b) for a, b in zip(ours, theirs, strict=True)) / scale


@pytest.mark.parametrize("case", REFERENCE["cases"], ids=lambda c: c["id"])
def test_matches_the_opendss_line_matrices(case: dict[str, Any]) -> None:
    given = REFERENCE["inputs"][case["input"]]
    seq = {k: _per_km(v, given["units"]) for k, v in given["sequence"].items()}
    n = case["phases"]
    got = sequence_to_phase(
        seq["r1"], seq["x1"], seq["r0"], seq["x0"], seq["c1"], seq["c0"], phases=n
    )
    line = case["line"]
    assert line["units"] == given["units"]
    for label, ours in (("r", got.r_matrix), ("x", got.x_matrix), ("c", got.c_matrix)):
        theirs = units.matrix_to_per_km(line[f"{label}_matrix"], _code(line["units"]))
        assert theirs is not None and len(ours) == n * n == len(theirs)
        assert _worst(ours, theirs) <= REL_TOL, f"{label} matrix"


def test_the_fixture_covers_one_two_and_three_phases() -> None:
    assert {c["phases"] for c in REFERENCE["cases"]} == {1, 2, 3}
    assert len(REFERENCE["cases"]) == 78


def test_one_phase_uses_the_positive_sequence_only() -> None:
    m = sequence_to_phase(0.2, 0.4, 0.6, 1.2, 10.0, 4.0, phases=1)
    assert (m.r_matrix, m.x_matrix, m.c_matrix) == ((0.2,), (0.4,), (10.0,))


def test_three_phase_self_and_mutual() -> None:
    m = sequence_to_phase(0.3, 0.6, 0.9, 1.8, 12.0, 6.0, phases=3)
    assert m.r_matrix == pytest.approx([0.5, 0.2, 0.2, 0.2, 0.5, 0.2, 0.2, 0.2, 0.5])
    assert m.x_matrix == pytest.approx([1.0, 0.4, 0.4, 0.4, 1.0, 0.4, 0.4, 0.4, 1.0])
    assert m.c_matrix == pytest.approx([10.0, -2.0, -2.0, -2.0, 10.0, -2.0, -2.0, -2.0, 10.0])
    assert m.r_matrix == tuple(m.r_matrix[j * 3 + i] for i in range(3) for j in range(3))


def test_zero_phases_is_refused() -> None:
    with pytest.raises(ValueError):
        sequence_to_phase(0.1, 0.1, 0.1, 0.1, 1.0, 1.0, phases=0)
