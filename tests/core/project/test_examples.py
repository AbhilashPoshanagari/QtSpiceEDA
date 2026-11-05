"""The v0.1 sample projects in examples/: they load, check clean and give the expected
answers (RC low-pass, diode rectifier, first-order feedback loop)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.blocks.runner import run_diagram
from core.netlist.builder import build
from core.netlist.parser import parse
from core.netlist.validate import validate
from core.project.io import Project, load_project
from core.sim.decimate import to_db
from core.sim.ngspice_adapter import NgspiceAdapter
from core.sim.result import SimulationResult

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SAMPLES = ["rc_filter.qsp", "diode_rectifier.qsp", "first_order_feedback.qsp"]


def load(name: str) -> Project:
    loaded = load_project(EXAMPLES / name)
    assert loaded.ok, loaded.errors
    assert loaded.warnings == []
    assert loaded.project is not None
    return loaded.project


def netlist(name: str) -> str:
    return load(name).netlists[0].text


def simulate(text: str, card: str | None = None) -> SimulationResult:
    result = parse(text)
    assert result.ok, result.diagnostics
    adapter = NgspiceAdapter()
    adapter.load(build(result.circuit, card))
    adapter.run()
    sim = adapter.results()
    assert sim is not None
    return sim


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_loads_without_block_code(name: str) -> None:
    project = load(name)
    # Only built-in blocks: opening a sample never asks to trust code.
    assert all(not s.code.strip() for s in project.block_sources)
    assert project.results == {}


@pytest.mark.parametrize("name", ["rc_filter.qsp", "diode_rectifier.qsp"])
def test_sample_netlist_checks_clean(name: str) -> None:
    result = parse(netlist(name))
    assert result.diagnostics == []
    assert validate(result.circuit) == []


def test_feedback_diagram_matches_first_order_step_response() -> None:
    project = load("first_order_feedback.qsp")
    settings = project.analysis
    result = run_diagram(
        project.diagrams[0].diagram, {}, settings.diagram_t_end, settings.diagram_dt
    )
    t, y, r = result.vectors["time"], result.vectors["y"], result.vectors["r"]
    tau, t0 = 0.5, 0.5  # Gain k = 1/tau = 2, step at 0.5 s
    assert t[-1] == pytest.approx(4.0)
    np.testing.assert_array_equal(r, np.where(t >= t0, 1.0, 0.0))
    expected = np.where(t >= t0, 1 - np.exp(-(t - t0) / tau), 0.0)
    # Forward Euler with dt = 1 ms: within 0.5 % of the 1.0 final value everywhere.
    assert np.max(np.abs(y - expected)) < 5e-3
    assert y[-1] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.ngspice
def test_rc_filter_tran_and_ac() -> None:
    text = netlist("rc_filter.qsp")
    tran = simulate(text)  # the first card, .tran
    t, v = tran.vectors["time"], tran.vectors["V(out)"]
    assert np.interp(1e-3, t, v) == pytest.approx(1 - np.exp(-1), rel=1e-3)

    ac = simulate(text, ".ac dec 50 1 100k")
    f, db = ac.vectors["frequency"], to_db(ac.vectors["V(out)"])
    f_3db = np.interp(-3.0103, db[::-1], f[::-1])
    assert f_3db == pytest.approx(1 / (2 * np.pi * 1e-3), rel=0.01)


@pytest.mark.ngspice
def test_diode_rectifier_peak_and_ripple() -> None:
    result = simulate(netlist("diode_rectifier.qsp"))
    t, vin, vout = (result.vectors[k] for k in ("time", "V(in)", "V(out)"))
    assert vin.max() == pytest.approx(5.0, rel=1e-3)
    assert vout.min() > -0.05  # a half-wave rectifier never drives the output negative
    last = vout[t >= 0.08]  # the last cycle, once C1 has charged
    assert 4.0 < last.max() < 4.6  # 5 V minus one diode drop
    ripple = last.max() - last.min()
    assert 0.2 < ripple < 0.6  # ~ Vout / (f R C) = 0.4 V
