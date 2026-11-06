from __future__ import annotations

import math

import numpy as np
import pytest

from core.waveform.traces import MAG, PHASE, TraceStore, nice_ticks, padded


def _tran(points: int = 1001) -> TraceStore:
    t = np.linspace(0, 5e-3, points)
    return TraceStore.build(
        "tran",
        t,
        "s",
        {"V(out)": ("V", 1 - np.exp(-t / 1e-3)), "V(sin)": ("V", np.sin(2e3 * np.pi * t))},
    )


def _ac() -> TraceStore:
    f = np.logspace(0, 5, 501)
    h = 1 / (1 + 1j * 2 * np.pi * f * 1e-3)
    return TraceStore.build("ac", f, "Hz", {"V(out)": ("V", h)})


def test_values_and_interpolation() -> None:
    store = _tran()
    assert store.names() == ["V(out)", "V(sin)"] and store.unit("V(out)") == "V"
    assert store.value_at("V(out)", 1e-3) == pytest.approx(1 - math.exp(-1), abs=1e-4)
    assert math.isnan(store.value_at("nope", 0))
    assert not store.add("bad", "V", np.zeros(3))  # wrong length


def test_ac_magnitude_and_phase_at_corner() -> None:
    store = _ac()
    fc = 1 / (2 * math.pi * 1e-3)
    assert store.value_at("V(out)", fc, MAG) == pytest.approx(-3.01, abs=0.02)
    assert store.value_at("V(out)", fc, PHASE) == pytest.approx(-45, abs=0.5)


def test_series_decimated_to_pixels() -> None:
    store = _tran(1_000_001)
    s = store.series("V(sin)", MAG, False, False, False, 0, 5e-3, 500)
    assert 0 < len(s.x) <= 1000
    assert s.y.max() == pytest.approx(1, abs=1e-3) and s.y.min() == pytest.approx(-1, abs=1e-3)
    zoomed = store.series("V(sin)", MAG, False, False, False, 1e-3, 1.0001e-3, 500)
    assert 0 < len(zoomed.x) <= 1000


def test_log_x_drops_non_positive_axis_values() -> None:
    store = _tran()
    s = store.series("V(out)", MAG, True, False, False, -6, math.log10(5e-3), 400)
    assert np.all(np.isfinite(s.x)) and s.x.max() == pytest.approx(math.log10(5e-3))
    assert store.x_bounds(True)[0] == pytest.approx(5e-6)


def test_log_y_hides_or_takes_abs() -> None:
    store = _tran()
    hidden = store.series("V(sin)", MAG, False, True, False, 0, 5e-3, 400)
    assert hidden.hidden > 400  # the negative half waves and the zeros
    assert np.all(hidden.y <= 0 + 1e-12)  # log10 of values up to 1
    shown = store.series("V(sin)", MAG, False, True, True, 0, 5e-3, 400)
    assert shown.hidden < hidden.hidden  # only exact zeros stay hidden


def test_step_moves_one_data_point() -> None:
    store = _tran(11)  # 0, 0.5 ms, 1 ms, ...
    assert store.step(1e-3, 1) == pytest.approx(1.5e-3)
    assert store.step(1e-3, -1) == pytest.approx(0.5e-3)
    assert store.step(1.2e-3, 1) == pytest.approx(1.5e-3)
    assert store.step(0, -1) == 0 and store.step(5e-3, 1) == pytest.approx(5e-3)
    assert store.nearest_index(1.2e-3) == 2


def test_unordered_x_is_thinned_not_bucketed() -> None:
    x = np.array([0, 1, 2, 0, 1, 2], dtype=float)
    store = TraceStore.build("dc", x, "V", {"I(D1)": ("A", x * 2)})
    assert not store.ordered
    s = store.series("I(D1)", MAG, False, False, False, 0, 2, 100)
    assert len(s.x) == 6


def test_ticks_and_padding() -> None:
    assert nice_ticks(-90, 0, 6, degrees=True) == [-90, -75, -60, -45, -30, -15, 0]
    assert nice_ticks(0, 10, 5) == [0, 2, 4, 6, 8, 10]
    assert nice_ticks(1, 1) == []
    assert padded(0, 10) == (-0.5, 10.5)
    lo, hi = padded(2, 2)
    assert lo < 2 < hi
