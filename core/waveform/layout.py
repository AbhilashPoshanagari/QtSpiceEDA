"""PlotLayout: the panes of the waveform window for one analysis type (W1). Qt-free.

A layout holds the panes from top to bottom; each pane lists the trace names it shows
(the same name may sit in several panes, but only once in a pane), its own Y axis and,
for an AC Bode pane, the phase axis drawn on the right. All panes share one X axis.

Ranges are in real units (seconds, hertz, volts, dB for AC magnitude, degrees for
phase), not in plotted log10 coordinates. ``auto`` axes take their range from the data
in view; ``auto`` off keeps ``lo``/``hi``.

The zoom history (W3) is stored with the layout so that undoing an edit also restores
the history that went with it; it is not written to the project file.

``to_dict`` / ``from_dict`` give the form stored in the .qsp file; ``from_dict`` never
raises: it returns a usable layout plus Diagnostic(0, 0, severity, message) entries.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from core.netlist.model import ERROR, WARNING, Diagnostic

ANALYSES: tuple[str, ...] = ("tran", "ac", "dc")
MAX_PANES = 12
MAX_HISTORY = 50


@dataclass
class YAxis:
    """One Y axis. ``log`` plots log10 of the values; values <= 0 are then hidden,
    or plotted as |y| when ``show_abs`` is set (W5)."""

    auto: bool = True
    lo: float = -1.0
    hi: float = 1.0
    log: bool = False
    show_abs: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto": self.auto,
            "lo": self.lo,
            "hi": self.hi,
            "log": self.log,
            "abs": self.show_abs,
        }


@dataclass
class XAxis:
    auto: bool = True
    lo: float = 0.0
    hi: float = 1.0
    log: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"auto": self.auto, "lo": self.lo, "hi": self.hi, "log": self.log}


@dataclass
class Pane:
    traces: list[str] = field(default_factory=list)
    title: str = ""
    y: YAxis = field(default_factory=YAxis)
    phase: YAxis = field(default_factory=YAxis)  # AC: right axis, degrees
    grid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "traces": list(self.traces),
            "y": self.y.to_dict(),
            "phase": self.phase.to_dict(),
            "grid": self.grid,
        }


@dataclass
class View:
    """What a zoom changes: the X axis and every pane's Y and phase axes."""

    x: XAxis
    y: list[YAxis]
    phase: list[YAxis]


@dataclass
class PlotLayout:
    analysis: str = "tran"
    panes: list[Pane] = field(default_factory=lambda: [Pane()])
    x: XAxis = field(default_factory=XAxis)
    active: int = 0  # the pane new traces (probes, the trace list) go to
    history: list[View] = field(default_factory=list)  # zoom back (W3)

    @classmethod
    def default(cls, analysis: str) -> PlotLayout:
        """One empty pane; AC opens as a Bode plot (log frequency, dB and phase)."""
        layout = cls(analysis=analysis)
        if analysis == "ac":
            layout.x.log = True
        return layout

    @property
    def is_ac(self) -> bool:
        return self.analysis == "ac"

    def copy(self) -> PlotLayout:
        return copy.deepcopy(self)

    def _clamp(self, index: int) -> int:
        return min(max(index, 0), len(self.panes) - 1)

    # ---- panes (W2)

    def add_pane(self, index: int, above: bool) -> int:
        """Insert an empty pane above or below pane ``index``; returns its index and
        makes it the active pane. -1 when there are already MAX_PANES."""
        if len(self.panes) >= MAX_PANES:
            return -1
        index = self._clamp(index)
        at = index if above else index + 1
        self.panes.insert(at, Pane())
        self.active = at
        return at

    def delete_pane(self, index: int) -> bool:
        """Remove a pane and its traces. The last pane cannot be deleted."""
        if len(self.panes) <= 1 or not 0 <= index < len(self.panes):
            return False
        del self.panes[index]
        if self.active >= len(self.panes) or self.active > index:
            self.active = max(0, self.active - 1)
        return True

    def move_pane(self, index: int, to: int) -> bool:
        if not 0 <= index < len(self.panes) or not 0 <= to < len(self.panes) or index == to:
            return False
        pane = self.panes.pop(index)
        self.panes.insert(to, pane)
        self.active = to
        return True

    # ---- traces (W4)

    def all_traces(self) -> list[str]:
        """Every trace name in any pane, once, top pane first."""
        seen: dict[str, None] = {}
        for pane in self.panes:
            for name in pane.traces:
                seen.setdefault(name, None)
        return list(seen)

    def add_trace(self, pane: int, name: str) -> bool:
        if not name or not 0 <= pane < len(self.panes) or name in self.panes[pane].traces:
            return False
        self.panes[pane].traces.append(name)
        return True

    def remove_trace(self, pane: int, name: str) -> bool:
        if not 0 <= pane < len(self.panes) or name not in self.panes[pane].traces:
            return False
        self.panes[pane].traces.remove(name)
        return True

    def remove_everywhere(self, name: str) -> bool:
        changed = False
        for pane in self.panes:
            if name in pane.traces:
                pane.traces.remove(name)
                changed = True
        return changed

    def drop_trace(self, src: int, name: str, dst: int, copy_: bool) -> bool:
        """LTspice drag and drop: move ``name`` from pane ``src`` to ``dst``, or copy
        it (Ctrl). ``src`` -1 means from outside the panes (the trace list): add.
        Moving onto a pane that already shows it just removes it from ``src``."""
        if not 0 <= dst < len(self.panes):
            return False
        if src == dst:
            return False
        if src >= 0 and (src >= len(self.panes) or name not in self.panes[src].traces):
            return False
        added = self.add_trace(dst, name)
        removed = False
        if src >= 0 and not copy_:
            removed = self.remove_trace(src, name)
        return added or removed

    def show_only(self, pane: int, name: str) -> bool:
        """Double-click a trace name: its pane keeps only that trace."""
        if not 0 <= pane < len(self.panes) or name not in self.panes[pane].traces:
            return False
        if self.panes[pane].traces == [name]:
            return False
        self.panes[pane].traces = [name]
        return True

    # ---- zoom (W3)

    def view(self) -> View:
        return View(
            copy.deepcopy(self.x),
            [copy.deepcopy(p.y) for p in self.panes],
            [copy.deepcopy(p.phase) for p in self.panes],
        )

    def set_view(self, view: View) -> None:
        self.x = copy.deepcopy(view.x)
        for pane, y, ph in zip(self.panes, view.y, view.phase, strict=False):
            pane.y = copy.deepcopy(y)
            pane.phase = copy.deepcopy(ph)

    def push_history(self) -> None:
        self.history.append(self.view())
        del self.history[:-MAX_HISTORY]

    def zoom_back(self) -> bool:
        """Restore the view before the last zoom. False when there is none."""
        if not self.history:
            return False
        self.set_view(self.history.pop())
        return True

    def auto_range(self) -> None:
        """Every axis back to automatic (log/lin settings stay)."""
        self.x.auto = True
        for pane in self.panes:
            pane.y.auto = True
            pane.phase.auto = True

    # ---- serialisation

    def to_dict(self) -> dict[str, Any]:
        """The .qsp form (no zoom history, no active pane)."""
        return {"x": self.x.to_dict(), "panes": [p.to_dict() for p in self.panes]}

    @classmethod
    def from_dict(cls, data: object, analysis: str) -> tuple[PlotLayout, list[Diagnostic]]:
        """Read a layout; bad parts are replaced by defaults and reported."""
        found: list[Diagnostic] = []
        layout = cls.default(analysis)
        where = f"plots.{analysis}"

        def warn(text: str) -> None:
            found.append(Diagnostic(0, 0, WARNING, f"{where}: {text}"))

        if not isinstance(data, dict):
            found.append(Diagnostic(0, 0, ERROR, f"{where} must be an object"))
            return layout, found
        layout.x = _x_axis(data.get("x"), layout.x, warn)
        panes = data.get("panes")
        if not isinstance(panes, list) or not panes:
            warn("needs a non-empty list 'panes'; using one empty pane")
            return layout, found
        layout.panes = []
        for i, item in enumerate(panes[:MAX_PANES]):
            layout.panes.append(_pane(item, f"panes[{i}]", warn))
        if len(panes) > MAX_PANES:
            warn(f"only the first {MAX_PANES} panes are kept")
        return layout, found


def _num(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _axis_fields(data: dict[str, Any], target: XAxis | YAxis, name: str, warn) -> None:
    for key in ("auto", "log"):
        if key in data:
            if isinstance(data[key], bool):
                setattr(target, key, data[key])
            else:
                warn(f"{name}.{key} must be true or false")
    lo, hi = data.get("lo", target.lo), data.get("hi", target.hi)
    if _num(lo) and _num(hi) and float(lo) < float(hi):
        target.lo, target.hi = float(lo), float(hi)
    else:
        warn(f"{name}.lo and {name}.hi must be numbers with lo < hi; automatic range used")
        target.auto = True


def _x_axis(data: object, default: XAxis, warn) -> XAxis:
    axis = copy.deepcopy(default)
    if data is None:
        return axis
    if not isinstance(data, dict):
        warn("'x' must be an object")
        return axis
    _axis_fields(data, axis, "x", warn)
    return axis


def _y_axis(data: object, name: str, warn) -> YAxis:
    axis = YAxis()
    if data is None:
        return axis
    if not isinstance(data, dict):
        warn(f"{name} must be an object")
        return axis
    _axis_fields(data, axis, name, warn)
    if "abs" in data:
        if isinstance(data["abs"], bool):
            axis.show_abs = data["abs"]
        else:
            warn(f"{name}.abs must be true or false")
    return axis


def _pane(data: object, name: str, warn) -> Pane:
    pane = Pane()
    if not isinstance(data, dict):
        warn(f"{name} must be an object")
        return pane
    title = data.get("title", "")
    pane.title = title if isinstance(title, str) else ""
    traces = data.get("traces", [])
    if isinstance(traces, list):
        for t in traces:
            if isinstance(t, str) and t and t not in pane.traces:
                pane.traces.append(t)
            elif not isinstance(t, str) or not t:
                warn(f"{name}.traces holds a value that is not a trace name")
    else:
        warn(f"{name}.traces must be a list of names")
    pane.y = _y_axis(data.get("y"), f"{name}.y", warn)
    pane.phase = _y_axis(data.get("phase"), f"{name}.phase", warn)
    grid = data.get("grid", True)
    pane.grid = grid if isinstance(grid, bool) else True
    return pane


def layouts_to_dict(layouts: dict[str, PlotLayout]) -> dict[str, Any]:
    """{analysis: layout dict} in ANALYSES order, for the .qsp ``plots`` field."""
    return {a: layouts[a].to_dict() for a in ANALYSES if a in layouts}


def layouts_from_dict(data: object) -> tuple[dict[str, PlotLayout], list[Diagnostic]]:
    found: list[Diagnostic] = []
    out: dict[str, PlotLayout] = {}
    if not isinstance(data, dict):
        return out, [Diagnostic(0, 0, ERROR, "'plots' must be an object")]
    for key, value in data.items():
        if key not in ANALYSES:
            found.append(Diagnostic(0, 0, WARNING, f"plots: unknown analysis {key!r} ignored"))
            continue
        layout, diags = PlotLayout.from_dict(value, key)
        out[key] = layout
        found += diags
    return out, found
