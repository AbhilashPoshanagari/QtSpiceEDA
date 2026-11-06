"""Acceptance for W7-W10 with real ngspice runs:
- V(out)/V(in) on the RC low-pass .ac run reads -3 dB and -45 degrees at 159 Hz;
- the RMS of a 1 V sine over whole periods is 0.707 V within 0.1 %."""

from __future__ import annotations

import math

import pytest

from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from tests.ui.test_waveform_acceptance import RC_AC, run

SINE = """1 V sine, 1 kHz
V1 s 0 SIN(0 1 1k)
R1 s 0 1k
.tran 1u 4m 0 1u
.end
"""


@pytest.mark.ngspice
def test_rc_ac_ratio_is_minus_3db_at_159_hz(qapp: object) -> None:
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    model.set_result(run(RC_AC))
    assert ctl.plotExpression("V(out)/V(in)", 0) == ""
    assert ctl.attachCursor(1, 0, "V(out)/V(in)")
    assert ctl.setCursorX(1, 1 / (2 * math.pi * 1e3 * 1e-6))
    [cursor] = ctl.property("cursors")
    assert cursor["xText"] == "159.2 Hz"
    assert float(cursor["yText"].removesuffix(" dB")) == pytest.approx(-3.01, abs=0.02)
    assert float(cursor["phaseText"].rstrip("°")) == pytest.approx(-45, abs=0.2)
    assert ctl.plotExpression("dB(V(out)/V(in))", 0) == ""  # the shipped .func


@pytest.mark.ngspice
def test_rms_of_a_1v_sine_over_whole_periods(qapp: object) -> None:
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    model.set_result(run(SINE))
    s = ctl.traceStats(0, "V(s)")
    assert s["ok"] and s["hasRms"]
    assert s["rms"] == pytest.approx(1 / math.sqrt(2), rel=1e-3)
    assert abs(s["average"]) < 1e-3
    current = ctl.traceStats(0, "I(v1)")
    assert current["hasRms"] and current["rms"] == pytest.approx(1e-3 / math.sqrt(2), rel=1e-3)
