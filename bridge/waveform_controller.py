"""WaveformController: the panes of the waveform window (W1-W6).

QML uses it as ``import QtSpice`` / ``WaveformController.addPane(0, true)``. It owns one
``PlotLayout`` per analysis type (.tran, .ac, .dc), follows the result shown by
``WaveformModel`` (``attach``), adds probed traces to the active pane
(``attachProbes(SchematicController)``) and handles drops, zooms and cursors.

Every pane, trace, axis and zoom change is a snapshot command on ``undoStack`` (W1,
CLAUDE.md rule 10); the zoom history travels in the layout, so undo restores it too.
Wheel and pan steps that follow each other merge into one undo step. Cursors are not
layout edits and are not undoable.

Coordinates: QML works in *display* coordinates (log10 of the value on a log axis);
the layout stores real units. ``fillPane`` puts the decimated points of one pane into
its LineSeries and returns that pane's Y range; an AC pane draws the phase of each
trace too, scaled onto the magnitude axis and labelled on the right (QtGraphs 6.8 has
one Y axis per GraphsView).
"""

from __future__ import annotations

import copy
import math
import weakref
from typing import Any

from PySide6.QtCore import Property, QObject, QPointF, Signal, Slot
from PySide6.QtGraphs import QXYSeries
from PySide6.QtGui import QColor, QUndoCommand, QUndoStack
from PySide6.QtQml import QmlElement, QmlSingleton

from bridge.waveform_model import si
from core.waveform.layout import ANALYSES, PlotLayout, layouts_to_dict
from core.waveform.traces import MAG, PHASE, TraceStore, nice_ticks, padded

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

COLORS = (
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#ff7f0e",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#17becf",
    "#7f7f7f",
    "#bcbd22",
)
MISSING_COLOR = "#a0a0a0"
_MERGE_VIEW = 1  # consecutive wheel / pan steps merge into one undo step


class _LayoutCommand(QUndoCommand):
    """One edit of one analysis' layout: the layout before and after."""

    def __init__(
        self,
        ctl: WaveformController,
        text: str,
        analysis: str,
        before: PlotLayout,
        after: PlotLayout,
        merge: int = -1,
    ) -> None:
        super().__init__(text)
        self._ctl = weakref.ref(ctl)
        self.analysis = analysis
        self.before = before
        self.after = after
        self._merge = merge

    def id(self) -> int:  # noqa: A003 - Qt API
        return self._merge

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802 - Qt API
        if not isinstance(other, _LayoutCommand) or other.analysis != self.analysis:
            return False
        self.after = other.after
        return True

    def redo(self) -> None:
        ctl = self._ctl()
        if ctl is not None:
            ctl._set_layout(self.analysis, self.after)

    def undo(self) -> None:
        ctl = self._ctl()
        if ctl is not None:
            ctl._set_layout(self.analysis, self.before)


@QmlElement
@QmlSingleton
class WaveformController(QObject):
    """Panes, traces, zoom and cursors of the waveform window."""

    layoutChanged = Signal()  # panes, traces or axes changed (redraw)
    dataChanged = Signal()  # a new result or a probe trace
    undoStateChanged = Signal()
    cursorsChanged = Signal()
    deleteModeChanged = Signal()
    layoutEdited = Signal()  # an undo step was done, undone or redone; or a load

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._undo = QUndoStack(self)
        for signal in (
            self._undo.canUndoChanged,
            self._undo.canRedoChanged,
            self._undo.undoTextChanged,
            self._undo.redoTextChanged,
        ):
            signal.connect(self.undoStateChanged)
        self._model: QObject | None = None
        self._result: object = None
        self._store = TraceStore()
        self._analysis = ""
        self._layouts: dict[str, PlotLayout] = {}
        self._ranges: dict[int, tuple[float, float, float, float]] = {}
        self._cursors: list[dict[str, Any] | None] = [None, None]
        self._delete_mode = False

    # ---- wiring

    @Slot(QObject)
    def attach(self, model: QObject) -> None:
        """Follow ``model`` (the WaveformModel). Safe to call more than once."""
        if model is self._model:
            return
        if self._model is not None:
            self._model.resultChanged.disconnect(self._on_result)  # type: ignore[attr-defined]
        self._model = model
        model.resultChanged.connect(self._on_result)  # type: ignore[attr-defined]
        self._on_result()

    @Slot(QObject)
    def attachProbes(self, schematic: QObject) -> None:  # noqa: N802 - QML API
        """Put every trace the schematic probes (S11) into the active pane."""
        schematic.probed.connect(self.probe)  # type: ignore[attr-defined]

    def undo_stack(self) -> QUndoStack:
        return self._undo

    # ---- project (W1)

    def layouts(self) -> dict[str, PlotLayout]:
        return {a: layout.copy() for a, layout in self._layouts.items()}

    def layouts_dict(self) -> dict[str, Any]:
        return layouts_to_dict(self._layouts)

    def load_layouts(self, layouts: dict[str, PlotLayout]) -> None:
        """Replace every layout (a project was opened); clears the undo history."""
        self._layouts = {a: layout.copy() for a, layout in layouts.items() if a in ANALYSES}
        self._undo.clear()
        self._cursors = [None, None]
        self._ranges = {}
        if self._analysis and self._analysis not in self._layouts:
            self._layouts[self._analysis] = self._initial_layout()
        self.layoutChanged.emit()
        self.cursorsChanged.emit()
        self.layoutEdited.emit()

    # ---- result

    @Slot()
    def _on_result(self) -> None:
        model = self._model
        if model is None:
            return
        result = model.result()  # type: ignore[attr-defined]
        x, traces = model.raw_data()  # type: ignore[attr-defined]
        analysis = getattr(result, "analysis", "") if result is not None else ""
        if not getattr(result, "axis", "") or analysis not in ANALYSES:
            analysis = ""
        new_result = result is not self._result
        self._result = result
        self._store = TraceStore.build(analysis, x, model.axis_unit(), traces)  # type: ignore[attr-defined]
        self._analysis = analysis
        if analysis:
            if analysis not in self._layouts:
                self._layouts[analysis] = self._initial_layout()
            elif new_result and not self._layouts[analysis].all_traces():
                self._layouts[analysis] = self._initial_layout(self._layouts[analysis])
        if new_result:
            self._ranges = {}
            self._cursors = [
                c if c is not None and self._store.has(c["name"]) else None for c in self._cursors
            ]
            self.cursorsChanged.emit()
        self.dataChanged.emit()
        self.layoutChanged.emit()

    def _initial_layout(self, base: PlotLayout | None = None) -> PlotLayout:
        """A new layout shows the traces that WaveformModel shows (as v0.1 did)."""
        layout = base.copy() if base is not None else PlotLayout.default(self._analysis)
        model = self._model
        if model is not None:
            for name in self._store.names():
                if model.isVisible(name):  # type: ignore[attr-defined]
                    layout.add_trace(min(layout.active, len(layout.panes) - 1), name)
        return layout

    # ---- editing

    def _layout(self) -> PlotLayout | None:
        return self._layouts.get(self._analysis) if self._analysis else None

    def _set_layout(self, analysis: str, layout: PlotLayout) -> None:
        self._layouts[analysis] = layout.copy()
        if analysis == self._analysis:
            count = len(layout.panes)
            self._ranges = {k: v for k, v in self._ranges.items() if k < count}
            for i, c in enumerate(self._cursors):
                if c is not None and (
                    c["pane"] >= count or c["name"] not in layout.panes[c["pane"]].traces
                ):
                    self._cursors[i] = None
            self.layoutChanged.emit()
            self.cursorsChanged.emit()
        self.layoutEdited.emit()

    def _edit(self, text: str, change, merge: int = -1) -> bool:
        """Apply ``change(layout) -> bool`` to a copy and push it as one undo step."""
        layout = self._layout()
        if layout is None:
            return False
        after = layout.copy()
        if not change(after):
            return False
        self._undo.push(_LayoutCommand(self, text, self._analysis, layout.copy(), after, merge))
        return True

    # ---- properties

    def _get_analysis(self) -> str:
        return self._analysis

    def _get_is_ac(self) -> bool:
        return self._analysis == "ac"

    def _get_has_data(self) -> bool:
        return bool(self._analysis) and len(self._store.x) > 0

    def _get_pane_count(self) -> int:
        layout = self._layout()
        return len(layout.panes) if layout else 0

    def _get_active_pane(self) -> int:
        layout = self._layout()
        return layout.active if layout else 0

    def _get_panes(self) -> list[dict[str, Any]]:
        layout = self._layout()
        if layout is None:
            return []
        out = []
        for i, pane in enumerate(layout.panes):
            traces = [
                {"name": n, "color": self.colorOf(n), "present": self._store.has(n)}
                for n in pane.traces
            ]
            present = sum(1 for t in traces if t["present"])
            out.append(
                {
                    "index": i,
                    "title": pane.title,
                    "traces": traces,
                    "active": i == layout.active,
                    "yLog": pane.y.log and not layout.is_ac,
                    "yAuto": pane.y.auto,
                    "showAbs": pane.y.show_abs,
                    "grid": pane.grid,
                    "seriesNeeded": present * (2 if layout.is_ac else 1),
                }
            )
        return out

    def _get_result_traces(self) -> list[dict[str, Any]]:
        layout = self._layout()
        shown = set(layout.all_traces()) if layout else set()
        return [
            {"name": n, "color": self.colorOf(n), "shown": n in shown} for n in self._store.names()
        ]

    def _get_x_log(self) -> bool:
        layout = self._layout()
        return bool(layout and layout.x.log)

    def _x_view(self) -> tuple[float, float]:
        """The X range in view, real units."""
        layout = self._layout()
        if layout is None:
            return 0.0, 1.0
        bounds = self._store.x_bounds(layout.x.log)
        if layout.x.auto or (layout.x.log and layout.x.lo <= 0):
            return bounds
        return layout.x.lo, layout.x.hi

    def _get_x_min(self) -> float:
        lo, _ = self._x_view()
        return self._store.display_x(lo, self._get_x_log())

    def _get_x_max(self) -> float:
        _, hi = self._x_view()
        return self._store.display_x(hi, self._get_x_log())

    def _get_x_label(self) -> str:
        result = self._result
        if result is None or not getattr(result, "axis", ""):
            return ""
        unit = self._store.axis_unit
        axis = result.axis  # type: ignore[attr-defined]
        return f"{axis} [{unit}]" if unit else axis

    def _get_can_undo(self) -> bool:
        return self._undo.canUndo()

    def _get_can_redo(self) -> bool:
        return self._undo.canRedo()

    def _get_undo_text(self) -> str:
        return self._undo.undoText()

    def _get_redo_text(self) -> str:
        return self._undo.redoText()

    def _get_can_zoom_back(self) -> bool:
        layout = self._layout()
        return bool(layout and layout.history)

    def _get_delete_mode(self) -> bool:
        return self._delete_mode

    def _set_delete_mode(self, on: bool) -> None:
        if on != self._delete_mode:
            self._delete_mode = on
            self.deleteModeChanged.emit()

    analysis = Property(str, _get_analysis, notify=layoutChanged)
    isAc = Property(bool, _get_is_ac, notify=layoutChanged)  # noqa: N815
    hasData = Property(bool, _get_has_data, notify=layoutChanged)  # noqa: N815
    paneCount = Property(int, _get_pane_count, notify=layoutChanged)  # noqa: N815
    activePane = Property(int, _get_active_pane, notify=layoutChanged)  # noqa: N815
    panes = Property("QVariantList", _get_panes, notify=layoutChanged)
    resultTraces = Property("QVariantList", _get_result_traces, notify=layoutChanged)  # noqa: N815
    xLog = Property(bool, _get_x_log, notify=layoutChanged)  # noqa: N815
    xMin = Property(float, _get_x_min, notify=layoutChanged)  # noqa: N815
    xMax = Property(float, _get_x_max, notify=layoutChanged)  # noqa: N815
    xLabel = Property(str, _get_x_label, notify=layoutChanged)  # noqa: N815
    canUndo = Property(bool, _get_can_undo, notify=undoStateChanged)  # noqa: N815
    canRedo = Property(bool, _get_can_redo, notify=undoStateChanged)  # noqa: N815
    undoText = Property(str, _get_undo_text, notify=undoStateChanged)  # noqa: N815
    redoText = Property(str, _get_redo_text, notify=undoStateChanged)  # noqa: N815
    canZoomBack = Property(bool, _get_can_zoom_back, notify=layoutChanged)  # noqa: N815
    deleteMode = Property(  # noqa: N815
        bool, _get_delete_mode, _set_delete_mode, notify=deleteModeChanged
    )

    @Slot(str, result=str)
    def colorOf(self, name: str) -> str:  # noqa: N802 - QML API
        names = self._store.names()
        if name not in names:
            return MISSING_COLOR
        return COLORS[names.index(name) % len(COLORS)]

    # ---- undo

    @Slot()
    def undo(self) -> None:
        self._undo.undo()

    @Slot()
    def redo(self) -> None:
        self._undo.redo()

    # ---- panes (W2)

    @Slot(int)
    def setActivePane(self, index: int) -> None:  # noqa: N802 - QML API
        """Choose where new traces go. Not a layout edit (no undo step)."""
        layout = self._layout()
        if layout is not None and 0 <= index < len(layout.panes) and index != layout.active:
            layout.active = index
            self.layoutChanged.emit()

    @Slot(int, bool, result=int)
    def addPane(self, index: int, above: bool) -> int:  # noqa: N802 - QML API
        """Add an empty pane above or below pane ``index``; returns its index or -1."""
        where: list[int] = []
        text = "Add pane above" if above else "Add pane below"

        def change(layout: PlotLayout) -> bool:
            where.append(layout.add_pane(index, above))
            return where[0] >= 0

        return where[0] if self._edit(text, change) else -1

    @Slot(int, result=bool)
    def deletePane(self, index: int) -> bool:  # noqa: N802 - QML API
        return self._edit("Delete pane", lambda layout: layout.delete_pane(index))

    @Slot(int, int, result=bool)
    def movePane(self, index: int, to: int) -> bool:  # noqa: N802 - QML API
        return self._edit("Move pane", lambda layout: layout.move_pane(index, to))

    @Slot(result="QVariantMap")
    def config(self) -> dict[str, Any]:
        """The layout as PlotConfigDialog edits it: {x, panes: [{from, title, y,
        phase, grid, traces}]} with ranges in real units."""
        layout = self._layout()
        if layout is None:
            return {}
        lo, hi = self._x_view()
        data = layout.to_dict()
        data["x"].update({"lo": lo, "hi": hi})
        for i, pane in enumerate(data["panes"]):
            pane["from"] = i
            ranges = self._ranges.get(i)
            if ranges is not None:
                ylo, yhi, plo, phi = ranges
                if layout.panes[i].y.auto:
                    log = layout.panes[i].y.log and not layout.is_ac
                    pane["y"].update({"lo": 10**ylo if log else ylo, "hi": 10**yhi if log else yhi})
                if layout.panes[i].phase.auto and layout.is_ac:
                    pane["phase"].update({"lo": plo, "hi": phi})
        data["isAc"] = layout.is_ac
        return data

    @Slot("QVariantMap", result=bool)
    def configure(self, data: dict[str, Any]) -> bool:
        """Apply PlotConfigDialog: titles, ranges, axes and pane order, one undo step.
        Bad numbers leave that axis unchanged."""
        data = _plain(data)

        def change(layout: PlotLayout) -> bool:
            before = layout.to_dict()
            old = layout.panes
            panes = []
            for item in data.get("panes", []):
                src = item.get("from", -1)
                if not isinstance(src, int | float) or not 0 <= int(src) < len(old):
                    continue
                pane = copy.deepcopy(old[int(src)])
                if isinstance(item.get("title"), str):
                    pane.title = item["title"]
                if isinstance(item.get("grid"), bool):
                    pane.grid = item["grid"]
                _set_axis(pane.y, item.get("y"))
                _set_axis(pane.phase, item.get("phase"))
                panes.append(pane)
            if panes:
                layout.panes = panes
                layout.active = min(layout.active, len(panes) - 1)
            _set_axis(layout.x, data.get("x"))
            if layout.x.log and not layout.x.auto and layout.x.lo <= 0:
                layout.x.auto = True
            return layout.to_dict() != before

        return self._edit("Configure plot", change)

    @Slot(bool, result=bool)
    def setXLog(self, on: bool) -> bool:  # noqa: N802 - QML API
        def change(layout: PlotLayout) -> bool:
            if layout.x.log == on:
                return False
            layout.x.log = on
            layout.x.auto = True
            return True

        return self._edit("Log X axis" if on else "Linear X axis", change)

    @Slot(int, bool, result=bool)
    def setYLog(self, pane: int, on: bool) -> bool:  # noqa: N802 - QML API
        def change(layout: PlotLayout) -> bool:
            if not 0 <= pane < len(layout.panes) or layout.is_ac:
                return False
            axis = layout.panes[pane].y
            if axis.log == on:
                return False
            axis.log = on
            axis.auto = True
            return True

        return self._edit("Log Y axis" if on else "Linear Y axis", change)

    @Slot(int, bool, result=bool)
    def setShowAbs(self, pane: int, on: bool) -> bool:  # noqa: N802 - QML API
        def change(layout: PlotLayout) -> bool:
            if not 0 <= pane < len(layout.panes):
                return False
            axis = layout.panes[pane].y
            if axis.show_abs == on:
                return False
            axis.show_abs = on
            return True

        return self._edit("Plot |y| on log axis" if on else "Hide values <= 0", change)

    # ---- traces (W4)

    @Slot(str, result=bool)
    def addTrace(self, name: str) -> bool:  # noqa: N802 - QML API
        """Add ``name`` to the active pane."""
        return self._edit(f"Add {name}", lambda layout: layout.add_trace(layout.active, name))

    @Slot(str)
    def probe(self, name: str) -> None:
        """A schematic probe (S11) showed ``name``: put it in the active pane."""
        self.addTrace(name)

    @Slot(int, str, result=bool)
    def removeTrace(self, pane: int, name: str) -> bool:  # noqa: N802 - QML API
        return self._edit(f"Remove {name}", lambda layout: layout.remove_trace(pane, name))

    @Slot(str, bool, result=bool)
    def setTraceShown(self, name: str, shown: bool) -> bool:  # noqa: N802 - QML API
        """Trace list checkbox: on adds to the active pane, off removes from all."""
        if shown:
            return self.addTrace(name)
        return self._edit(f"Remove {name}", lambda layout: layout.remove_everywhere(name))

    @Slot(int, str, int, bool, result=bool)
    def dropTrace(self, src: int, name: str, dst: int, copy_: bool) -> bool:  # noqa: N802
        """Drop a dragged trace name on pane ``dst`` (``src`` -1: from the trace list)."""
        text = f"Copy {name}" if copy_ or src < 0 else f"Move {name}"
        return self._edit(text, lambda layout: layout.drop_trace(src, name, dst, copy_))

    @Slot(int, str, result=bool)
    def showOnly(self, pane: int, name: str) -> bool:  # noqa: N802 - QML API
        return self._edit(f"Show only {name}", lambda layout: layout.show_only(pane, name))

    # ---- zoom (W3)

    @Slot(int, float, float, float, float, result=bool)
    def zoomBox(self, pane: int, x0: float, x1: float, y0: float, y1: float) -> bool:  # noqa: N802
        """Zoom to a box drawn in pane ``pane`` (display coordinates). X applies to
        every pane; Y only to this one (its phase axis zooms in proportion)."""
        if x0 == x1 or y0 == y1:
            return False
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        ranges = self._ranges.get(pane)

        def change(layout: PlotLayout) -> bool:
            if not 0 <= pane < len(layout.panes):
                return False
            layout.push_history()
            store = self._store
            layout.x.auto = False
            layout.x.lo, layout.x.hi = (
                store.real_x(x0, layout.x.log),
                store.real_x(x1, layout.x.log),
            )
            target = layout.panes[pane]
            log = target.y.log and not layout.is_ac
            target.y.auto = False
            target.y.lo, target.y.hi = (10**y0, 10**y1) if log else (y0, y1)
            if layout.is_ac and ranges is not None:
                ylo, yhi, plo, phi = ranges
                if yhi > ylo:
                    scale = (phi - plo) / (yhi - ylo)
                    target.phase.auto = False
                    target.phase.lo = plo + (y0 - ylo) * scale
                    target.phase.hi = plo + (y1 - ylo) * scale
            return True

        return self._edit("Zoom", change)

    @Slot(float, float, result=bool)
    def zoomX(self, factor: float, anchor: float) -> bool:  # noqa: N802 - QML API
        """Wheel zoom of X around display ``anchor`` (factor < 1 zooms in)."""
        lo, hi = self._get_x_min(), self._get_x_max()
        if not (math.isfinite(factor) and factor > 0 and hi > lo):
            return False
        span = (hi - lo) * factor
        full_lo, full_hi = (
            self._store.display_x(v, self._get_x_log())
            for v in self._store.x_bounds(self._get_x_log())
        )
        if span < (full_hi - full_lo) * 1e-9:
            return False
        new_lo = anchor - (anchor - lo) * factor
        new_hi = new_lo + span
        return self._set_x("Zoom", new_lo, new_hi, span >= full_hi - full_lo)

    @Slot(float, result=bool)
    def panX(self, delta: float) -> bool:  # noqa: N802 - QML API
        """Shift the X view by ``delta`` (display units)."""
        if delta == 0 or not math.isfinite(delta):
            return False
        return self._set_x("Pan", self._get_x_min() + delta, self._get_x_max() + delta, False)

    def _set_x(self, text: str, lo: float, hi: float, full: bool) -> bool:
        def change(layout: PlotLayout) -> bool:
            if full:
                if layout.x.auto:
                    return False
                layout.x.auto = True
                return True
            layout.x.auto = False
            layout.x.lo = self._store.real_x(lo, layout.x.log)
            layout.x.hi = self._store.real_x(hi, layout.x.log)
            return True

        return self._edit(text, change, _MERGE_VIEW)

    @Slot(result=bool)
    def zoomBack(self) -> bool:  # noqa: N802 - QML API
        return self._edit("Zoom back", lambda layout: layout.zoom_back())

    @Slot(result=bool)
    def autoRange(self) -> bool:  # noqa: N802 - QML API
        def change(layout: PlotLayout) -> bool:
            before = layout.view()
            if (
                before.x.auto
                and all(y.auto for y in before.y)
                and all(p.auto for p in before.phase)
            ):
                return False
            layout.push_history()
            layout.auto_range()
            return True

        return self._edit("Auto-range", change)

    # ---- drawing

    @Slot(int, "QVariantList", int, result="QVariantMap")
    def fillPane(self, pane: int, series: list, pixels: int) -> dict[str, Any]:  # noqa: N802
        """Fill ``series`` (LineSeries, at least ``seriesNeeded`` of them) with the
        traces of ``pane`` and return {yMin, yMax, hidden, rightTicks, series}."""
        layout = self._layout()
        empty: dict[str, Any] = {
            "yMin": -1.0,
            "yMax": 1.0,
            "hidden": 0,
            "rightTicks": [],
            "series": [],
        }
        if layout is None or not 0 <= pane < len(layout.panes):
            for s in series:
                if isinstance(s, QXYSeries):
                    s.clear()
                    s.setVisible(False)
            return empty
        p = layout.panes[pane]
        store = self._store
        ac = layout.is_ac
        log_y = p.y.log and not ac
        x0, x1 = self._get_x_min(), self._get_x_max()
        pixels = max(1, int(pixels))
        names = [n for n in p.traces if store.has(n)]
        mags = [
            store.series(n, MAG, layout.x.log, log_y, p.y.show_abs, x0, x1, pixels) for n in names
        ]
        phases = (
            [store.series(n, PHASE, layout.x.log, False, False, x0, x1, pixels) for n in names]
            if ac
            else []
        )

        if p.y.auto:
            ylo, yhi = padded(*_extent([s.y for s in mags], [s.x for s in mags], x0, x1))
        else:
            ylo, yhi = (
                (math.log10(p.y.lo), math.log10(p.y.hi))
                if log_y and p.y.lo > 0
                else (p.y.lo, p.y.hi)
            )
        plo, phi = -1.0, 1.0
        ticks: list[dict[str, Any]] = []
        if ac:
            if p.phase.auto:
                extent = _extent([s.y for s in phases], [s.x for s in phases], x0, x1)
                plo, phi = _phase_range(extent)
            else:
                plo, phi = p.phase.lo, p.phase.hi
            scale = (yhi - ylo) / (phi - plo) if phi > plo else 1.0
            for value in nice_ticks(plo, phi, 6, degrees=True):
                ticks.append({"y": ylo + (value - plo) * scale, "text": f"{value:.4g}°"})
        self._ranges[pane] = (ylo, yhi, plo, phi)

        state = []
        used = 0
        for i, name in enumerate(names):
            color = QColor(self.colorOf(name))
            used = _fill(series, used, mags[i].x, mags[i].y, name, color, 1.5)
            state.append({"name": name, "count": len(mags[i].x), "kind": MAG})
            if ac:
                py = ylo + (phases[i].y - plo) * ((yhi - ylo) / (phi - plo) if phi > plo else 1.0)
                faded = QColor(color)
                faded.setAlpha(150)
                used = _fill(series, used, phases[i].x, py, name, faded, 1.0)
                state.append({"name": name, "count": len(phases[i].x), "kind": PHASE})
        for s in series[used:]:
            if isinstance(s, QXYSeries):
                s.clear()
                s.setVisible(False)
        return {
            "yMin": ylo,
            "yMax": yhi,
            "hidden": sum(s.hidden for s in mags),
            "rightTicks": ticks,
            "series": state,
        }

    # ---- formatting

    @Slot(float, result=str)
    def formatX(self, x: float) -> str:  # noqa: N802 - QML API
        """Display X as text, e.g. "1.5 ms" or "159.2 Hz"."""
        return si(self._store.real_x(x, self._get_x_log()), self._store.axis_unit)

    @Slot(float, float, result=str)
    def formatDeltaX(self, a: float, b: float) -> str:  # noqa: N802 - QML API
        """|b - a| in real units and, as a frequency, 1/|b - a| (W3, W6)."""
        log = self._get_x_log()
        delta = abs(self._store.real_x(b, log) - self._store.real_x(a, log))
        text = si(delta, self._store.axis_unit)
        if self._store.axis_unit == "s" and delta > 0:
            text += f"  (1/Δ = {si(1 / delta, 'Hz')})"
        return text

    @Slot(int, float, result=str)
    def formatY(self, pane: int, y: float) -> str:  # noqa: N802 - QML API
        """A display Y of pane ``pane`` as text (log axes show 10^y)."""
        layout = self._layout()
        if layout is None or not 0 <= pane < len(layout.panes):
            return f"{y:.4g}"
        p = layout.panes[pane]
        if layout.is_ac:
            return f"{y:.4g} dB"
        unit = next((self._store.unit(n) for n in p.traces if self._store.has(n)), "")
        if p.y.log:
            return si(10.0**y, unit)
        return si(y, unit)

    @Slot(int, float, float, result=str)
    def formatDeltaY(self, pane: int, a: float, b: float) -> str:  # noqa: N802 - QML API
        layout = self._layout()
        if layout is None or not 0 <= pane < len(layout.panes):
            return ""
        p = layout.panes[pane]
        if p.y.log and not layout.is_ac:
            ratio = 10.0 ** abs(b - a)
            return f"×{ratio:.4g}"
        return self.formatY(pane, abs(b - a))

    # ---- cursors (W6)

    @Slot(int, int, str, result=bool)
    def attachCursor(self, which: int, pane: int, name: str) -> bool:  # noqa: N802
        """Attach cursor 1, 2 or both (3) to trace ``name`` of pane ``pane``."""
        layout = self._layout()
        if (
            layout is None
            or not 0 <= pane < len(layout.panes)
            or name not in layout.panes[pane].traces
            or not self._store.has(name)
        ):
            return False
        lo, hi = self._x_view()
        log = layout.x.log
        for n in (1, 2):
            if not which & n:
                continue
            old = self._cursors[n - 1]
            if old is not None and lo <= old["x"] <= hi:
                x = old["x"]
            else:  # one third / two thirds across the view
                d0, d1 = self._store.display_x(lo, log), self._store.display_x(hi, log)
                x = self._store.real_x(d0 + (d1 - d0) * n / 3, log)
            self._cursors[n - 1] = {"pane": pane, "name": name, "x": x}
        self.cursorsChanged.emit()
        return True

    @Slot()
    def detachCursors(self) -> None:  # noqa: N802 - QML API
        if self._cursors != [None, None]:
            self._cursors = [None, None]
            self.cursorsChanged.emit()

    @Slot(int, float, result=bool)
    def setCursorX(self, which: int, x: float) -> bool:  # noqa: N802 - QML API
        """Put cursor ``which`` at real axis value ``x`` (clamped to the data)."""
        c = self._cursor(which)
        if c is None or not len(self._store.x) or not math.isfinite(x):
            return False
        lo, hi = float(self._store.x.min()), float(self._store.x.max())
        c["x"] = min(max(x, lo), hi)
        self.cursorsChanged.emit()
        return True

    @Slot(int, float, result=bool)
    def setCursorDisplayX(self, which: int, d: float) -> bool:  # noqa: N802 - QML API
        """Dragging: cursor ``which`` to display X ``d``."""
        return self.setCursorX(which, self._store.real_x(d, self._get_x_log()))

    @Slot(int, int, result=bool)
    def stepCursor(self, which: int, direction: int) -> bool:  # noqa: N802 - QML API
        """Arrow keys: move cursor ``which`` to the next / previous data point."""
        c = self._cursor(which)
        if c is None:
            return False
        x = self._store.step(c["x"], 1 if direction > 0 else -1)
        if x == c["x"]:
            return False
        c["x"] = x
        self.cursorsChanged.emit()
        return True

    def _cursor(self, which: int) -> dict[str, Any] | None:
        return self._cursors[which - 1] if which in (1, 2) else None

    def _cursor_point(self, c: dict[str, Any]) -> dict[str, Any]:
        layout = self._layout()
        store = self._store
        log_x = bool(layout and layout.x.log)
        pane = layout.panes[c["pane"]] if layout and c["pane"] < len(layout.panes) else None
        value = store.value_at(c["name"], c["x"], MAG)
        out: dict[str, Any] = {
            "pane": c["pane"],
            "name": c["name"],
            "color": self.colorOf(c["name"]),
            "x": c["x"],
            "value": value,
            "dx": store.display_x(c["x"], log_x),
            "xText": si(c["x"], store.axis_unit),
        }
        log_y = bool(pane and pane.y.log and not store.is_ac)
        if log_y:
            y = abs(value) if pane and pane.y.show_abs else value
            out["dy"] = math.log10(y) if y > 0 else math.nan
        else:
            out["dy"] = value
        if store.is_ac:
            phase = store.value_at(c["name"], c["x"], PHASE)
            out["phase"] = phase
            out["yText"] = f"{value:.4g} dB"
            out["phaseText"] = f"{phase:.4g}°"
            ranges = self._ranges.get(c["pane"])
            if ranges is not None and ranges[3] > ranges[2]:
                ylo, yhi, plo, phi = ranges
                out["dyPhase"] = ylo + (phase - plo) * (yhi - ylo) / (phi - plo)
        else:
            out["yText"] = si(value, store.unit(c["name"]))
        return out

    def _get_cursors(self) -> list[dict[str, Any]]:
        """[{which, pane, name, color, x, xText, value, yText, (phase, phaseText),
        dx, dy}] for each attached cursor (dx/dy in display coordinates)."""
        out = []
        for i, c in enumerate(self._cursors):
            if c is not None:
                point = self._cursor_point(c)
                point["which"] = i + 1
                out.append(point)
        return out

    def _get_cursor_diff(self) -> dict[str, Any]:
        """Cursor 2 minus cursor 1: {dxText, freqText, dyText, (dPhaseText)}; {} unless
        both are attached."""
        a, b = self._cursors
        if a is None or b is None:
            return {}
        pa, pb = self._cursor_point(a), self._cursor_point(b)
        store = self._store
        dx = pb["x"] - pa["x"]
        out: dict[str, Any] = {
            "dx": dx,
            "dxText": si(dx, store.axis_unit),
            "freqText": si(1 / abs(dx), "Hz") if dx != 0 and store.axis_unit == "s" else "",
        }
        if store.is_ac:
            out["dyText"] = f"{pb['value'] - pa['value']:.4g} dB"
            out["dPhaseText"] = f"{pb['phase'] - pa['phase']:.4g}°"
            out["freqText"] = ""
        else:
            unit = store.unit(a["name"]) if store.unit(a["name"]) == store.unit(b["name"]) else ""
            out["dyText"] = si(pb["value"] - pa["value"], unit)
        return out

    cursors = Property("QVariantList", _get_cursors, notify=cursorsChanged)
    cursorDiff = Property("QVariantMap", _get_cursor_diff, notify=cursorsChanged)  # noqa: N815


# ---- helpers


def _plain(value: Any) -> Any:
    """QJSValue / QVariant containers from QML as plain Python values."""
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _set_axis(axis: Any, data: object) -> None:
    if not isinstance(data, dict):
        return
    for key, attr in (("auto", "auto"), ("log", "log"), ("abs", "show_abs")):
        if isinstance(data.get(key), bool) and hasattr(axis, attr):
            setattr(axis, attr, data[key])
    lo, hi = data.get("lo"), data.get("hi")
    ok = all(
        isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
        for v in (lo, hi)
    )
    if ok and float(lo) < float(hi):  # type: ignore[arg-type]
        axis.lo, axis.hi = float(lo), float(hi)  # type: ignore[arg-type]


def _extent(ys: list, xs: list, x0: float, x1: float) -> tuple[float, float]:
    """Min and max of the points inside [x0, x1] (the decimated arrays keep peaks)."""
    lo, hi = math.inf, -math.inf
    for x, y in zip(xs, ys, strict=True):
        if not len(y):
            continue
        inside = (x >= min(x0, x1)) & (x <= max(x0, x1))
        window = y[inside] if inside.any() else y
        lo, hi = min(lo, float(window.min())), max(hi, float(window.max()))
    return lo, hi


def _phase_range(extent: tuple[float, float]) -> tuple[float, float]:
    """Phase axis: the data range rounded out to multiples of 15 degrees."""
    lo, hi = extent
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return -180.0, 180.0
    lo, hi = math.floor(lo / 15) * 15, math.ceil(hi / 15) * 15
    if hi - lo < 15:
        lo, hi = lo - 15, hi + 15
    return float(lo), float(hi)


def _fill(series: list, used: int, x, y, name: str, color: QColor, width: float) -> int:
    if used >= len(series) or not isinstance(series[used], QXYSeries):
        return used + 1
    s = series[used]
    ok = [(a, b) for a, b in zip(x.tolist(), y.tolist(), strict=True) if math.isfinite(b)]
    s.replace([QPointF(a, b) for a, b in ok])
    s.setName(name)
    s.setColor(color)
    s.setProperty("width", width)
    s.setVisible(True)
    return used + 1
