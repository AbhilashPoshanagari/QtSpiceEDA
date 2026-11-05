"""Reduce long vectors for display, and AC helpers. Qt-free.

``minmax_buckets`` splits the visible x range into one bucket per pixel and keeps the
smallest and largest sample of each bucket, so a 1-million-point trace becomes at most
``2 * pixels`` points without losing peaks.
"""

from __future__ import annotations

import numpy as np

_TINY = 1e-300


def minmax_buckets(
    x: np.ndarray, y: np.ndarray, x0: float, x1: float, pixels: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(xs, ys)`` for the window ``[x0, x1]`` drawn ``pixels`` wide.

    ``x`` must be ascending. One sample on each side of the window is kept so the line
    reaches the plot edges. If the window holds no more than ``2 * pixels`` samples
    they are returned unchanged.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    if n == 0 or pixels < 1:
        return np.zeros(0), np.zeros(0)
    if x1 < x0:
        x0, x1 = x1, x0

    lo = max(int(np.searchsorted(x, x0, side="left")) - 1, 0)
    hi = min(int(np.searchsorted(x, x1, side="right")) + 1, n)
    xs, ys = x[lo:hi], y[lo:hi]
    if len(xs) <= 2 * pixels or x1 <= x0:
        return xs.copy(), ys.copy()

    # Bucket edges in x; counts per bucket via searchsorted on the sorted window.
    edges = np.linspace(x0, x1, pixels + 1)
    starts = np.searchsorted(xs, edges[:-1], side="left")
    starts[0] = 0  # the sample left of x0 joins the first bucket
    ends = np.empty_like(starts)
    ends[:-1] = starts[1:]
    ends[-1] = len(xs)  # the sample right of x1 joins the last bucket
    keep = ends > starts
    starts, ends = starts[keep], ends[keep]

    counts = ends - starts
    bucket = np.repeat(np.arange(len(starts)), counts)
    offset = starts[0]
    window = ys[offset : ends[-1]]
    xwin = xs[offset : ends[-1]]
    rel = starts - offset

    mins = np.minimum.reduceat(window, rel)
    maxs = np.maximum.reduceat(window, rel)
    i_min = _first_index(window == mins[bucket], bucket, len(rel))
    i_max = _first_index(window == maxs[bucket], bucket, len(rel))

    first = np.minimum(i_min, i_max)
    second = np.maximum(i_min, i_max)
    order = np.empty(2 * len(rel), dtype=np.int64)
    order[0::2] = first
    order[1::2] = second
    # Buckets whose min and max are the same sample would repeat it; drop the repeat.
    dup = np.zeros(len(order), dtype=bool)
    dup[1::2] = first == second
    order = order[~dup]
    return xwin[order].copy(), window[order].copy()


def _first_index(mask: np.ndarray, bucket: np.ndarray, buckets: int) -> np.ndarray:
    """Index of the first True of ``mask`` within each bucket (NaN buckets: bucket start)."""
    hits = np.flatnonzero(mask)
    result = np.full(buckets, -1, dtype=np.int64)
    found, where = np.unique(bucket[hits], return_index=True)
    result[found] = hits[where]
    missing = result < 0
    if missing.any():  # all-NaN bucket: fall back to its first sample
        result[missing] = np.searchsorted(bucket, np.flatnonzero(missing), side="left")
    return result


def to_db(values: np.ndarray) -> np.ndarray:
    """Magnitude in dB (20 log10 |v|); zero magnitude maps to a very low finite value."""
    return 20.0 * np.log10(np.maximum(np.abs(np.asarray(values)), _TINY))


def phase_deg(values: np.ndarray) -> np.ndarray:
    """Unwrapped phase in degrees."""
    return np.degrees(np.unwrap(np.angle(np.asarray(values))))
