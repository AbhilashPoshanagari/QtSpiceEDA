"""Phase 3 acceptance: examples/rc_lowpass.cir (R = 1k, C = 1u, RC = 1 ms)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.netlist.builder import build
from core.netlist.parser import parse
from core.sim.decimate import to_db
from core.sim.ngspice_adapter import NgspiceAdapter

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "rc_lowpass.cir"
RC = 1e-3


@pytest.fixture
def circuit():
    result = parse(EXAMPLE.read_text())
    assert result.ok, result.diagnostics
    return result.circuit


def _run(deck: str):
    adapter = NgspiceAdapter()
    assert adapter.available(), adapter.last_error
    adapter.load(deck)
    adapter.run()
    result = adapter.results()
    assert result is not None
    return result


@pytest.mark.ngspice
def test_tran_matches_exponential_within_0_1_percent(circuit) -> None:
    result = _run(build(circuit, ".tran 1u 5m"))
    t = result.vectors["time"]
    v = result.vectors["V(out)"]
    expected = 1 - np.exp(-t / RC)
    # 0.1 % of the 1 V final value, everywhere on the curve.
    assert np.max(np.abs(v - expected)) < 1e-3
    assert np.interp(RC, t, v) == pytest.approx(1 - np.exp(-1), rel=1e-3)


@pytest.mark.ngspice
def test_ac_minus_3db_at_159_hz(circuit) -> None:
    result = _run(build(circuit, ".ac dec 50 1 100k"))
    assert result.analysis == "ac"
    f = result.vectors["frequency"]
    db = to_db(result.vectors["V(out)"])
    f_3db = np.interp(-3.0103, db[::-1], f[::-1])  # db falls with f
    assert f_3db == pytest.approx(1 / (2 * np.pi * RC), rel=0.01)  # 159.15 Hz
    assert round(f_3db) == 159
