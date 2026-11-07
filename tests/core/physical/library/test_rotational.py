"""B2: rotational components against analytic answers (plain ngspice, uic)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.fixtures.physical.library import add_solver, at, new_diagram, simulate, vector, wire

REF = "ref.R"


def _diagram():  # noqa: ANN202
    d, reg = new_diagram()
    d.add_block("MechanicalRotationalReference", block_id="ref")
    add_solver(d, reg, REF)
    return d, reg


@pytest.mark.ngspice
def test_inertia_and_damper_decay_with_time_constant_j_over_b() -> None:
    J, b, w0 = 0.01, 0.001, 100.0  # noqa: N806
    d, reg = _diagram()
    d.add_block("Inertia", block_id="inertia", settings={"J": J, "w0": w0})
    d.add_block("RotationalDamper", block_id="damper", settings={"b": b})
    wire(d, "inertia.A", "damper.A")
    wire(d, "damper.B", REF)
    out, res = simulate(d, reg, ".tran 10m 30 0 10m uic")
    t = res.vectors["time"].real
    w = vector(out, res, "inertia.A.w")
    tau = J / b
    assert at(t, w, tau) == pytest.approx(w0 * math.exp(-1), rel=1e-3)
    np.testing.assert_allclose(w, w0 * np.exp(-t / tau), atol=1e-3 * w0)


@pytest.mark.ngspice
def test_inertia_and_spring_oscillate_at_sqrt_k_over_j() -> None:
    J, k, w0 = 0.01, 1.0, 1.0  # noqa: N806
    d, reg = _diagram()
    d.add_block("Inertia", block_id="inertia", settings={"J": J, "w0": w0})
    d.add_block("RotationalSpring", block_id="spring", settings={"k": k})
    wire(d, "inertia.A", "spring.A")
    wire(d, "spring.B", REF)
    out, res = simulate(d, reg, ".tran 0.5m 2 0 0.5m uic")
    t = res.vectors["time"].real
    w = vector(out, res, "inertia.A.w")
    wn = math.sqrt(k / J)
    np.testing.assert_allclose(w, w0 * np.cos(wn * t), atol=5e-3 * w0)
    crossing = t[np.argmax(w < 0)]
    assert crossing == pytest.approx(math.pi / (2 * wn), rel=5e-3)


@pytest.mark.ngspice
def test_torque_source_spins_up_inertia_against_damper() -> None:
    J, b, tq = 0.01, 0.1, 0.5  # noqa: N806
    d, reg = _diagram()
    d.add_block("Inertia", block_id="inertia", settings={"J": J})
    d.add_block("RotationalDamper", block_id="damper", settings={"b": b})
    d.add_block("TorqueSource", block_id="motor", settings={"tau": tq})
    wire(d, "inertia.A", "damper.A")
    wire(d, "motor.A", "inertia.A")
    wire(d, "damper.B", REF)
    wire(d, "motor.B", REF)
    out, res = simulate(d, reg, ".tran 1m 1 0 1m uic")
    t = res.vectors["time"].real
    w = vector(out, res, "inertia.A.w")
    np.testing.assert_allclose(w, tq / b * (1 - np.exp(-t * b / J)), atol=2e-3 * tq / b)


@pytest.mark.ngspice
def test_speed_source_sets_the_relative_speed() -> None:
    d, reg = _diagram()
    d.add_block("AngularVelocitySource", block_id="src", settings={"w": 42.0})
    d.add_block("RotationalDamper", block_id="damper")
    wire(d, "src.A", "damper.A")
    wire(d, "src.B", REF)
    wire(d, "damper.B", REF)
    out, res = simulate(d, reg, ".tran 1m 10m")
    assert vector(out, res, "src.A.w")[-1] == pytest.approx(42.0, rel=1e-9)
