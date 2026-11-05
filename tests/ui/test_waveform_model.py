from __future__ import annotations

import csv
import math
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGraphs import QLineSeries

from bridge.waveform_model import WaveformModel, si
from core.sim.result import SimulationResult
from tests.ui.fakes import tran_result


def ac_result() -> SimulationResult:
    f = np.logspace(0, 5, 251)
    h = 1 / (1 + 1j * 2 * np.pi * f * 1e-3)
    return SimulationResult(
        axis="frequency",
        vectors={"frequency": f, "V(out)": h},
        units={"frequency": "Hz", "V(out)": "V"},
        analysis="ac",
    )


@pytest.fixture
def model(qapp: object) -> WaveformModel:
    return WaveformModel()


def test_empty_model(model: WaveformModel) -> None:
    assert model.property("traceNames") == []
    assert model.property("hasAxis") is False
    assert model.samples("V(out)", 0, 1, 100) == []
    assert model.exportCsv("x.csv") is False
    assert "run a simulation" in model.property("errorString")


def test_trace_names_and_visibility(model: WaveformModel, qtbot) -> None:
    with qtbot.waitSignals([model.resultChanged, model.visibilityChanged]):
        model.set_result(tran_result())
    assert model.property("traceNames") == ["V(out)", "V(in)"]
    assert model.property("analysis") == "tran"
    assert model.property("xLabel") == "time [s]"
    assert model.property("xMin") == 0 and model.property("xMax") == pytest.approx(5e-3)
    with qtbot.waitSignal(model.visibilityChanged):
        model.setVisible("V(in)", False)
    assert model.isVisible("V(in)") is False
    assert [t["visible"] for t in model.property("traces")] == [True, False]
    model.set_result(tran_result())  # visibility is kept across runs
    assert model.isVisible("V(in)") is False


def test_samples_reduce_a_million_points(model: WaveformModel) -> None:
    model.set_result(tran_result(1_000_000))
    started = time.perf_counter()
    points = model.samples("V(out)", 0, 5e-3, 800)
    elapsed = time.perf_counter() - started
    assert 0 < len(points) <= 1600
    assert elapsed < 0.1
    ys = [p.y() for p in points]
    assert min(ys) == pytest.approx(0, abs=1e-6)
    assert max(ys) == pytest.approx(1 - math.exp(-5), rel=1e-6)


def test_samples_window(model: WaveformModel) -> None:
    model.set_result(tran_result(10_001))
    points = model.samples("V(out)", 1e-3, 2e-3, 100)
    xs = [p.x() for p in points]
    assert xs[0] <= 1e-3 <= xs[1] and xs[-2] <= 2e-3 <= xs[-1]


def test_fill_series(model: WaveformModel) -> None:
    model.set_result(tran_result(100_000))
    series = QLineSeries()
    count = model.fillSeries(series, "V(out)", 0, 5e-3, 500, False)
    assert count == series.count() <= 1000
    assert model.fillSeries(series, "nope", 0, 1, 500, False) == 0


def test_ac_is_db_and_phase_on_log_x(model: WaveformModel) -> None:
    model.set_result(ac_result())
    assert model.property("isAc") is True
    assert model.property("xMin") == pytest.approx(0) and model.property("xMax") == 5
    x_fc = math.log10(1 / (2 * math.pi * 1e-3))
    assert model.valueAt("V(out)", x_fc, False) == pytest.approx(-3.01, abs=0.02)
    assert model.valueAt("V(out)", x_fc, True) == pytest.approx(-45, abs=0.5)
    assert model.formatX(x_fc) == "159.2 Hz"
    assert model.formatY("V(out)", -3.0103, False) == "-3.01 dB"
    assert model.formatY("V(out)", -45.0, True) == "-45°"
    assert model.phaseSamples("V(out)", 0, 5, 50)
    lo, hi = model.yRange(0, 5, True)
    assert lo < -85 and hi > -5


def test_tran_has_no_phase(model: WaveformModel) -> None:
    model.set_result(tran_result())
    assert model.phaseSamples("V(out)", 0, 1, 10) == []
    assert math.isnan(model.valueAt("V(out)", 0, True))


def test_descending_dc_sweep_is_reordered(model: WaveformModel) -> None:
    v = np.linspace(5, 0, 51)
    model.set_result(
        SimulationResult(
            axis="v-sweep",
            vectors={"v-sweep": v, "V(out)": v / 2},
            units={"v-sweep": "V", "V(out)": "V"},
            analysis="dc",
        )
    )
    assert model.property("xMin") == 0 and model.property("xMax") == 5
    assert model.valueAt("V(out)", 4.0, False) == pytest.approx(2.0)


def test_y_range_follows_visible_traces(model: WaveformModel) -> None:
    model.set_result(tran_result())
    lo, hi = model.yRange(0, 5e-3, False)
    assert lo < 0 and hi > 1  # V(in) = 1 plus padding
    model.setVisible("V(in)", False)
    model.setVisible("V(out)", False)
    assert model.yRange(0, 5e-3, False) == [-1.0, 1.0]


def test_cursor_formatting(model: WaveformModel) -> None:
    model.set_result(tran_result())
    assert model.formatX(1.5e-3) == "1.5 ms"
    assert model.formatDeltaX(1e-3, 3e-3) == "2 ms"
    assert model.formatY("V(out)", 0.632, False) == "632 mV"
    assert si(0) == "0" and si(float("nan")) == "—" and si(1e3, "Hz") == "1 kHz"


def test_export_csv(model: WaveformModel, tmp_path: Path) -> None:
    model.set_result(ac_result())
    target = tmp_path / "ac.csv"
    assert model.exportCsv(QUrl.fromLocalFile(str(target))) is True
    rows = list(csv.reader(target.open()))
    assert rows[0] == ["frequency [Hz]", "V(out).re [V]", "V(out).im [V]"]
    assert float(rows[1][0]) == pytest.approx(1.0)  # real frequency, not log10
    assert len(rows) == 252
    assert model.exportCsv(QUrl.fromLocalFile(str(tmp_path / "no" / "x.csv"))) is False
    assert "x.csv" in model.property("errorString")
