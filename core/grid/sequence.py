"""Sequence impedances -> phase matrices. Qt-free, engine-free.

The only place this conversion exists (rule 26). Inputs and outputs are in the
canonical units of GridModel (ohm/km, nF/km); this module never converts units
(rule 17: that is units.py).

For an n-phase transposed line given positive and zero sequence values s1, s0 (the same
for r, x and c), as OpenDSS makes a Line's matrices (spike S4, checked against
tests/fixtures/grid/sequence_reference.json):

- n >= 2: self = (2 s1 + s0) / 3 on the diagonal, mutual = (s0 - s1) / 3 off it;
- n = 1: s1 only (OpenDSS's Line uses z1 and c1 for a 1-phase line, although its
  LineCode reports (2 s1 + s0) / 3; the Line value is the one the solve uses).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhaseMatrices:
    """Row-major ``phases x phases`` matrices, ready for LineCode."""

    r_matrix: tuple[float, ...]
    """Series resistance in ohm/km."""
    x_matrix: tuple[float, ...]
    """Series reactance in ohm/km."""
    c_matrix: tuple[float, ...]
    """Shunt capacitance in nF/km."""


def _matrix(s1: float, s0: float, phases: int) -> tuple[float, ...]:
    if phases == 1:
        return (float(s1),)
    self_ = (2.0 * s1 + s0) / 3.0
    mutual = (s0 - s1) / 3.0
    return tuple(self_ if i == j else mutual for i in range(phases) for j in range(phases))


def sequence_to_phase(
    r1: float, x1: float, r0: float, x0: float, c1: float, c0: float, phases: int
) -> PhaseMatrices:
    """Phase matrices of a transposed ``phases``-phase line from its sequence values.

    r1, x1, r0, x0 in ohm/km and c1, c0 in nF/km; the matrices come out in the same
    units. ``phases`` must be at least 1 (a ValueError otherwise: a programming error,
    not bad user input, which validate.py reports before anything is converted).
    """
    if phases < 1:
        raise ValueError(f"phases must be >= 1, not {phases}")
    return PhaseMatrices(
        r_matrix=_matrix(r1, r0, phases),
        x_matrix=_matrix(x1, x0, phases),
        c_matrix=_matrix(c1, c0, phases),
    )
