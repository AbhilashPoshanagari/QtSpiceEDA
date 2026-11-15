"""A netlist run starts with an empty waveform window (LTspice behaviour).

Before the fix every node of the result was plotted after a run, so a netlist with
many nodes (the CMOS XOR in tests/netlists/good/cmos_xor.cir) filled the plot with all
of them. Now new traces start hidden; the user adds the ones they want, and those stay
plotted on the next run. A result restored from a project still shows its traces.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from bridge.simulator import Simulator
from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from tests.ui.fakes import FakeBackend, tran_result

XOR = Path(__file__).resolve().parents[1] / "netlists" / "good" / "cmos_xor.cir"
NODES = {"V(a)", "V(b)", "V(abar)", "V(bbar)", "V(y)", "V(vdd)"}


def plotted(ctl: WaveformController) -> list[str]:
    return [t["name"] for p in ctl.property("panes") for t in p["traces"]]


def visible(model: WaveformModel) -> list[str]:
    return [t["name"] for t in model.property("traces") if t["visible"]]


# ---- WaveformModel: the show_new switch


def test_show_new_false_lists_traces_but_hides_them(qapp: object) -> None:
    model = WaveformModel()
    model.set_result(tran_result(), show_new=False)
    assert model.property("traceNames") == ["V(out)", "V(in)"]
    assert visible(model) == []


def test_default_still_shows_every_trace(qapp: object) -> None:
    model = WaveformModel()
    model.set_result(tran_result())
    assert visible(model) == ["V(out)", "V(in)"]


def test_shown_traces_stay_shown_on_the_next_run(qapp: object) -> None:
    model = WaveformModel()
    model.set_result(tran_result(), show_new=False)
    model.setVisible("V(out)", True)
    model.set_result(tran_result(), show_new=False)
    assert visible(model) == ["V(out)"]


# ---- Simulator -> WaveformModel -> WaveformController (fake backend)


@pytest.fixture
def window(qapp: object) -> Iterator[tuple[Simulator, WaveformModel, WaveformController]]:
    sim = Simulator(backend=FakeBackend(lines=["Circuit: xor", "Transient Analysis"]))
    model, ctl = WaveformModel(), WaveformController()
    sim.attachWaveforms(model)
    ctl.attach(model)
    yield sim, model, ctl
    sim.shutdown()


def test_run_starts_with_an_empty_plot(window, qtbot) -> None:
    sim, model, ctl = window
    with qtbot.waitSignal(sim.resultReady, timeout=3000):
        sim.run(XOR.read_text())
    assert model.property("traceNames")  # the result is there ...
    assert visible(model) == []  # ... but nothing is selected
    assert plotted(ctl) == []


def test_added_trace_stays_alone_after_a_second_run(window, qtbot) -> None:
    sim, model, ctl = window
    with qtbot.waitSignal(sim.resultReady, timeout=3000):
        sim.run(XOR.read_text())
    name = model.property("traceNames")[0]
    assert ctl.addTrace(name)
    with qtbot.waitSignal(sim.resultReady, timeout=3000):
        sim.run(XOR.read_text())
    assert plotted(ctl) == [name]


def test_restored_project_result_still_shows_its_traces(window) -> None:
    sim, model, ctl = window
    sim.restore_result(tran_result())
    assert visible(model) == ["V(out)", "V(in)"]
    assert plotted(ctl) == ["V(out)", "V(in)"]


# ---- the XOR netlist with real ngspice


@pytest.fixture
def ngspice_window(qapp: object) -> Iterator[tuple[Simulator, WaveformModel, WaveformController]]:
    sim = Simulator()
    model, ctl = WaveformModel(), WaveformController()
    sim.attachWaveforms(model)
    ctl.attach(model)
    yield sim, model, ctl
    sim.shutdown()


@pytest.mark.ngspice
def test_cmos_xor_run_selects_no_node(ngspice_window, qtbot) -> None:
    sim, model, ctl = ngspice_window
    assert sim.check(XOR.read_text()), sim.property("diagnostics")
    with qtbot.waitSignal(sim.resultReady, timeout=30000):
        sim.run(XOR.read_text())
    names = set(model.property("traceNames"))
    assert names >= NODES, names
    assert visible(model) == []
    assert plotted(ctl) == []

    # the user plots Y; it works, and is the only trace after a re-run
    assert ctl.addTrace("V(y)")
    assert plotted(ctl) == ["V(y)"]
    with qtbot.waitSignal(sim.resultReady, timeout=30000):
        sim.run(XOR.read_text())
    assert plotted(ctl) == ["V(y)"]

    # and the circuit really is an XOR: Y = A ^ B, sampled mid-way through each state
    result = sim.result
    t = np.asarray(result.vectors["time"]).real
    for at, a, b in ((5e-9, 1, 1), (15e-9, 1, 0), (25e-9, 0, 1), (35e-9, 0, 0)):
        assert np.interp(at, t, np.asarray(result.vectors["V(a)"]).real) == pytest.approx(
            1.8 * a, abs=0.05
        )
        assert np.interp(at, t, np.asarray(result.vectors["V(b)"]).real) == pytest.approx(
            1.8 * b, abs=0.05
        )
        y = np.interp(at, t, np.asarray(result.vectors["V(y)"]).real)
        assert (y > 0.9) == bool(a ^ b), (at, y)
