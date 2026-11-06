"""RLC band-pass acceptance (examples/rlc_bandpass.qsp) with a real ngspice .ac run.

The example is drawn as a schematic, so its nets are the netlister's N001 (source and L)
and N003 (across R). Series R = 20, L = 10 mH, C = 10 uF, output across R:
    f0 = 1 / (2 pi sqrt(LC)) = 503.29 Hz, Q = sqrt(L/C) / R = 1.581, BW = f0 / Q = 318.31 Hz
    fL, fH = f0 (sqrt(1 + 1/(4Q^2)) -/+ 1/(2Q)) = 368.70 Hz, 687.01 Hz
The test runs the netlist's own .ac dec 2000 10 100k card.
Checks the Bode pane (log frequency, magnitude in dB, phase on the right axis), the
transfer-function expression V(n003)/V(n001), the -3 dB points and phase there, the
+/-20 dB/decade skirts, and the bandwidth read with two cursors the way a user does it
(step each cursor with the arrow keys until the magnitude first reads -3 dB)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from core.project.io import load_project
from tests.ui.qml_harness import qml_window
from tests.ui.test_waveform_acceptance import run
from tests.ui.test_waveform_view import _child, _parts, _settle

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "rlc_bandpass.qsp"
R, L, C = 20.0, 10e-3, 10e-6
F0 = 1 / (2 * math.pi * math.sqrt(L * C))
Q = math.sqrt(L / C) / R
BW = F0 / Q
FL = F0 * (math.sqrt(1 + 1 / (4 * Q * Q)) - 1 / (2 * Q))
FH = F0 * (math.sqrt(1 + 1 / (4 * Q * Q)) + 1 / (2 * Q))
H = "V(n003)/V(n001)"
MINUS_3DB = -10 * math.log10(2)  # -3.0103 dB


def _example():
    loaded = load_project(EXAMPLE)
    assert loaded.ok and loaded.project is not None
    return loaded.project


@pytest.fixture
def opened(qapp: object):
    project = _example()
    result = run(project.netlists[0].text)
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    ctl.load_layouts(project.plots)
    model.set_result(result)
    return result, ctl


def _cursor(ctl: WaveformController, which: int) -> dict:
    return next(c for c in ctl.property("cursors") if c["which"] == which)


def _db(text: str) -> float:
    assert text.endswith(" dB"), text
    return float(text[:-3])


def _deg(text: str) -> float:
    return float(text.rstrip("°"))


def test_theory_values() -> None:
    assert pytest.approx(503.29, abs=0.01) == F0
    assert pytest.approx(1.5811, abs=1e-4) == Q
    assert pytest.approx(318.31, abs=0.01) == BW
    assert (pytest.approx(368.70, abs=0.01), pytest.approx(687.01, abs=0.01)) == (FL, FH)
    assert pytest.approx(BW) == FH - FL and pytest.approx(F0 * F0) == FL * FH


@pytest.mark.ngspice
def test_example_opens_as_a_bode_plot_of_the_transfer_function(opened) -> None:
    _, ctl = opened
    assert ctl.property("isAc") and ctl.property("xLog")
    [pane] = ctl.property("panes")
    [trace] = [t for t in pane["traces"] if t["name"] == H]
    assert trace["present"] and trace["expression"]


@pytest.mark.ngspice
def test_magnitude_phase_and_minus_3db_points(opened) -> None:
    result, _ = opened
    f = result.vectors["frequency"].real
    h = result.vectors["V(n003)"] / result.vectors["V(n001)"]
    db = 20 * np.log10(np.abs(h))
    ph = np.degrees(np.angle(h))
    lf = np.log10(f)
    k = int(np.argmax(db))
    assert f[k] == pytest.approx(F0, rel=0.002) and db[k] == pytest.approx(0, abs=0.01)
    assert np.interp(math.log10(F0), lf, ph) == pytest.approx(0, abs=0.2)
    # -3 dB crossings, interpolated between data points
    below = db < MINUS_3DB
    lo = np.interp(MINUS_3DB, db[: k + 1], lf[: k + 1])
    hi = np.interp(MINUS_3DB, db[k:][::-1], lf[k:][::-1])
    fl, fh = 10**lo, 10**hi
    assert fl == pytest.approx(FL, rel=1e-3) and fh == pytest.approx(FH, rel=1e-3)
    assert fh - fl == pytest.approx(BW, rel=2e-3)
    assert math.sqrt(fl * fh) == pytest.approx(F0, rel=1e-3)
    assert np.interp(lo, lf, ph) == pytest.approx(45, abs=0.3)
    assert np.interp(hi, lf, ph) == pytest.approx(-45, abs=0.3)
    assert below[0] and below[-1] and not below[k]
    # the whole curve matches H = R / (R + jwL + 1/(jwC)) (magnitude and phase)
    w = 2 * np.pi * f
    exact = R / (R + 1j * w * L + 1 / (1j * w * C))
    assert np.max(np.abs(db - 20 * np.log10(np.abs(exact)))) < 0.01
    assert np.max(np.abs(ph - np.degrees(np.angle(exact)))) < 0.1
    # skirts: about +20 dB/decade far below f0 and -20 dB/decade far above (half decades)
    assert np.interp(1.5, lf, db) - np.interp(1, lf, db) == pytest.approx(10, abs=0.1)
    assert np.interp(5, lf, db) - np.interp(4.5, lf, db) == pytest.approx(-10, abs=0.1)
    assert ph[0] == pytest.approx(90, abs=1) and ph[-1] == pytest.approx(-90, abs=0.5)


@pytest.mark.ngspice
def test_bandwidth_with_two_cursors(opened) -> None:
    _, ctl = opened
    assert ctl.attachCursor(3, 0, H)  # both cursors

    def walk(which: int, direction: int) -> None:
        ctl.setCursorX(which, F0)
        while _db(_cursor(ctl, which)["yText"]) > MINUS_3DB:
            assert ctl.stepCursor(which, direction)

    ctl.setCursorX(1, F0)
    c1 = _cursor(ctl, 1)
    assert c1["xText"] == "503.3 Hz" and abs(_db(c1["yText"])) < 0.01
    assert abs(_deg(c1["phaseText"])) < 1
    walk(1, -1)
    walk(2, +1)
    c1, c2 = _cursor(ctl, 1), _cursor(ctl, 2)
    step = 10 ** (1 / 2000)  # one data point at 2000 points per decade
    assert FL / step <= c1["x"] <= FL and FH <= c2["x"] <= FH * step
    assert _db(c1["yText"]) == pytest.approx(MINUS_3DB, abs=0.1)
    assert _db(c2["yText"]) == pytest.approx(MINUS_3DB, abs=0.1)
    assert _deg(c1["phaseText"]) == pytest.approx(45, abs=1)
    assert _deg(c2["phaseText"]) == pytest.approx(-45, abs=1)
    diff = ctl.property("cursorDiff")
    assert diff["dx"] == pytest.approx(BW, rel=0.025)  # within one step at each edge
    assert diff["dxText"].endswith(" Hz")
    assert _deg(diff["dPhaseText"]) == pytest.approx(-90, abs=2)
    q = math.sqrt(c1["x"] * c2["x"]) / diff["dx"]
    assert q == pytest.approx(Q, rel=0.025)


@pytest.mark.ngspice
def test_bode_pane_draws_magnitude_and_phase(qapp: object, tmp_path: Path, qtbot) -> None:
    project = _example()
    result = run(project.netlists[0].text)
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        ctl.load_layouts(project.plots)
        model.set_result(result)
        _settle(qtbot)
        pane = _child(view, "pane0")
        assert [(s["name"], s["kind"]) for s in pane.property("seriesState")] == [
            (H, "mag"),
            (H, "phase"),
        ]
        assert all(s["count"] > 0 for s in pane.property("seriesState"))
        ticks = [t["text"] for t in pane.property("rightTicks")]
        assert "0°" in ticks and any(t.startswith("-") for t in ticks)
        assert pane.property("yMax") >= 0 > pane.property("yMin")  # dB axis, peak 0 dB
