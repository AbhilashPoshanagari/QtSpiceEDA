"""Spike 4: mass-spring-damper as R, L and C vs the analytic answer.

Each spike runs in a fresh spawned process (ngspice is global per
process). Timings are printed, never asserted."""

from __future__ import annotations

import math

import numpy as np
import pytest
import spike4_msd
from fresh import in_fresh_process

pytestmark = pytest.mark.ngspice


def test_spike4_msd_matches_analytic_step_response() -> None:
    r = in_fresh_process(spike4_msd.run)
    assert r.zeta == pytest.approx(0.2)
    assert r.wn == pytest.approx(10.0)
    assert r.max_err_x < 1e-3
    assert r.max_err_v < 1e-3
    assert r.x_final == pytest.approx(r.x_ss_analytic, rel=5e-3)
    assert r.peak_time == pytest.approx(r.peak_time_analytic, abs=1e-3)


def test_spike4_analytic_helper_has_the_right_shape() -> None:
    t = np.linspace(0, 5, 5001)
    x, v, wn, zeta, wd = spike4_msd.analytic(t, 2.0, 200.0, 8.0, 10.0)
    assert x[0] == pytest.approx(0) and v[0] == pytest.approx(0)
    assert x[-1] == pytest.approx(10.0 / 200.0, rel=1e-3)
    assert np.allclose(np.gradient(x, t)[1:-1], v[1:-1], atol=1e-4)
    assert wd == pytest.approx(wn * math.sqrt(1 - zeta**2))
