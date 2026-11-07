"""C2: the CoSimulator (ngspice + v0.1 engine, fixed controller tick)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np
import pytest

from core.blocks.diagram import DiagramError
from core.blocks.engine import BlockError, ExecutionEngine
from core.blocks.library import builtin_blocks
from core.physical.cosim import CoSimError, CoSimulator, node_of
from tests.fixtures.physical.components import speed_loop, speed_loop_reference
from tests.fresh import in_fresh_process

J, B, KP, W_SET = 0.01, 0.001, 1.0, 100.0


def _engine(**kwargs: float) -> tuple[ExecutionEngine, object]:
    d, reg = speed_loop(**kwargs)
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    return engine, reg


@dataclass
class Outcome:
    times: list[float] = field(default_factory=list)
    w: list[float] = field(default_factory=list)
    tau: list[float] = field(default_factory=list)
    physical: dict[str, list[float]] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    vectors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0


def _run(ts: float, t_end: float, kp: float = KP, stop_after: float = -1.0) -> Outcome:
    engine, reg = _engine(J=J, b=B, kp=kp, w_set=W_SET)
    cosim = CoSimulator(engine, reg)  # type: ignore[arg-type]
    out = Outcome()
    if stop_after >= 0:
        threading.Timer(stop_after, cosim.stop).start()
    started = time.monotonic()
    try:
        run = cosim.run(t_end, ts)
    except (CoSimError, BlockError, DiagramError) as err:
        out.error = f"{type(err).__name__}: {err}"
        out.seconds = time.monotonic() - started - max(stop_after, 0.0)
        return out
    out.seconds = time.monotonic() - started
    out.times = list(run.signal.vectors["time"])
    out.w = list(run.signal.vectors["w"])
    out.tau = list(run.signal.vectors["tau"])
    out.physical = {k: list(v.real) for k, v in run.physical.vectors.items()}
    out.vectors = dict(run.mapped.vectors)
    out.units = dict(run.mapped.units)
    out.warnings = run.warnings
    return out


@pytest.mark.ngspice
def test_p_speed_controller_matches_the_reference() -> None:
    ts, t_end = 1e-3, 0.1
    out = in_fresh_process(_run, ts, t_end)
    assert out.error == ""
    assert out.warnings == []
    ticks = round(t_end / ts) + 1
    np.testing.assert_allclose(out.times, np.arange(ticks) * ts, rtol=0, atol=1e-12)
    w_ref, tau_ref = speed_loop_reference(J, B, KP, W_SET, ts, ticks)
    w, tau = np.asarray(out.w), np.asarray(out.tau)
    # Within 1 % of the exact zero-order-hold answer over the whole run ...
    assert np.max(np.abs(w - w_ref)) < 0.01 * W_SET
    assert np.max(np.abs(tau - tau_ref)) < 0.01 * np.max(np.abs(tau_ref))
    # ... and it settles at the set speed (P control: kp / (kp + b) * w_set).
    w_ss = KP / (KP + B) * W_SET
    assert w[-1] == pytest.approx(w_ss, rel=0.01)
    assert w[-1] == pytest.approx(W_SET, rel=0.01)
    # Much tighter in fact: ngspice integrates the inertia almost exactly.
    assert np.max(np.abs(w - w_ref)) < 1e-3 * W_SET


@pytest.mark.ngspice
def test_tau_is_held_between_ticks_with_one_sample_of_delay() -> None:
    out = in_fresh_process(_run, 1e-3, 0.01)
    t = np.asarray(out.physical["time"])
    w_node = np.asarray(out.physical[out.vectors["inertia.A.w"]])
    # tau_0 = kp * w_set is applied over [0, Ts): w rises as a first-order step.
    a = np.exp(-B * t / J)
    first = t <= 1e-3
    expected = (1 - a[first]) * KP * W_SET / B
    np.testing.assert_allclose(w_node[first], expected, rtol=1e-3, atol=1e-6)
    # The sensor read at tick k is w(k*Ts): no sample of delay on the sensing side.
    tick_w = np.interp(np.asarray(out.times), t, w_node)
    np.testing.assert_allclose(out.w, tick_w, rtol=1e-9, atol=1e-9)


@pytest.mark.ngspice
def test_stop_ends_the_run_within_a_second() -> None:
    out = in_fresh_process(_run, 1e-5, 100.0, KP, 0.5)
    assert out.error == "CoSimError: run stopped"
    assert out.seconds < 1.0


def _raising_run() -> str:
    engine, reg = _engine()
    cosim = CoSimulator(engine, reg)  # type: ignore[arg-type]
    # Make the controller fail at the first tick: a non-number into the torque input.
    engine._order[[n.id for n in engine._order].index("kp")].settings["k"] = "x"
    try:
        cosim.run(0.01, 1e-3)
    except BlockError as err:
        return str(err)
    return "no error"


@pytest.mark.ngspice
def test_a_block_error_stops_the_run_and_names_the_block() -> None:
    message = in_fresh_process(_raising_run)
    assert message.startswith("block kp:")


def test_ts_below_ten_microseconds_is_refused() -> None:
    engine, reg = _engine()
    with pytest.raises(DiagramError, match="below the smallest"):
        CoSimulator(engine, reg).run(0.01, 5e-6)  # type: ignore[arg-type]


def test_the_deck_has_external_sources_and_sensors() -> None:
    engine, reg = _engine()
    mapped = CoSimulator(engine, reg).map(0.1, 1e-3)  # type: ignore[arg-type]
    assert "I_motor_I" in mapped.deck and "external" in mapped.deck
    assert ".tran 0.001 0.10200000000000001 0 0.001\n" in mapped.deck
    (ext,) = mapped.external_inputs
    assert (ext.block, ext.signal, ext.kind, ext.domain) == ("motor", "tau", "i", "rotational")
    assert node_of(mapped.vectors["sensor.w"]) == "s_sensor_w"


def test_node_of() -> None:
    assert node_of("V(rotational_1)") == "rotational_1"
    assert node_of("time") == "time"


def _held_force_with_nonzero_initial() -> float:
    from tests.fixtures.physical.components import new_diagram, wire

    d, reg = new_diagram()
    d.add_block("Solver", block_id="solver")
    d.add_block("TransRef", block_id="ref")
    d.add_block("Damper", settings={"b": 4.0}, block_id="damper")
    d.add_block("ForceInput", block_id="push")  # initial 0.5 N (the deck's dc value)
    d.add_block("Constant", settings={"value": 2.0}, block_id="f")
    wire(d, "solver.port", "ref.V")
    wire(d, "push.A", "damper.A")
    wire(d, "push.B", "ref.V")
    wire(d, "damper.B", "ref.V")
    d.add_wire("f", "y", "push", "F")
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    run = CoSimulator(engine, reg).run(0.01, 1e-3)
    return float(run.physical.vectors[run.mapped.vectors["damper.A.v"]].real[-1])


@pytest.mark.ngspice
def test_external_source_with_a_nonzero_initial_carries_exactly_the_held_value() -> None:
    # ngspice adds the `dc` value of an external source to the callback's value.
    assert in_fresh_process(_held_force_with_nonzero_initial) == pytest.approx(2.0 / 4.0)
