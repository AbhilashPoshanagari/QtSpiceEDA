"""C5: a combined run gives one SimulationResult."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.blocks.diagram import DiagramError
from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks
from core.netlist.model import Circuit
from core.physical.cosim import ANALYSIS, CoSimRun, CoSimulator, merge
from core.physical.mapper import MappedNetwork
from core.sim.result import SimulationResult
from tests.fixtures.physical.components import speed_loop, speed_loop_reference
from tests.fresh import in_fresh_process


def _fake_run() -> CoSimRun:
    ts = 0.1
    t = np.array([0.0, 0.03, 0.1, 0.17, 0.2, 0.2 + 1e-12, 0.25, 0.3])
    physical = SimulationResult(
        axis="time",
        vectors={"time": t, "V(rotational_1)": t * 10, "V(s_sensor_w)": t * 10, "I(v1)": t},
        units={"time": "s", "V(rotational_1)": "V", "V(s_sensor_w)": "V", "I(v1)": "A"},
        analysis="tran",
    )
    signal = SimulationResult(
        axis="time",
        vectors={"time": np.array([0.0, 0.1, 0.2]), "tau": np.array([5.0, 4.0, 3.0])},
        units={"time": "s"},
        analysis="blocks",
    )
    mapped = MappedNetwork(
        circuit=Circuit(title="x"),
        deck="",
        vectors={"inertia.A.w": "V(rotational_1)", "sensor.w": "V(s_sensor_w)"},
        units={"inertia.A.w": "rad/s", "sensor.w": "rad/s"},
    )
    return CoSimRun(signal=signal, physical=physical, mapped=mapped, ts=ts, t_end=0.2)


def test_merge_names_units_and_axis() -> None:
    result = merge(_fake_run())
    assert result.analysis == ANALYSIS
    assert result.axis == "time"
    assert list(result.vectors) == ["time", "inertia.A.w", "sensor.w", "tau"]
    assert result.units == {"time": "s", "inertia.A.w": "rad/s", "sensor.w": "rad/s"}
    # Cut at t_end (within the tick tolerance), raw ngspice names left out.
    np.testing.assert_allclose(result.vectors["time"], [0, 0.03, 0.1, 0.17, 0.2, 0.2 + 1e-12])
    np.testing.assert_allclose(result.vectors["inertia.A.w"], [0, 0.3, 1, 1.7, 2, 2])


def test_scope_traces_are_held_between_ticks() -> None:
    result = merge(_fake_run())
    np.testing.assert_array_equal(result.vectors["tau"], [5, 5, 4, 4, 3, 3])


def test_merged_result_saves_like_any_other(tmp_path: Path) -> None:
    result = _fake_run().result
    result.save_npz(tmp_path / "r.npz")
    back = SimulationResult.load_npz(tmp_path / "r.npz")
    assert list(back.vectors) == list(result.vectors)
    assert back.units == result.units
    assert "inertia.A.w [rad/s]" in result.to_csv().splitlines()[0]


def test_scope_named_like_a_physical_vector_is_refused() -> None:
    d, reg = speed_loop()
    d.set_settings("scope_w", {"label": "sensor.w"})
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    with pytest.raises(DiagramError, match="physical vector"):
        CoSimulator(engine, reg).run(0.01, 1e-3)


def _merged(ts: float, t_end: float) -> SimulationResult:
    d, reg = speed_loop()
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    return CoSimulator(engine, reg).run(t_end, ts).result


@pytest.mark.ngspice
def test_combined_run_result() -> None:
    ts, t_end = 1e-3, 0.05
    result = in_fresh_process(_merged, ts, t_end)
    assert result.analysis == ANALYSIS
    names = result.trace_names()
    for name in ("inertia.A.w", "damper.A.w", "motor.A.w", "sensor.A.w", "sensor.w", "w", "tau"):
        assert name in names
    assert result.units["inertia.A.w"] == "rad/s"
    assert result.units["sensor.w"] == "rad/s"
    t = result.vectors["time"]
    assert t[0] == 0.0 and t[-1] == pytest.approx(t_end, abs=1e-12)
    # Every tick is a point of the axis, and the Scope equals the sensor there.
    ticks = np.arange(round(t_end / ts) + 1) * ts
    at = np.searchsorted(t, ticks - 1e-12)
    np.testing.assert_allclose(t[at], ticks, atol=1e-12)
    np.testing.assert_allclose(result.vectors["w"][at], result.vectors["sensor.w"][at])
    w_ref, _tau = speed_loop_reference(0.01, 0.001, 1.0, 100.0, ts, len(ticks))
    np.testing.assert_allclose(result.vectors["inertia.A.w"][at], w_ref, atol=0.1)
