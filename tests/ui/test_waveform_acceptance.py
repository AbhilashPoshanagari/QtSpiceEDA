"""Acceptance for W1-W6 with real ngspice runs:
- an RC low-pass .ac run opens as a Bode pane and cursors read -3 dB and -45 degrees
  at 159 Hz;
- a diode forward sweep from about 1 nA to 10 mA reads clearly on a log Y axis
  (decade labels, no hidden points);
- every pane and trace action on those results can be undone."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from core.project.io import load_project
from core.sim.ngspice_adapter import NgspiceAdapter
from core.sim.result import SimulationResult
from tests.ui.qml_harness import qml_window
from tests.ui.test_waveform_view import _child, _parts, _settle

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

RC_AC = """RC low-pass, fc = 159 Hz
V1 in 0 DC 0 AC 1
R1 in out 1k
C1 out 0 1u
.ac dec 50 1 100k
.end
"""


def run(deck: str) -> SimulationResult:
    adapter = NgspiceAdapter()
    assert adapter.available(), adapter.last_error
    adapter.load(deck)
    adapter.run()
    result = adapter.results()
    assert result is not None
    return result


def diode_deck() -> str:
    loaded = load_project(EXAMPLES / "diode_log.qsp")
    assert loaded.ok and loaded.project is not None
    return loaded.project.netlists[0].text


@pytest.mark.ngspice
def test_rc_ac_bode_with_cursors_at_159_hz(qapp: object, tmp_path: Path, qtbot) -> None:
    result = run(RC_AC)
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(result)
        ctl.showOnly(0, "V(out)")
        _settle(qtbot)
        assert ctl.property("isAc") and ctl.property("xLog")  # Bode: log frequency
        pane = _child(view, "pane0")
        assert [s["kind"] for s in pane.property("seriesState")] == ["mag", "phase"]
        assert any(t["text"] == "-45°" for t in pane.property("rightTicks"))

        assert ctl.attachCursor(1, 0, "V(out)")
        assert ctl.setCursorX(1, 1 / (2 * math.pi * 1e3 * 1e-6))
        _settle(qtbot)
        assert _child(view, "cursor1X").property("text") == "159.2 Hz"
        magnitude = _child(view, "cursor1Y").property("text")
        assert magnitude.endswith(" dB") and float(magnitude[:-3]) == pytest.approx(-3.01, abs=0.02)
        phase = _child(view, "cursor1Phase").property("text")
        assert float(phase.rstrip("°")) == pytest.approx(-45, abs=0.2)


@pytest.mark.ngspice
def test_diode_sweep_reads_on_log_y(qapp: object, tmp_path: Path, qtbot) -> None:
    result = run(diode_deck())
    current = result.vectors["I(vm)"]
    assert current.min() < 1.5e-9 and current.max() > 9e-3  # about 1 nA .. 10 mA
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(result)
        ctl.showOnly(0, "I(vm)")
        assert ctl.setYLog(0, True)
        _settle(qtbot)
        pane = _child(view, "pane0")
        assert pane.property("hiddenPoints") == 0
        lo, hi = pane.property("yMin"), pane.property("yMax")
        assert -9.5 < lo < -8.5 and -2.5 < hi < -1.5  # seven decades on screen
        assert ctl.formatY(0, -9) == "1 nA" and ctl.formatY(0, -2) == "10 mA"
        assert ctl.formatY(0, -6) == "1 µA"
        assert pane.property("yTickInterval") == 1  # one label per decade
        assert ctl.attachCursor(1, 0, "I(vm)")
        ctl.setCursorX(1, 0.5)
        [cursor] = ctl.property("cursors")
        assert cursor["yText"].endswith("A") and 1e-5 < cursor["value"] < 1e-3


@pytest.mark.ngspice
def test_every_pane_and_trace_action_undoes(qapp: object, tmp_path: Path, qtbot) -> None:
    result = run(diode_deck())
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(result)
        _settle(qtbot)
        start = ctl.layouts()["dc"].to_dict()
        actions = [
            lambda: ctl.addPane(0, False) >= 0,
            lambda: ctl.addPane(1, True) >= 0,
            lambda: ctl.dropTrace(0, "I(vm)", 1, False),
            lambda: ctl.dropTrace(1, "I(vm)", 2, True),
            lambda: ctl.showOnly(0, "V(in)") or True,
            lambda: ctl.removeTrace(2, "I(vm)"),
            lambda: ctl.movePane(2, 0),
            lambda: ctl.setYLog(1, True),
            lambda: ctl.setShowAbs(1, True),
            lambda: ctl.setXLog(True),
            lambda: ctl.zoomBox(1, -1, -0.3, -8, -3),
            lambda: ctl.zoomX(0.5, -0.5),
            lambda: ctl.autoRange(),
            lambda: ctl.zoomBack(),
            lambda: ctl.configure({"panes": [{"from": 1, "title": "I"}, {"from": 0}]}),
            lambda: ctl.setTraceShown("V(in)", False),
            lambda: ctl.deletePane(0),
        ]
        for action in actions:
            assert action()
        _settle(qtbot)
        assert ctl.layouts()["dc"].to_dict() != start
        while ctl.property("canUndo"):
            ctl.undo()
        _settle(qtbot)
        assert ctl.layouts()["dc"].to_dict() == start
