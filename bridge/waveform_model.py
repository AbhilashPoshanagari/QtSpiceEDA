"""WaveformModel: the QML-facing view of the last SimulationResult.

All x values exchanged with QML are *display* coordinates: the axis value itself for
.tran and .dc, and log10(frequency) for .ac (QtGraphs 6.8 has no log axis, so the
view plots log10 f on a linear axis and labels it with ``formatX``). AC traces are
shown as magnitude in dB and, with ``phase=True``, phase in degrees.

``samples`` / ``fillSeries`` reduce a trace to at most two points per pixel (min and
max per bucket), so a million-point result redraws quickly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import Property, QObject, QPointF, QUrl, Signal, Slot
from PySide6.QtGraphs import QXYSeries
from PySide6.QtQml import QmlElement, QmlSingleton

from core.sim.decimate import minmax_buckets, phase_deg, to_db
from core.sim.result import SimulationResult

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

_PREFIXES = [
    (1e12, "T"),
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "µ"),
    (1e-9, "n"),
    (1e-12, "p"),
    (1e-15, "f"),
]


NO_PREFIX_UNITS = frozenset({"pu", "km", "%"})
"""Units that are shown without an SI prefix (0.98 pu, not 980 mpu)."""


def si(value: float, unit: str = "", digits: int = 4) -> str:
    """Format with an SI prefix: si(0.00159, "s") == "1.59 ms".

    Units in NO_PREFIX_UNITS keep the plain number: si(0.98234, "pu") == "0.9823 pu".
    """
    if not math.isfinite(value):
        return "—"
    if unit in NO_PREFIX_UNITS:
        return f"{value:.{digits}g} {unit}"
    if value == 0:
        return f"0 {unit}".rstrip()
    magnitude = abs(value)
    scale, prefix = next(((s, p) for s, p in _PREFIXES if magnitude >= s * 0.9995), _PREFIXES[-1])
    text = f"{value / scale:.{digits}g}"
    return f"{text} {prefix}{unit}".rstrip()


@dataclass
class _Trace:
    name: str
    unit: str
    y: np.ndarray  # display values: real part, or dB for AC
    phase: np.ndarray | None  # degrees, AC only
    visible: bool = True
    raw: np.ndarray | None = None  # the values in ascending-x order (complex for AC)


@QmlElement
@QmlSingleton
class WaveformModel(QObject):
    """Traces of the last result, their visibility, and decimated samples for drawing."""

    resultChanged = Signal()
    visibilityChanged = Signal()
    errorStringChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._result: SimulationResult | None = None
        self._x = np.zeros(0)
        self._x_real = np.zeros(0)
        self._traces: dict[str, _Trace] = {}
        self._ordered = True
        self._order: slice = slice(None)
        self._error_string = ""

    # ---- loading

    def set_result(self, result: SimulationResult | None, show_new: bool = True) -> None:
        """Show ``result`` (None clears). Keeps the visibility of traces that exist in
        both the old and the new result; the other traces start visible only when
        ``show_new`` is true (netlist runs pass False: the window starts empty and the
        user adds traces, as in LTspice)."""
        old_visible = {name: t.visible for name, t in self._traces.items()}
        self._result = result
        self._traces = {}
        self._x = np.zeros(0)
        self._x_real = np.zeros(0)
        self._ordered = True
        if result is not None and result.axis:
            x = np.asarray(result.vectors[result.axis]).real.astype(float)
            ac = result.analysis == "ac"
            order = slice(None)
            if len(x) > 1 and x[0] > x[-1]:  # e.g. ".dc V1 5 0 -1"
                order = slice(None, None, -1)
            x = x[order]
            self._order = order
            self._x_real = x
            if ac:
                x = np.log10(np.maximum(x, 1e-300))
            self._ordered = bool(np.all(np.diff(x) >= 0))
            self._x = x
            for name in result.trace_names():
                if np.asarray(result.vectors[name]).shape != x.shape:
                    continue  # e.g. device currents ngspice leaves empty in .ac (S11)
                data = np.asarray(result.vectors[name])[order]
                unit = result.units.get(name, "")
                if ac:
                    self._traces[name] = _Trace(name, unit, to_db(data), phase_deg(data))
                else:
                    self._traces[name] = _Trace(name, unit, np.real(data).astype(float), None)
                self._traces[name].visible = old_visible.get(name, show_new)
                self._traces[name].raw = data
        self.resultChanged.emit()
        self.visibilityChanged.emit()

    @Slot()
    def clear(self) -> None:
        self.set_result(None)

    def add_trace(self, name: str, unit: str, data: np.ndarray, visible: bool = True) -> bool:
        """Add (or replace) a trace computed from the current result, such as a
        schematic probe's V(a,b) (S11). ``data`` is in the result's own point order."""
        if self._result is None or not self._result.axis:
            return False
        values = np.asarray(data)
        if values.shape != np.asarray(self._result.vectors[self._result.axis]).shape:
            return False
        values = values[self._order]
        if self._result.analysis == "ac":
            trace = _Trace(name, unit, to_db(values), phase_deg(values))
        else:
            trace = _Trace(name, unit, np.real(values).astype(float), None)
        trace.visible = visible
        trace.raw = values
        self._traces[name] = trace
        self.resultChanged.emit()
        self.visibilityChanged.emit()
        return True

    def result(self) -> SimulationResult | None:
        return self._result

    def raw_data(self) -> tuple[np.ndarray, dict[str, tuple[str, np.ndarray]]]:
        """(axis values, {name: (unit, values)}) in ascending-x order, real frequencies
        and complex values for AC; probe traces included (for WaveformController)."""
        traces = {name: (t.unit, t.raw) for name, t in self._traces.items() if t.raw is not None}
        return self._x_real, traces

    def axis_unit(self) -> str:
        return self._x_unit()

    def show_only(self, names: list[str]) -> None:
        """Make exactly the traces in ``names`` visible (the others stay listed)."""
        wanted = set(names)
        for trace in self._traces.values():
            trace.visible = trace.name in wanted
        self.visibilityChanged.emit()

    # ---- properties

    def _get_trace_names(self) -> list[str]:
        return list(self._traces)

    def _get_traces(self) -> list[dict[str, object]]:
        return [
            {"name": t.name, "unit": t.unit, "visible": t.visible} for t in self._traces.values()
        ]

    def _get_analysis(self) -> str:
        return self._result.analysis if self._result else ""

    def _get_is_ac(self) -> bool:
        return self._get_analysis() == "ac"

    def _get_has_axis(self) -> bool:
        return len(self._x) > 0

    def _get_x_min(self) -> float:
        return float(self._x[0]) if len(self._x) and self._ordered else self._x_bound(min)

    def _get_x_max(self) -> float:
        return float(self._x[-1]) if len(self._x) and self._ordered else self._x_bound(max)

    def _x_bound(self, pick) -> float:
        return float(pick(self._x)) if len(self._x) else 0.0

    def _get_x_label(self) -> str:
        if self._result is None or not self._result.axis:
            return ""
        unit = self._result.units.get(self._result.axis, "")
        return f"{self._result.axis} [{unit}]" if unit else self._result.axis

    def _get_point_count(self) -> int:
        return len(self._x)

    def _get_error_string(self) -> str:
        return self._error_string

    traceNames = Property("QStringList", _get_trace_names, notify=resultChanged)  # noqa: N815
    traces = Property("QVariantList", _get_traces, notify=visibilityChanged)
    analysis = Property(str, _get_analysis, notify=resultChanged)
    isAc = Property(bool, _get_is_ac, notify=resultChanged)  # noqa: N815
    hasAxis = Property(bool, _get_has_axis, notify=resultChanged)  # noqa: N815
    xMin = Property(float, _get_x_min, notify=resultChanged)  # noqa: N815
    xMax = Property(float, _get_x_max, notify=resultChanged)  # noqa: N815
    xLabel = Property(str, _get_x_label, notify=resultChanged)  # noqa: N815
    pointCount = Property(int, _get_point_count, notify=resultChanged)  # noqa: N815
    errorString = Property(str, _get_error_string, notify=errorStringChanged)  # noqa: N815

    # ---- visibility

    @Slot(str, bool)
    def setVisible(self, name: str, visible: bool) -> None:  # noqa: N802 - QML API
        trace = self._traces.get(name)
        if trace is not None and trace.visible != visible:
            trace.visible = visible
            self.visibilityChanged.emit()

    @Slot(str, result=bool)
    def isVisible(self, name: str) -> bool:  # noqa: N802 - QML API
        trace = self._traces.get(name)
        return trace is not None and trace.visible

    # ---- data for drawing

    def arrays(
        self, name: str, x0: float, x1: float, pixels: int, phase: bool = False
    ) -> tuple[np.ndarray, np.ndarray]:
        """Decimated ``(x, y)`` arrays of one trace in display coordinates."""
        trace = self._traces.get(name)
        if trace is None or not len(self._x):
            return np.zeros(0), np.zeros(0)
        y = trace.phase if phase else trace.y
        if y is None:
            return np.zeros(0), np.zeros(0)
        if not self._ordered:  # e.g. nested .dc sweeps: no x bucketing, just thin out
            step = max(1, len(self._x) // max(1, 4 * pixels))
            return self._x[::step].copy(), y[::step].copy()
        return minmax_buckets(self._x, y, x0, x1, max(1, int(pixels)))

    @Slot(str, float, float, int, result="QVariantList")
    def samples(self, name: str, x0: float, x1: float, pixels: int) -> list[QPointF]:
        """Points of ``name`` between ``x0`` and ``x1`` reduced to min/max per pixel.
        AC traces are in dB."""
        return _points(*self.arrays(name, x0, x1, pixels))

    @Slot(str, float, float, int, result="QVariantList")
    def phaseSamples(self, name: str, x0: float, x1: float, pixels: int) -> list[QPointF]:  # noqa: N802
        """Like ``samples`` but the AC phase in degrees (empty for other analyses)."""
        return _points(*self.arrays(name, x0, x1, pixels, phase=True))

    @Slot(QObject, str, float, float, int, bool, result=int)
    def fillSeries(  # noqa: N802 - QML API
        self, series: QObject, name: str, x0: float, x1: float, pixels: int, phase: bool
    ) -> int:
        """Replace the points of a QtGraphs LineSeries with ``samples`` (or phase).
        Returns the number of points."""
        if not isinstance(series, QXYSeries):
            return 0
        points = _points(*self.arrays(name, x0, x1, pixels, phase))
        series.replace(points)
        return len(points)

    @Slot(float, float, bool, result="QVariantList")
    def yRange(self, x0: float, x1: float, phase: bool) -> list[float]:  # noqa: N802
        """[low, high] of the visible traces between x0 and x1, padded by 5 %."""
        lo, hi = math.inf, -math.inf
        if len(self._x):
            if self._ordered:
                a = max(int(np.searchsorted(self._x, min(x0, x1), "left")) - 1, 0)
                b = min(int(np.searchsorted(self._x, max(x0, x1), "right")) + 1, len(self._x))
            else:
                a, b = 0, len(self._x)
            for trace in self._traces.values():
                y = trace.phase if phase else trace.y
                if not trace.visible or y is None or b <= a:
                    continue
                window = y[a:b]
                finite = window[np.isfinite(window)]
                if len(finite):
                    lo, hi = min(lo, float(finite.min())), max(hi, float(finite.max()))
        if not math.isfinite(lo):
            return [-1.0, 1.0]
        if hi - lo < 1e-12 * max(1.0, abs(hi)):
            pad = max(abs(hi) * 0.05, 1e-12)
        else:
            pad = (hi - lo) * 0.05
        return [lo - pad, hi + pad]

    @Slot(str, float, bool, result=float)
    def valueAt(self, name: str, x: float, phase: bool) -> float:  # noqa: N802
        """Trace value at display x (linear interpolation; NaN if unknown)."""
        trace = self._traces.get(name)
        if trace is None or not len(self._x) or not self._ordered:
            return math.nan
        y = trace.phase if phase else trace.y
        if y is None:
            return math.nan
        return float(np.interp(x, self._x, y))

    # ---- formatting for axis labels and cursor readouts

    def _real_x(self, x: float) -> float:
        return 10.0**x if self._get_is_ac() else x

    def _x_unit(self) -> str:
        if self._result is None or not self._result.axis:
            return ""
        return self._result.units.get(self._result.axis, "")

    @Slot(float, result=str)
    def formatX(self, x: float) -> str:  # noqa: N802 - QML API
        """Display x as text, e.g. "1.5 ms" or (AC) "159.2 Hz"."""
        return si(self._real_x(x), self._x_unit())

    @Slot(float, float, result=str)
    def formatDeltaX(self, xa: float, xb: float) -> str:  # noqa: N802 - QML API
        """xb - xa in real units; for AC also the ratio in decades."""
        delta = si(self._real_x(xb) - self._real_x(xa), self._x_unit())
        if self._get_is_ac():
            return f"{delta} ({xb - xa:.3g} dec)"
        return delta

    @Slot(str, float, bool, result=str)
    def formatY(self, name: str, value: float, phase: bool) -> str:  # noqa: N802 - QML API
        if not math.isfinite(value):
            return "—"
        if phase:
            return f"{value:.4g}°"
        if self._get_is_ac():
            return f"{value:.4g} dB"
        trace = self._traces.get(name)
        return si(value, trace.unit if trace else "")

    # ---- export

    @Slot("QVariant", result=bool)
    def exportCsv(self, url: QUrl | str) -> bool:  # noqa: N802 - QML API
        """Write every vector of the result (axis first, real frequencies, complex AC
        values as .re/.im columns) to a CSV file."""
        if self._result is None:
            self._set_error("Nothing to export: run a simulation first")
            return False
        qurl = url if isinstance(url, QUrl) else QUrl(str(url))
        path = Path(qurl.toLocalFile()) if qurl.isLocalFile() else Path(str(url))
        try:
            self._result.to_csv(path)
        except OSError as exc:
            self._set_error(f"Cannot write {path.name}: {exc.strerror or exc}")
            return False
        self._set_error("")
        return True

    def _set_error(self, text: str) -> None:
        if text != self._error_string:
            self._error_string = text
            self.errorStringChanged.emit()


def _points(xs: np.ndarray, ys: np.ndarray) -> list[QPointF]:
    ok = np.isfinite(xs) & np.isfinite(ys)
    return list(map(QPointF, xs[ok].tolist(), ys[ok].tolist()))
