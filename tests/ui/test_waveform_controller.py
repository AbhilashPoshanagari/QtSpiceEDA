"""WaveformController without QML: layouts, undo, probes, zoom, cursors, project."""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGraphs import QLineSeries

from bridge.diagram_controller import DiagramController
from bridge.project_manager import ProjectManager
from bridge.simulator import Simulator
from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from core.project.io import load_project
from core.sim.result import SimulationResult
from tests.ui.fakes import tran_result
from tests.ui.test_waveform_model import ac_result


def diode_result() -> SimulationResult:
    v = np.linspace(0, 0.9, 181)
    i = 1e-14 * (np.exp(v / 0.02585) - 1)
    return SimulationResult(
        axis="v-sweep",
        vectors={"v-sweep": v, "I(D1)": i, "V(a)": v},
        units={"v-sweep": "V", "I(D1)": "A", "V(a)": "V"},
        analysis="dc",
    )


class FakeSchematic(QObject):
    probed = Signal(str)


@pytest.fixture
def parts(qapp: object) -> Iterator[tuple[WaveformModel, WaveformController]]:
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    yield model, ctl


def traces(ctl: WaveformController) -> list[list[str]]:
    return [[t["name"] for t in p["traces"]] for p in ctl.property("panes")]


def fill(ctl: WaveformController, pane: int, pixels: int = 400) -> tuple[dict, list[QLineSeries]]:
    series = [QLineSeries() for _ in range(8)]
    return ctl.fillPane(pane, series, pixels), series


# ---- W1: layouts follow the result


def test_new_result_fills_the_first_pane(parts) -> None:
    model, ctl = parts
    assert ctl.property("hasData") is False and ctl.property("panes") == []
    model.set_result(tran_result())
    assert ctl.property("analysis") == "tran" and ctl.property("hasData")
    assert traces(ctl) == [["V(out)", "V(in)"]]
    assert ctl.property("canUndo") is False  # not a user edit


def test_one_layout_per_analysis(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    ctl.addPane(0, False)
    model.set_result(ac_result())
    assert ctl.property("isAc") and ctl.property("xLog")  # Bode: log frequency
    assert traces(ctl) == [["V(out)"]]
    model.set_result(tran_result(51))
    assert traces(ctl) == [["V(out)", "V(in)"], []]  # the .tran layout came back
    assert set(ctl.layouts()) == {"tran", "ac"}


def test_traces_missing_from_the_result_stay_listed(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    model.set_result(ac_result())
    model.set_result(
        SimulationResult("time", {"time": np.arange(3.0), "V(x)": np.zeros(3)}, analysis="tran")
    )
    pane = ctl.property("panes")[0]
    assert [(t["name"], t["present"]) for t in pane["traces"]] == [
        ("V(out)", False),
        ("V(in)", False),
    ]
    assert pane["traces"][0]["color"] == "#a0a0a0"


def test_undo_redo_of_pane_and_trace_edits(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    steps = [
        (lambda: ctl.addPane(0, True) == 0, [[], ["V(out)", "V(in)"]]),
        (lambda: ctl.dropTrace(1, "V(in)", 0, False), [["V(in)"], ["V(out)"]]),
        (lambda: ctl.dropTrace(1, "V(out)", 0, True), [["V(in)", "V(out)"], ["V(out)"]]),
        (lambda: ctl.showOnly(0, "V(out)"), [["V(out)"], ["V(out)"]]),
        (lambda: ctl.removeTrace(1, "V(out)"), [["V(out)"], []]),
        (lambda: ctl.movePane(1, 0), [[], ["V(out)"]]),
        (lambda: ctl.deletePane(0), [["V(out)"]]),
        (lambda: ctl.setTraceShown("V(in)", True), [["V(out)", "V(in)"]]),
        (lambda: ctl.setTraceShown("V(out)", False), [["V(in)"]]),
    ]
    history = [traces(ctl)]
    for action, expected in steps:
        assert action()
        assert traces(ctl) == expected
        history.append(expected)
    for expected in reversed(history[:-1]):
        ctl.undo()
        assert traces(ctl) == expected
    assert ctl.property("canUndo") is False
    for expected in history[1:]:
        ctl.redo()
        assert traces(ctl) == expected


def test_no_op_edits_push_nothing(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    assert not ctl.deletePane(0)  # the last pane
    assert not ctl.dropTrace(0, "V(in)", 0, False)
    assert not ctl.addTrace("V(in)")  # already there
    assert ctl.property("canUndo") is False


def test_probe_goes_to_the_active_pane_and_undoes(parts) -> None:
    model, ctl = parts
    schematic = FakeSchematic()
    ctl.attachProbes(schematic)
    model.set_result(tran_result())
    ctl.addPane(0, False)  # the new pane becomes active
    assert ctl.property("activePane") == 1
    t = np.asarray(model.result().vectors["time"])
    model.add_trace("V(in,out)", "V", np.exp(-t / 1e-3))
    schematic.probed.emit("V(in,out)")
    assert traces(ctl) == [["V(out)", "V(in)"], ["V(in,out)"]]
    assert ctl.property("undoText") == "Add V(in,out)"
    ctl.undo()
    assert traces(ctl) == [["V(out)", "V(in)"], []]


# ---- W1: saved with the project


def test_layouts_are_saved_in_the_project(qapp: object, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    sim, diagram, model = Simulator(), DiagramController(), WaveformModel()
    ctl = WaveformController()
    try:
        sim.attachWaveforms(model)
        diagram.attachWaveforms(model)
        projects = ProjectManager(settings=settings)
        projects.attach(sim, diagram, model)
        ctl.attach(model)
        projects.attachPlots(ctl)
        sim.restore_result(tran_result())
        ctl.addPane(0, False)
        ctl.dropTrace(0, "V(in)", 1, False)
        assert projects.property("modified")
        path = tmp_path / "w.qsp"
        assert projects.saveAs(str(path))
        assert not projects.property("modified")
        loaded = load_project(path)
        assert loaded.ok and loaded.project is not None
        assert [p.traces for p in loaded.project.plots["tran"].panes] == [["V(out)"], ["V(in)"]]

        ctl.addPane(0, True)
        assert projects.property("modified")
        ctl.undo()
        assert not projects.property("modified")  # undo back to the saved state

        assert projects.newProject()
        assert ctl.layouts() == {} or traces(ctl) != [["V(out)"], ["V(in)"]]
        assert projects.open(str(path))
        assert traces(ctl) == [["V(out)"], ["V(in)"]]
        assert ctl.property("canUndo") is False
    finally:
        sim.shutdown()
        diagram.shutdown()


# ---- drawing


def test_fill_pane_bounds_points_and_autoscales(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result(200_001))
    info, series = fill(ctl, 0, 500)
    assert [s["name"] for s in info["series"]] == ["V(out)", "V(in)"]
    assert all(0 < s["count"] <= 1000 for s in info["series"])
    assert series[0].isVisible() and not series[2].isVisible()
    assert info["yMin"] < 0 < 1 < info["yMax"]
    assert info["hidden"] == 0 and info["rightTicks"] == []


def test_ac_pane_draws_phase_on_the_right_axis(parts) -> None:
    model, ctl = parts
    model.set_result(ac_result())
    pane = ctl.property("panes")[0]
    assert pane["seriesNeeded"] == 2
    info, series = fill(ctl, 0)
    assert [s["kind"] for s in info["series"]] == ["mag", "phase"]
    texts = [t["text"] for t in info["rightTicks"]]
    assert "-45°" in texts and "0°" in texts and "-90°" in texts
    # Phase points are scaled onto the magnitude axis.
    ys = [series[1].at(i).y() for i in range(series[1].count())]
    assert min(ys) >= info["yMin"] - 1e-9 and max(ys) <= info["yMax"] + 1e-9
    assert ctl.formatX(math.log10(159.155)) == "159.2 Hz"


# ---- W3: zoom


def x_range(ctl: WaveformController) -> tuple[float, float]:
    return ctl.property("xMin"), ctl.property("xMax")


def test_box_zoom_sets_x_for_all_panes_and_y_for_one(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result(1001))
    ctl.addPane(0, False)
    ctl.dropTrace(0, "V(in)", 1, False)
    fill(ctl, 0)
    fill(ctl, 1)
    assert ctl.zoomBox(0, 1e-3, 2e-3, 0.2, 0.6)
    assert x_range(ctl) == pytest.approx((1e-3, 2e-3))
    assert fill(ctl, 0)[0]["yMin"] == pytest.approx(0.2)
    assert fill(ctl, 0)[0]["yMax"] == pytest.approx(0.6)
    info = fill(ctl, 1)[0]  # the other pane autoscales on the new X range
    assert info["yMin"] < 1 < info["yMax"]
    assert ctl.property("canZoomBack")
    assert not ctl.zoomBox(0, 1e-3, 1e-3, 0, 1)  # an empty box


def test_zoom_back_auto_range_and_undo(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result(1001))
    full = x_range(ctl)
    ctl.zoomBox(0, 1e-3, 4e-3, 0, 1)
    ctl.zoomBox(0, 2e-3, 3e-3, 0, 1)
    assert ctl.zoomBack()
    assert x_range(ctl) == pytest.approx((1e-3, 4e-3))
    assert ctl.zoomBack() and x_range(ctl) == pytest.approx(full)
    assert not ctl.zoomBack() and not ctl.property("canZoomBack")
    # Undo walks back through the zoom backs and the zooms, history included.
    ctl.undo()
    assert x_range(ctl) == pytest.approx((1e-3, 4e-3)) and ctl.property("canZoomBack")
    ctl.undo()
    assert x_range(ctl) == pytest.approx((2e-3, 3e-3))
    ctl.redo()
    ctl.redo()
    assert x_range(ctl) == pytest.approx(full)
    ctl.zoomBox(0, 2e-3, 3e-3, 0, 1)
    assert ctl.autoRange() and x_range(ctl) == pytest.approx(full)
    assert not ctl.autoRange()  # already automatic: no undo step
    assert ctl.property("undoText") == "Auto-range"
    assert ctl.zoomBack() and x_range(ctl) == pytest.approx((2e-3, 3e-3))


def test_wheel_and_pan_steps_merge_into_one_undo_step(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result(1001))
    for _ in range(3):
        assert ctl.zoomX(0.5, 2.5e-3)
    assert x_range(ctl) == pytest.approx((2.5e-3 - 0.3125e-3, 2.5e-3 + 0.3125e-3))
    assert ctl.panX(1e-4)
    ctl.undo()
    assert x_range(ctl) == pytest.approx((0, 5e-3)) and not ctl.property("canUndo")
    assert ctl.zoomX(1.25, 2.5e-3) is False or x_range(ctl) == pytest.approx((0, 5e-3))


def test_delta_x_shows_frequency(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result(1001))
    assert ctl.formatDeltaX(1e-3, 2e-3) == "1 ms  (1/Δ = 1 kHz)"
    model.set_result(diode_result())
    assert ctl.formatDeltaX(0.1, 0.3) == "200 mV"


# ---- W5: log axes and Bode


def test_log_y_hides_non_positive_values_or_plots_abs(parts) -> None:
    model, ctl = parts
    model.set_result(diode_result())
    ctl.showOnly(0, "I(D1)")
    assert ctl.setYLog(0, True)
    assert ctl.property("panes")[0]["yLog"] is True
    info, _ = fill(ctl, 0)
    assert info["hidden"] == 1  # I(D1) = 0 at 0 V
    assert -17 < info["yMin"] < -12 and info["yMax"] > 0  # decades, not amps
    assert ctl.formatY(0, -9) == "1 nA" and ctl.formatY(0, -2) == "10 mA"
    assert ctl.formatDeltaY(0, -9, -6) == "×1000"

    t = np.linspace(0, 1, 1001)
    model.set_result(
        SimulationResult("time", {"time": t, "V(s)": np.sin(2 * np.pi * 3 * t)}, {}, "tran")
    )
    ctl.setYLog(0, True)
    hidden = fill(ctl, 0)[0]["hidden"]
    assert hidden > 400
    assert ctl.setShowAbs(0, True)
    assert fill(ctl, 0)[0]["hidden"] < 10  # only the zeros
    ctl.undo()
    ctl.undo()
    assert ctl.property("panes")[0]["yLog"] is False


def test_log_x_on_tran_and_linear_x_on_ac(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result(1001))
    assert ctl.setXLog(True)
    assert ctl.property("xMin") == pytest.approx(math.log10(5e-6))  # t = 0 left out
    assert ctl.formatX(-3) == "1 ms"
    assert ctl.logGrid(-3, -2)[0] == pytest.approx(math.log10(2e-3))
    model.set_result(ac_result())
    assert ctl.property("xLog") and ctl.property("xMax") == pytest.approx(5)
    assert ctl.setXLog(False)
    assert ctl.property("xMax") == pytest.approx(1e5)
    ctl.undo()
    assert ctl.property("xLog")


def test_ac_opens_as_bode_with_minus_3db_and_minus_45_degrees(parts) -> None:
    model, ctl = parts
    model.set_result(ac_result())
    info, series = fill(ctl, 0)
    assert ctl.formatY(0, -3.01) == "-3.01 dB"
    assert info["yMax"] > 0 and info["yMin"] < -40
    fc = 1 / (2 * math.pi * 1e-3)
    mags = [(series[0].at(i).x(), series[0].at(i).y()) for i in range(series[0].count())]
    nearest = min(mags, key=lambda p: abs(p[0] - math.log10(fc)))
    assert nearest[1] == pytest.approx(-3.01, abs=0.1)
