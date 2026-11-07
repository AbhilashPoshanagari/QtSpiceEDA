"""B3: translational components against analytic answers (plain ngspice, uic)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.fixtures.physical.library import add_solver, new_diagram, simulate, vector, wire

REF = "ref.R"


def _diagram():  # noqa: ANN202
    d, reg = new_diagram()
    d.add_block("MechanicalTranslationalReference", block_id="ref")
    add_solver(d, reg, REF)
    return d, reg


@pytest.mark.ngspice
def test_mass_and_spring_oscillate_at_sqrt_k_over_m() -> None:
    m, k, v0 = 2.0, 50.0, 1.0
    d, reg = _diagram()
    d.add_block("Mass", block_id="mass", settings={"m": m, "v0": v0})
    d.add_block("TranslationalSpring", block_id="spring", settings={"k": k})
    wire(d, "mass.A", "spring.A")
    wire(d, "spring.B", REF)
    out, res = simulate(d, reg, ".tran 0.5m 4 0 0.5m uic")
    t = res.vectors["time"].real
    v = vector(out, res, "mass.A.v")
    wn = math.sqrt(k / m)
    np.testing.assert_allclose(v, v0 * np.cos(wn * t), atol=5e-3 * v0)
    # Period from the downward zero crossings.
    down = t[1:][(v[:-1] >= 0) & (v[1:] < 0)]
    assert np.diff(down).mean() == pytest.approx(2 * math.pi / wn, rel=2e-3)


@pytest.mark.ngspice
def test_force_on_mass_and_damper_settles_at_f_over_b() -> None:
    m, b, f = 1.0, 4.0, 10.0
    d, reg = _diagram()
    d.add_block("Mass", block_id="mass", settings={"m": m})
    d.add_block("TranslationalDamper", block_id="damper", settings={"b": b})
    d.add_block("ForceSource", block_id="push", settings={"F": f})
    wire(d, "mass.A", "damper.A")
    wire(d, "push.A", "mass.A")
    wire(d, "damper.B", REF)
    wire(d, "push.B", REF)
    out, res = simulate(d, reg, ".tran 1m 3 0 1m uic")
    t = res.vectors["time"].real
    v = vector(out, res, "mass.A.v")
    np.testing.assert_allclose(v, f / b * (1 - np.exp(-t * b / m)), atol=2e-3 * f / b)


@pytest.mark.ngspice
def test_velocity_source_sets_the_relative_velocity() -> None:
    d, reg = _diagram()
    d.add_block("VelocitySource", block_id="src", settings={"v": -0.25})
    d.add_block("TranslationalDamper", block_id="damper")
    wire(d, "src.A", "damper.A")
    wire(d, "src.B", REF)
    wire(d, "damper.B", REF)
    out, res = simulate(d, reg, ".tran 1m 10m")
    assert vector(out, res, "src.A.v")[-1] == pytest.approx(-0.25, rel=1e-9)
