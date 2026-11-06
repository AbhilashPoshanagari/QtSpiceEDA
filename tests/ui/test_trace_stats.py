"""W10: Ctrl+click a trace name -> average and RMS over the zoomed X range."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, Qt

from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from core.sim.result import SimulationResult
from tests.ui.mouse import click, item_center
from tests.ui.qml_harness import qml_window
from tests.ui.test_waveform_model import ac_result
from tests.ui.test_waveform_view import _child, _parts, _settle


def sine_result() -> SimulationResult:
    t = np.linspace(0, 4e-3, 4001)  # four periods of 1 kHz
    return SimulationResult(
        axis="time",
        vectors={
            "time": t,
            "V(s)": np.sin(2e3 * np.pi * t),
            "I(r1)": 1e-3 * np.sin(2e3 * np.pi * t),
        },
        units={"time": "s", "V(s)": "V", "I(r1)": "A"},
        analysis="tran",
    )


@pytest.fixture
def parts(qapp: object):
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    yield model, ctl


def test_stats_over_the_zoomed_range(parts) -> None:
    model, ctl = parts
    model.set_result(sine_result())
    s = ctl.traceStats(0, "V(s)")
    assert s["ok"] and s["hasRms"]
    assert s["rms"] == pytest.approx(1 / math.sqrt(2), rel=1e-3)
    assert abs(s["average"]) < 1e-6
    assert s["rmsText"] == "707.1 mV" and s["fromText"] == "0 s" and s["toText"] == "4 ms"
    ctl.zoomBox(0, 0, 0.25e-3, -1, 1)  # a quarter period: average 2/pi
    s = ctl.traceStats(0, "V(s)")
    assert s["average"] == pytest.approx(2 / math.pi, rel=1e-3)
    assert s["toText"] == "250 µs"
    assert ctl.traceStats(0, "I(r1)")["hasRms"]


def test_rms_only_for_voltage_and_current_traces(parts) -> None:
    model, ctl = parts
    model.set_result(sine_result())
    assert ctl.plotExpression("V(s)*I(r1)", 0) == ""  # power: W
    assert ctl.plotExpression("V(s)/I(r1)", 0) == ""  # no unit
    for name in ("V(s)*I(r1)", "V(s)/I(r1)"):
        s = ctl.traceStats(0, name)
        assert s["ok"] and not s["hasRms"] and s["rmsText"] == ""
    assert ctl.traceStats(0, "2*V(s)")["ok"] is False  # not in the pane
    assert ctl.plotExpression("2*V(s)", 0) == ""
    assert ctl.traceStats(0, "2*V(s)")["rms"] == pytest.approx(math.sqrt(2), rel=1e-3)


def test_no_stats_for_ac(parts) -> None:
    model, ctl = parts
    model.set_result(ac_result())
    s = ctl.traceStats(0, "V(out)")
    assert not s["ok"] and ".tran" in s["message"]


def test_ctrl_click_a_trace_name_shows_the_readout(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, window, view):
        model, ctl = _parts(engine)
        model.set_result(sine_result())
        _settle(qtbot)
        label = _child(view, "label_0_V(s)")
        click(window, item_center(label), Qt.KeyboardModifier.ControlModifier)
        _settle(qtbot)
        pane = _child(view, "pane0")
        popup = pane.findChild(QObject, "statsPopup")
        assert popup.property("visible")
        text = popup.property("contentItem").property("text")
        assert "Average:" in text and "RMS: 707.1 mV" in text
        assert ctl.property("cursors") == []  # Ctrl+click does not attach a cursor
        popup.close()
        click(window, item_center(_child(view, "label_0_V(s)")))
        _settle(qtbot)
        assert len(ctl.property("cursors")) == 1
