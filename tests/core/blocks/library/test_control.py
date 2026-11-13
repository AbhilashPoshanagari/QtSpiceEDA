"""M4: discrete PI and PID blocks."""

from __future__ import annotations

import math

import pytest

from core.blocks.decorator import spec_of
from core.blocks.diagram import Diagram
from core.blocks.engine import BlockError, ExecutionEngine
from core.blocks.library import builtin_blocks, builtin_specs
from core.blocks.library.control import DiscretePI, DiscretePID

DT = 1e-3


def _run(ctrl: DiscretePI | DiscretePID, errors: list[float], dt: float = DT) -> list[float]:
    """The outputs the engine would see: output() before each step(e)."""
    out = []
    for e in errors:
        out.append(ctrl.output())
        ctrl.step(e, dt)
    return out


def test_specs() -> None:
    specs = {s.name: s for s in builtin_specs()}
    pi, pid = specs["DiscretePI"], specs["DiscretePID"]
    assert pi.stateful and pi.inputs == ["e"] and pi.outputs == ["y"]
    assert pi.time_args == ["dt"]
    assert pi.settings == {
        "kp": 1.0,
        "ki": 1.0,
        "ts": 0.0,
        "u_min": None,
        "u_max": None,
        "anti_windup": "clamping",
        "kb": 1.0,
        "initial": 0.0,
    }
    assert set(pid.settings) == set(pi.settings) | {"kd", "n"}
    assert spec_of(DiscretePI) is not None


def test_pi_step_response_is_the_discrete_formula() -> None:
    kp, ki = 2.0, 50.0
    out = _run(DiscretePI(kp=kp, ki=ki), [1.0] * 6)
    # u[k] = kp + ki * k * DT, seen one step later
    assert out == pytest.approx([0.0] + [kp + ki * k * DT for k in range(5)])


def test_sample_time_holds_the_output() -> None:
    out = _run(DiscretePI(kp=0.0, ki=10.0, ts=3 * DT), [1.0] * 10)
    ts = 3 * DT
    assert out == pytest.approx(
        [0.0, 0.0, 0.0, 0.0, ts * 10, ts * 10, ts * 10, 2 * ts * 10, 2 * ts * 10, 2 * ts * 10]
    )


def test_sample_time_must_be_a_multiple_of_the_step() -> None:
    with pytest.raises(ValueError, match="whole multiple"):
        DiscretePI(ts=2.5 * DT).step(1.0, DT)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kp": "x"}, "kp must be a finite number"),
        ({"ts": -1.0}, "ts must not be negative"),
        ({"u_min": 1.0, "u_max": 0.0}, "is above u_max"),
        ({"anti_windup": "magic"}, "anti_windup must be one of"),
        ({"ki": math.nan}, "ki must be a finite number"),
    ],
)
def test_bad_settings(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DiscretePI(**kwargs)  # type: ignore[arg-type]


def test_output_limits() -> None:
    out = _run(DiscretePI(kp=10.0, ki=0.0, u_min=-1.0, u_max=2.0), [1.0, -1.0, 0.1])
    assert out == [0.0, 2.0, -1.0]
    assert DiscretePI(initial=5.0, u_max=1.0).output() == 1.0


def _windup(mode: str) -> tuple[float, int]:
    """Integrator after 100 saturated steps, then the steps until u leaves the limit."""
    ctrl = DiscretePI(kp=1.0, ki=100.0, u_max=1.0, anti_windup=mode, kb=100.0)
    _run(ctrl, [5.0] * 100)  # saturated the whole time
    windup = ctrl.i
    for k in range(1, 10000):
        ctrl.step(-0.5, DT)
        if ctrl.output() < 1.0:
            return windup, k
    raise AssertionError("never left the limit")


def test_clamping_stops_the_integrator_while_limited() -> None:
    windup, steps = _windup("clamping")
    assert windup == 0.0  # kp * e = 5 is past the limit from the first step on
    assert steps <= 2


def test_back_calculation_bleeds_the_integrator() -> None:
    windup, steps = _windup("back-calculation")
    assert windup < 1.0  # i settles near u_max - kp*e = -4
    assert steps <= 2


def test_no_anti_windup_winds_up() -> None:
    windup, steps = _windup("none")
    assert windup == pytest.approx(5.0 * 100 * DT * 100)
    assert steps > 50


def test_clamping_integrates_back_out_of_the_limit() -> None:
    ctrl = DiscretePI(kp=0.0, ki=1000.0, u_max=1.0)
    _run(ctrl, [1.0] * 50)
    i_held = ctrl.i
    ctrl.step(-1.0, DT)  # e now pulls v back: integrate
    assert ctrl.i == pytest.approx(i_held - 1000.0 * DT)


def test_pid_derivative_of_a_ramp_is_kd_times_its_slope() -> None:
    kd, n, slope = 0.5, 50.0, 3.0
    ctrl = DiscretePID(kp=0.0, ki=0.0, kd=kd, n=n)
    errors = [slope * k * DT for k in range(2000)]
    out = _run(ctrl, errors)
    assert out[-1] == pytest.approx(kd * slope, rel=1e-3)
    assert out[1] == 0.0  # no derivative kick on the first sample


def test_pid_without_kd_is_the_pi() -> None:
    errs = [1.0, 0.5, -0.2, 0.7, 0.0, 2.0]
    pi = _run(DiscretePI(kp=1.5, ki=20.0, u_max=1.2), errs)
    pid = _run(DiscretePID(kp=1.5, ki=20.0, kd=0.0, u_max=1.2), errs)
    assert pid == pytest.approx(pi)


def test_pid_filter_must_be_stable() -> None:
    with pytest.raises(ValueError, match="below 2"):
        DiscretePID(n=3000.0).step(1.0, DT)


def test_pi_in_a_diagram_with_a_bad_setting_is_a_block_error() -> None:
    specs = {s.name: s for s in builtin_specs()}
    d = Diagram(specs.get)
    d.add_block("Constant", block_id="c")
    d.add_block("DiscretePI", block_id="pi", settings={"ts": 1.5 * DT})
    d.add_block("Scope", block_id="s")
    d.add_wire("c", "y", "pi", "e")
    d.add_wire("pi", "y", "s", "u")
    engine = ExecutionEngine(builtin_blocks())
    engine.compile(d)
    with pytest.raises(BlockError, match="whole multiple"):
        engine.run(0.01, DT)


def test_pi_closes_a_first_order_loop_in_the_engine() -> None:
    """Plant y' = (u - y)/tau (an Integrator); the PI drives it to the setpoint."""
    specs = {s.name: s for s in builtin_specs()}
    d = Diagram(specs.get)
    d.add_block("Constant", block_id="r", settings={"value": 2.0})
    d.add_block("Sum", block_id="err", settings={"signs": "+-"})
    d.add_block("DiscretePI", block_id="pi", settings={"kp": 2.0, "ki": 20.0})
    d.add_block("Sum", block_id="dy", settings={"signs": "+-"})
    d.add_block("Gain", block_id="tau", settings={"k": 1 / 0.1})
    d.add_block("Integrator", block_id="y")
    d.add_block("Scope", block_id="s", settings={"label": "y"})
    d.add_wire("r", "y", "err", "a")
    d.add_wire("y", "y", "err", "b")
    d.add_wire("err", "y", "pi", "e")
    d.add_wire("pi", "y", "dy", "a")
    d.add_wire("y", "y", "dy", "b")
    d.add_wire("dy", "y", "tau", "u")
    d.add_wire("tau", "y", "y", "u")
    d.add_wire("y", "y", "s", "u")
    engine = ExecutionEngine(builtin_blocks())
    engine.compile(d)
    y = engine.run(2.0, DT).vectors["y"]
    assert float(y[-1]) == pytest.approx(2.0, abs=1e-6)
