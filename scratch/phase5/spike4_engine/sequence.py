"""S4 c) Textbook sequence -> phase conversion (scratch prototype of core/grid/sequence.py).

For an n-phase transposed line given z1 and z0 (per unit length):
    zs = (2 z1 + z0) / 3      self impedance (diagonal)
    zm = (z0 - z1) / 3        mutual impedance (off-diagonal)
and the same for the shunt capacitance (c1, c0 -> cs, cm). Units are whatever the
inputs are in; this file never converts units (rule 17).
"""

from __future__ import annotations


def phase_matrix(s1: float, s0: float, n: int) -> list[float]:
    """Row-major n x n matrix with self (2 s1 + s0) / 3 and mutual (s0 - s1) / 3."""
    self_ = (2.0 * s1 + s0) / 3.0
    mutual = (s0 - s1) / 3.0
    return [self_ if i == j else mutual for i in range(n) for j in range(n)]


def phase_matrices(seq: dict[str, float], n: int) -> dict[str, list[float]]:
    return {
        "r_matrix": phase_matrix(seq["r1"], seq["r0"], n),
        "x_matrix": phase_matrix(seq["x1"], seq["x0"], n),
        "c_matrix": phase_matrix(seq["c1"], seq["c0"], n),
    }
