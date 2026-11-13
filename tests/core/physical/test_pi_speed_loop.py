"""M4: a discrete PI speed loop on a DC motor, with a saturated drive voltage.

Plant: the M1 DC motor fed by a controlled voltage source limited to +-12 V. The PI
cancels the mechanical pole (ki / kp = k^2 / (J R)), so without limits the loop is
first order and does not overshoot. A 0 -> 150 rad/s step saturates the voltage for
tens of ms: with clamping or back-calculation the speed still does not overshoot;
with no anti-windup the wound-up integrator makes it overshoot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks
from core.physical.cosim import CoSimulator
from tests.fixtures.physical.library import new_diagram, wire
from tests.fresh import in_fresh_process

MOTOR = {"R": 1.0, "L": 1e-3, "k": 0.05, "J": 1e-4, "b": 1e-5}
TS = 100e-6
W_REF = 150.0  # rad/s; needs about 7.5 V, the drive gives at most 12 V
V_MAX = 12.0
KP = 0.2  # V per rad/s
KI = KP * MOTOR["k"] ** 2 / (MOTOR["J"] * MOTOR["R"])  # zero on the mechanical pole
T_END = 0.25


@dataclass
class Speed:
    t: list[float] = field(default_factory=list)
    w: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _run(anti_windup: str, w_ref: float = W_REF) -> Speed:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="eref")
    d.add_block("MechanicalRotationalReference", block_id="rref")
    d.add_block("SolverConfiguration", block_id="solver", settings={"Ts": TS})
    d.add_block("ControlledVoltageSource", block_id="drive")
    d.add_block("DCMotor", block_id="motor", settings=MOTOR)
    d.add_block("AngularVelocitySensor", block_id="ws")
    wire(d, "solver.port", "eref.G")
    wire(d, "drive.p", "motor.p")
    wire(d, "drive.n", "eref.G")
    wire(d, "motor.n", "eref.G")
    wire(d, "motor.B", "rref.R")
    wire(d, "ws.A", "motor.A")
    wire(d, "ws.B", "rref.R")
    d.add_block("Constant", block_id="ref", settings={"value": w_ref})
    d.add_block("Sum", block_id="err", settings={"signs": "+-"})
    d.add_block(
        "DiscretePI",
        block_id="pi",
        settings={
            "kp": KP,
            "ki": KI,
            "u_min": -V_MAX,
            "u_max": V_MAX,
            "anti_windup": anti_windup,
            "kb": KI / KP,  # tracking time = integral time
        },
    )
    d.add_block("Scope", block_id="sw", settings={"label": "w"})
    d.add_block("Scope", block_id="sv", settings={"label": "v"})
    d.add_wire("ref", "y", "err", "a")
    d.add_wire("ws", "w", "err", "b")
    d.add_wire("err", "y", "pi", "e")
    d.add_wire("pi", "y", "drive", "V")
    d.add_wire("ws", "w", "sw", "u")
    d.add_wire("pi", "y", "sv", "u")
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    run = CoSimulator(engine, reg).run(T_END, TS)
    sig = run.signal.vectors
    return Speed(
        list(np.asarray(sig["time"]).real),
        list(np.asarray(sig["w"]).real),
        list(np.asarray(sig["v"]).real),
        list(run.warnings),
    )


@pytest.mark.ngspice
@pytest.mark.parametrize("mode", ["clamping", "back-calculation"])
def test_pi_speed_loop_with_anti_windup_does_not_overshoot_after_saturation(mode: str) -> None:
    got = in_fresh_process(_run, mode, timeout=300)
    assert got.warnings == []
    w, v = np.asarray(got.w), np.asarray(got.v)
    # the drive really saturates, for a good part of the rise
    saturated = np.count_nonzero(v >= V_MAX - 1e-9) * TS
    assert saturated > 0.015
    assert v.max() <= V_MAX + 1e-12
    # no overshoot, and it settles on the setpoint
    assert w.max() <= W_REF * 1.005
    assert w[-1] == pytest.approx(W_REF, rel=2e-3)


@pytest.mark.ngspice
def test_without_anti_windup_the_same_loop_overshoots() -> None:
    got = in_fresh_process(_run, "none", timeout=300)
    assert np.asarray(got.w).max() > W_REF * 1.05
    # a step too small to saturate does not overshoot: the overshoot is windup
    small = in_fresh_process(_run, "none", 20.0, timeout=300)
    assert np.asarray(small.v).max() < V_MAX
    assert np.asarray(small.w).max() <= 20.0 * 1.005
