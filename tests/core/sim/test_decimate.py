from __future__ import annotations

import time

import numpy as np
import pytest

from core.sim.decimate import MinMaxPyramid, minmax_buckets, phase_deg, to_db


def test_short_window_is_returned_unchanged() -> None:
    x = np.arange(10.0)
    xs, ys = minmax_buckets(x, x * 2, 0, 9, 100)
    np.testing.assert_array_equal(xs, x)
    np.testing.assert_array_equal(ys, x * 2)


def test_empty_and_single_point() -> None:
    xs, ys = minmax_buckets(np.zeros(0), np.zeros(0), 0, 1, 10)
    assert len(xs) == len(ys) == 0
    xs, ys = minmax_buckets(np.array([1.0]), np.array([5.0]), 0, 2, 10)
    assert list(xs) == [1.0] and list(ys) == [5.0]
    xs, ys = minmax_buckets(np.arange(5.0), np.arange(5.0), 0, 4, 0)
    assert len(xs) == 0


def test_point_count_is_bounded_and_x_ascending() -> None:
    x = np.linspace(0, 1, 1_000_000)
    y = np.sin(2 * np.pi * 50 * x)
    xs, ys = minmax_buckets(x, y, 0, 1, 800)
    assert len(xs) <= 2 * 800
    assert np.all(np.diff(xs) >= 0)
    assert len(xs) == len(ys)


def test_peaks_are_kept() -> None:
    x = np.arange(100_000, dtype=float)
    y = np.zeros_like(x)
    y[12_345] = 7.0
    y[67_890] = -3.0
    xs, ys = minmax_buckets(x, y, x[0], x[-1], 100)
    assert ys.max() == 7.0 and xs[ys.argmax()] == 12_345
    assert ys.min() == -3.0 and xs[ys.argmin()] == 67_890


def test_window_is_clipped_with_one_neighbour_each_side() -> None:
    x = np.arange(100_000, dtype=float)
    xs, _ = minmax_buckets(x, x, 1000.5, 2000.5, 100)
    assert xs[0] == 1000 and xs[-1] == 2001
    xs, _ = minmax_buckets(x, x, 2000.5, 1000.5, 100)  # reversed window
    assert xs[0] == 1000 and xs[-1] == 2001


def test_nan_buckets_do_not_crash() -> None:
    x = np.arange(10_000, dtype=float)
    y = np.full_like(x, np.nan)
    xs, ys = minmax_buckets(x, y, 0, x[-1], 10)
    assert len(xs) == len(ys) <= 20


def test_one_million_points_is_fast() -> None:
    x = np.linspace(0, 1, 1_000_000)
    y = np.random.default_rng(1).normal(size=x.size)
    minmax_buckets(x, y, 0, 1, 1000)  # warm up
    started = time.perf_counter()
    minmax_buckets(x, y, 0, 1, 1000)
    assert time.perf_counter() - started < 0.2


def test_db_and_phase() -> None:
    z = np.array([1.0, 1j, -1.0, 0.0, 10.0])
    db = to_db(z)
    assert db[0] == pytest.approx(0.0)
    assert db[4] == pytest.approx(20.0)
    assert np.isfinite(db[3])
    ph = phase_deg(np.exp(1j * np.linspace(0, -3 * np.pi, 50)))
    assert ph[-1] == pytest.approx(-540.0)


def test_pyramid_matches_buckets_peaks_and_bounds_points() -> None:
    x = np.linspace(0, 1, 1_000_000)
    y = np.sin(2 * np.pi * 50 * x)
    y[123_456] = 5.0  # a one-sample spike must survive
    pyramid = MinMaxPyramid(x, y)
    assert pyramid.levels
    xs, ys = pyramid.query(0, 1, 800)
    assert 0 < len(xs) <= 1600 and np.all(np.diff(xs) >= 0)
    assert ys.max() == 5.0 and ys.min() == pytest.approx(-1, abs=1e-6)
    zoomed_x, _ = pyramid.query(0.1, 0.1001, 800)
    assert zoomed_x.min() <= 0.1 and zoomed_x.max() >= 0.1001


def test_pyramid_short_and_empty() -> None:
    assert MinMaxPyramid(np.zeros(0), np.zeros(0)).query(0, 1, 10)[0].size == 0
    xs, _ = MinMaxPyramid(np.arange(10.0), np.arange(10.0)).query(0, 9, 100)
    assert len(xs) == 10


def test_pyramid_query_is_fast() -> None:
    x = np.linspace(0, 1, 1_000_000)
    pyramid = MinMaxPyramid(x, np.sin(x * 1e3))
    start = time.perf_counter()
    for _ in range(20):
        pyramid.query(0, 1, 1200)
    assert (time.perf_counter() - start) / 20 < 0.01
