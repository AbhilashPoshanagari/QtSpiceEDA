"""Trace data in plotted coordinates for the waveform panes (W1, W5). Qt-free.

``TraceStore`` holds the axis and the trace vectors of one result (complex for AC).
For a pane it returns decimated (x, y) arrays in *display* coordinates:

- X: the axis value, or log10 of it on a log X axis (points with x <= 0 dropped);
- Y: the real value (.tran/.dc) or the magnitude in dB (.ac), or log10 of it on a log
  Y axis. On a log Y axis values <= 0 are left out and counted ("n points hidden"),
  or plotted as |y| when the axis asks for that;
- phase (AC): unwrapped degrees, plotted against the right axis.

Each (trace, kind, log X, log Y, |y|) combination gets a MinMaxPyramid once, so a
redraw costs O(pixels) however many points the result has.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from core.sim.decimate import MinMaxPyramid, phase_deg, to_db

MAG, PHASE = "mag", "phase"


@dataclass
class Series:
    """One trace drawn in a pane: display arrays plus how many points were hidden."""

    x: np.ndarray
    y: np.ndarray
    hidden: int = 0


@dataclass
class _Trace:
    unit: str
    values: np.ndarray  # in ascending-x order; complex for AC


@dataclass
class TraceStore:
    analysis: str = ""
    axis_unit: str = ""
    x: np.ndarray = field(default_factory=lambda: np.zeros(0))  # real axis values
    ordered: bool = True
    _traces: dict[str, _Trace] = field(default_factory=dict)
    _cache: dict[tuple, tuple[MinMaxPyramid | None, np.ndarray, np.ndarray, int]] = field(
        default_factory=dict
    )

    @classmethod
    def build(
        cls, analysis: str, x: np.ndarray, axis_unit: str, traces: dict[str, tuple[str, np.ndarray]]
    ) -> TraceStore:
        """``x`` and every ``(unit, values)`` must already be in ascending-x order
        (WaveformModel reverses a falling .dc sweep)."""
        x = np.asarray(x).real.astype(float)
        store = cls(analysis, axis_unit, x, bool(len(x) < 2 or np.all(np.diff(x) >= 0)))
        for name, (unit, values) in traces.items():
            store.add(name, unit, values)
        return store

    @property
    def is_ac(self) -> bool:
        return self.analysis == "ac"

    def names(self) -> list[str]:
        return list(self._traces)

    def has(self, name: str) -> bool:
        return name in self._traces

    def unit(self, name: str) -> str:
        trace = self._traces.get(name)
        return trace.unit if trace else ""

    def add(self, name: str, unit: str, values: np.ndarray) -> bool:
        values = np.asarray(values)
        if values.shape != self.x.shape:
            return False
        if not self.is_ac:
            values = np.real(values).astype(float)
        self._traces[name] = _Trace(unit, values)
        self._cache = {k: v for k, v in self._cache.items() if k[0] != name}
        return True

    # ---- values

    def values(self, name: str, kind: str = MAG) -> np.ndarray | None:
        """Real values: .tran/.dc as stored, AC magnitude in dB or phase in degrees."""
        trace = self._traces.get(name)
        if trace is None:
            return None
        if self.is_ac:
            return phase_deg(trace.values) if kind == PHASE else to_db(trace.values)
        return trace.values

    def value_at(self, name: str, x: float, kind: str = MAG) -> float:
        """Value at real axis value ``x`` (linear interpolation in display X)."""
        y = self.values(name, kind)
        if y is None or not len(self.x) or not self.ordered:
            return math.nan
        return float(np.interp(x, self.x, y))

    def nearest_index(self, x: float) -> int:
        if not len(self.x):
            return -1
        i = int(np.searchsorted(self.x, x))
        if i <= 0:
            return 0
        if i >= len(self.x):
            return len(self.x) - 1
        return i if abs(self.x[i] - x) < abs(x - self.x[i - 1]) else i - 1

    def step(self, x: float, direction: int) -> float:
        """The next data point's x after (direction > 0) or before ``x`` (W6 arrows)."""
        if not len(self.x):
            return x
        if direction > 0:
            i = int(np.searchsorted(self.x, x, "right"))
        else:
            i = int(np.searchsorted(self.x, x, "left")) - 1
        i = min(max(i, 0), len(self.x) - 1)
        return float(self.x[i])

    # ---- display

    def display_x(self, x: float, log: bool) -> float:
        if log:
            return math.log10(x) if x > 0 else math.nan
        return x

    def real_x(self, d: float, log: bool) -> float:
        return 10.0**d if log else d

    def x_bounds(self, log: bool) -> tuple[float, float]:
        """Real axis bounds (positive part only on a log axis)."""
        x = self.x[self.x > 0] if log else self.x
        if not len(x):
            return (1.0, 10.0) if log else (0.0, 1.0)
        lo, hi = float(np.min(x)), float(np.max(x))
        if hi <= lo:
            hi = lo * 10 if log else lo + 1
        return lo, hi

    def series(
        self,
        name: str,
        kind: str,
        log_x: bool,
        log_y: bool,
        show_abs: bool,
        x0: float,
        x1: float,
        pixels: int,
    ) -> Series:
        """Decimated display arrays of ``name`` for display X ``x0``..``x1``."""
        key = (name, kind, log_x, log_y and kind == MAG, show_abs and log_y)
        if key not in self._cache:
            self._cache[key] = self._prepare(*key)
        pyramid, xs, ys, hidden = self._cache[key]
        if pyramid is None:  # unordered X (nested .dc): thin out, no bucketing
            step = max(1, len(xs) // max(1, 4 * pixels))
            return Series(xs[::step].copy(), ys[::step].copy(), hidden)
        px, py = pyramid.query(x0, x1, max(1, int(pixels)))
        return Series(px, py, hidden)

    def _prepare(
        self, name: str, kind: str, log_x: bool, log_y: bool, show_abs: bool
    ) -> tuple[MinMaxPyramid | None, np.ndarray, np.ndarray, int]:
        y = self.values(name, kind)
        if y is None:
            return None, np.zeros(0), np.zeros(0), 0
        x = self.x
        keep = np.isfinite(x) & np.isfinite(y)
        if log_x:
            keep &= x > 0
        hidden = 0
        if log_y:
            if show_abs:
                y = np.abs(y)
            positive = y > 0
            hidden = int(np.count_nonzero(keep & ~positive))
            keep &= positive
        xs, ys = x[keep], y[keep]
        if log_x:
            xs = np.log10(xs)
        if log_y:
            ys = np.log10(ys)
        if not self.ordered:
            return None, xs, ys, hidden
        return MinMaxPyramid(xs, ys), xs, ys, hidden


def nice_ticks(lo: float, hi: float, count: int = 6, degrees: bool = False) -> list[float]:
    """Round tick values covering [lo, hi] (about ``count`` of them). ``degrees``
    prefers steps of 15, 30, 45, 90 and 180."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return []
    raw = (hi - lo) / max(1, count)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    if degrees and raw > 7.5:
        step = next((d for d in (15, 30, 45, 90, 180, 360) if d >= raw), step)
    first = math.ceil(lo / step - 1e-9) * step
    out = []
    v = first
    while v <= hi + step * 1e-9 and len(out) < 100:
        out.append(0.0 if abs(v) < step * 1e-9 else v)
        v += step
    return out


def padded(lo: float, hi: float, fraction: float = 0.05) -> tuple[float, float]:
    """[lo, hi] widened by ``fraction`` each side; a flat range gets a small span."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return -1.0, 1.0
    if hi - lo < 1e-12 * max(1.0, abs(hi)):
        pad = max(abs(hi) * 0.05, 1e-12)
    else:
        pad = (hi - lo) * fraction
    return lo - pad, hi + pad


def log_minor_ticks(lo: float, hi: float, max_decades: float = 8) -> list[float]:
    """log10(k * 10^n) for k = 2..9 inside display range [lo, hi] of a log axis."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo or hi - lo > max_decades:
        return []
    out = []
    for decade in range(math.floor(lo), math.ceil(hi) + 1):
        for k in range(2, 10):
            v = decade + math.log10(k)
            if lo <= v <= hi:
                out.append(v)
    return out
