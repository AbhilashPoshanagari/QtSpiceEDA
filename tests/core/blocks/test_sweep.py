"""M7: parameter sweep (core): values, checks, combining runs, the sweep runner."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from core.blocks.runner import CANCELLED, ERROR, OK, BlockRunner, RunOutcome
from core.blocks.sweep import (
    MAX_RUNS,
    SweepRunner,
    SweepSpec,
    apply,
    check,
    combine,
    parse_values,
    run_name,
    split_run_name,
    typed,
)
from core.sim.result import SimulationResult
from tests.fixtures.physical.library import add_solver, new_diagram, wire

# ---- values and checks


def test_parse_values_takes_spice_numbers_and_separators() -> None:
    assert parse_values("1m 2m, 5m;1") == ([1e-3, 2e-3, 5e-3, 1.0], [])
    assert parse_values("  ") == ([], ["give at least one value"])
    values, problems = parse_values("1 lots 3")
    assert values == [1.0, 3.0] and "'lots' is not a number" in problems[0]
    _, problems = parse_values(" ".join(["1"] * (MAX_RUNS + 1)))
    assert "at most" in problems[0]


def _gain_diagram():
    d, _ = new_diagram()
    d.add_block("Constant", settings={"value": 2.0}, block_id="c")
    d.add_block("Gain", settings={"k": 1.0}, block_id="g")
    d.add_block("Scope", settings={"label": "y"}, block_id="s")
    d.add_wire("c", "y", "g", "u")
    d.add_wire("g", "y", "s", "u")
    return d


def test_check_reports_bad_sweeps_on_the_block() -> None:
    d = _gain_diagram()
    assert check(SweepSpec("g", "k", (1.0, 2.0)), d) == []
    found = check(SweepSpec("nope", "k", (1.0,)), d)
    assert found[0].block == "nope" and "no block" in found[0].message
    assert "no parameter" in check(SweepSpec("g", "gain", (1.0,)), d)[0].message
    assert "not a number" in check(SweepSpec("s", "label", (1.0,)), d)[0].message
    assert "no values" in check(SweepSpec("g", "k", ()), d)[0].message


def test_solver_ts_and_whole_numbers() -> None:
    d, reg = new_diagram()
    d.add_block("MechanicalRotationalReference", block_id="ref")
    add_solver(d, reg, "ref.R")
    assert "time step" in check(SweepSpec("solver", "Ts", (1e-3,)), d)[0].message
    d.add_block("PulseVoltageSource", block_id="p")
    assert "whole number" in check(SweepSpec("p", "ncycles", (1.5,)), d)[0].message
    spec = typed(d, SweepSpec("p", "ncycles", (2.0, 3.0)))
    assert spec.values == (2, 3) and all(isinstance(v, int) for v in spec.values)


def test_apply_changes_a_copy_only() -> None:
    data = _gain_diagram().to_dict()
    swept = apply(data, SweepSpec("g", "k", (3.0,)), 3.0)
    block = next(b for b in swept["blocks"] if b["id"] == "g")
    assert block["settings"]["k"] == 3.0
    assert next(b for b in data["blocks"] if b["id"] == "g")["settings"]["k"] == 1.0


def test_labels_and_run_names() -> None:
    spec = SweepSpec("inertia", "J", (1e-3, 0.002))
    assert spec.labels == ["J=0.001", "J=0.002"]
    assert run_name("inertia.A.w", 2) == "inertia.A.w@2"
    assert split_run_name("inertia.A.w@2", 3) == ("inertia.A.w", 2)
    assert split_run_name("inertia.A.w@4", 3) is None
    assert split_run_name("w@0", 3) is None
    assert split_run_name("w", 3) is None


# ---- combining runs


def _result(t: list[float], y: list[float]) -> SimulationResult:
    return SimulationResult(
        axis="time",
        vectors={"time": np.array(t), "y": np.array(y)},
        units={"time": "s", "y": "rad/s"},
        analysis="cosim",
    )


def test_combine_puts_runs_on_the_union_of_their_points() -> None:
    a = _result([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
    b = _result([0.0, 0.5, 2.0], [1.0, 2.0, 5.0])
    out = combine([a, b], ["k=1", "k=2"])
    assert out.runs == ["k=1", "k=2"] and out.analysis == "cosim"
    np.testing.assert_array_equal(out.vectors["time"], [0.0, 0.5, 1.0, 2.0])
    np.testing.assert_allclose(out.vectors["y@1"], [0.0, 5.0, 10.0, 20.0])
    np.testing.assert_allclose(out.vectors["y@2"], [1.0, 2.0, 3.0, 5.0])
    assert out.units["y@2"] == "rad/s"
    assert out.trace_names() == ["y@1", "y@2"]


def test_combine_needs_the_same_axis() -> None:
    other = SimulationResult(axis="f", vectors={"f": np.array([1.0]), "y": np.array([1.0])})
    with pytest.raises(ValueError):
        combine([_result([0.0], [0.0]), other], ["a", "b"])
    with pytest.raises(ValueError):
        combine([], [])


def test_run_labels_survive_npz(tmp_path: Path) -> None:
    out = combine([_result([0.0, 1.0], [0.0, 1.0])], ["J=1"])
    out.save_npz(tmp_path / "r.npz")
    back = SimulationResult.load_npz(tmp_path / "r.npz")
    assert back.runs == ["J=1"]
    _result([0.0], [0.0]).save_npz(tmp_path / "single.npz")
    assert SimulationResult.load_npz(tmp_path / "single.npz").runs == []


# ---- the sweep runner (fake runs)


class _Fake:
    def __init__(self, diagram: dict, fail: bool = False, gate: threading.Event | None = None):
        self.diagram = diagram
        self.fail = fail
        self.gate = gate
        self.cancelled = threading.Event()

    def run(self) -> RunOutcome:
        if self.gate is not None:
            self.gate.set()
            self.cancelled.wait(5)
            return RunOutcome(CANCELLED, message="stopped")
        if self.fail:
            return RunOutcome(ERROR, message="ValueError: J must be positive", console=["x"])
        k = next(b for b in self.diagram["blocks"] if b["id"] == "g")["settings"]["k"]
        return RunOutcome(OK, _result([0.0, 1.0], [k, 2 * k]), console=[f"ran {k}"])

    def cancel(self) -> None:
        self.cancelled.set()


def test_sweep_runner_runs_each_value_in_turn() -> None:
    made: list[_Fake] = []
    seen: list[tuple[int, int, str]] = []

    def make(diagram: dict) -> _Fake:
        made.append(_Fake(diagram))
        return made[-1]

    spec = SweepSpec("g", "k", (1.0, 2.0, 3.0))
    runner = SweepRunner(_gain_diagram().to_dict(), spec, make, lambda *a: seen.append(a))
    outcome = runner.run()
    assert outcome.ok and outcome.result is not None
    assert seen == [(1, 3, "k=1"), (2, 3, "k=2"), (3, 3, "k=3")]
    assert outcome.result.runs == ["k=1", "k=2", "k=3"]
    np.testing.assert_allclose(outcome.result.vectors["y@3"], [3.0, 6.0])
    assert outcome.console[:2] == ["Sweep run 1 of 3: g.k=1", "ran 1.0"]


def test_a_failing_run_fails_the_sweep_with_its_number() -> None:
    calls = []

    def make(diagram: dict) -> _Fake:
        calls.append(diagram)
        return _Fake(diagram, fail=len(calls) == 2)

    spec = SweepSpec("g", "k", (1.0, -1.0, 3.0))
    outcome = SweepRunner(_gain_diagram().to_dict(), spec, make).run()
    assert outcome.status == ERROR and len(calls) == 2
    assert outcome.message == "sweep run 2 of 3 (k=-1): ValueError: J must be positive"


def test_cancel_stops_the_running_run_and_the_rest() -> None:
    gate = threading.Event()
    calls = []

    def make(diagram: dict) -> _Fake:
        calls.append(diagram)
        return _Fake(diagram, gate=gate)

    runner = SweepRunner(_gain_diagram().to_dict(), SweepSpec("g", "k", (1.0, 2.0)), make)
    out: list[RunOutcome] = []
    thread = threading.Thread(target=lambda: out.append(runner.run()))
    thread.start()
    assert gate.wait(5)
    runner.cancel()
    thread.join(5)
    assert out[0].status == CANCELLED and len(calls) == 1


# ---- real runs


def _block_runner(t_end: float, dt: float):
    def make(diagram: dict) -> BlockRunner:
        return BlockRunner(diagram, {}, t_end, dt, timeout=120)

    return make


def test_sweep_of_a_signal_diagram() -> None:
    spec = SweepSpec("g", "k", (1.0, 2.0, 3.0))
    outcome = SweepRunner(_gain_diagram().to_dict(), spec, _block_runner(0.1, 0.01)).run()
    assert outcome.ok, outcome.message
    result = outcome.result
    assert result is not None and result.analysis == "blocks"
    for k in (1, 2, 3):
        np.testing.assert_allclose(result.vectors[f"y@{k}"], 2.0 * k)


B, TAU = 0.01, 0.5
J_VALUES = (0.002, 0.005, 0.01)


def _inertia_diagram() -> dict:
    d, reg = new_diagram()
    d.add_block("MechanicalRotationalReference", block_id="ref")
    d.add_block("Inertia", settings={"J": 0.01}, block_id="inertia")
    d.add_block("RotationalDamper", settings={"b": B}, block_id="damper")
    d.add_block("TorqueSource", settings={"tau": TAU}, block_id="motor")
    wire(d, "inertia.A", "damper.A")
    wire(d, "damper.B", "ref.R")
    wire(d, "motor.A", "inertia.A")
    wire(d, "motor.B", "ref.R")
    add_solver(d, reg, "ref.R")
    return d.to_dict()


@pytest.mark.ngspice
def test_sweeping_j_gives_three_labelled_speed_curves() -> None:
    spec = SweepSpec("inertia", "J", J_VALUES)
    outcome = SweepRunner(_inertia_diagram(), spec, _block_runner(1.0, 1e-3)).run()
    assert outcome.ok, outcome.message
    result = outcome.result
    assert result is not None
    assert result.runs == ["J=0.002", "J=0.005", "J=0.01"]
    t = result.vectors["time"]
    for k, j in enumerate(J_VALUES, start=1):
        w = result.vectors[f"inertia.A.w@{k}"]
        assert result.units[f"inertia.A.w@{k}"] == "rad/s"
        # w = tau/b (1 - exp(-t b / J)): time constant J / b = 0.2, 0.5, 1 s
        np.testing.assert_allclose(w, TAU / B * (1 - np.exp(-t * B / j)), atol=0.05)
    # the curves differ: a larger J is slower
    at = np.searchsorted(t, 0.2)
    w1, w2, w3 = (result.vectors[f"inertia.A.w@{k}"][at] for k in (1, 2, 3))
    assert w1 > w2 > w3
