"""GridDiagramController: the Grid Network Designer's bridge (D5).

QML uses it as ``import QtSpice`` / ``GridDiagramController { id: designer }``.

One controller owns one design (a GridDesign: GridModel + DiagramModel) and one
QUndoStack (rule 10). Every editing slot builds a core command from
core/grid/diagram/commands.py, applies it and pushes it as one QUndoCommand (rule 22);
QML never changes a model directly. A command that fails its precondition changes
nothing, pushes nothing and goes to the Console. ``moveGlyph`` calls with the same
``gesture`` (one mouse press, ``newGesture``) merge into one undo step.

Models for QML, derived from the design after every change (rule 20: every line drawn
comes from GridModel terminals, never from the diagram):

- ``busGlyphs``: one row per bus (id "bus.<name>", position, orientation, length,
  voltage zone, available phases, selected, severity);
- ``elementGlyphs``: one row per drawn element (id, kind, position, rotation, label
  offset, open switch, regulated transformer, selected, severity);
- ``edges``: what connects them: each branch from bus bar to bus bar (a transformer
  from winding 1 to each other winding) and each shunt's stub from its glyph to its
  bus bar; a disconnected terminal draws nothing;
- ``selection`` (ids; view state, not undoable) and ``propertySheet``: the fields of
  the one selected element or bus as rows {field, label, unit, value, editable,
  type, choices, diagnostic};
- ``libraryItems``: what can be added; only the v0.4 model classes, plus the bus and
  the "Regulator" (a single-phase transformer + RegControl).

Validation: after each electrical change a 50 ms single-shot QTimer runs validate.py
(topology checks included) and diagram.model.check_consistency; ``validated`` is
emitted, the diagnostics are kept per element id (glyph severities, property sheet)
and listed in the Console (``consoleText``), as the other editors do.

Files: ``newDesign`` (the base frequency is required, never assumed), ``load`` /
``save`` (.qgrid via core/grid/design_io.py), ``exportDss`` (writer.py, with the bus
glyph positions as bus coordinates) and ``openDssAsDesign``: the .dss file is read the
way the G6 view reads it, in a GridRunner child (``solve=False``), never in this
process; the original file is never written (rule 28).

Run (rule 23): ``runPowerFlow`` validates, then hands a copy of the GridModel to
``GridController.runModel``; its GridRunner child writes the DSS script with writer.py
(saved under ``<results>/grid/<design>/``), solves it in its own OpenDSSEngine and
sends the result back (``resultReady``). The designer never calls OpenDSS. Without
dss-python every slot but ``runPowerFlow`` and ``openDssAsDesign`` works; those two
refuse and ``runDisabledReason`` says why.

Results (D8): after a run, the FeasibilityReport (Level 1 + Level 2 of the snapshot,
analysis.feasibility_report) is ``feasibility`` and listed in the Console; the results
overlay (analysis.results_overlay) is ``overlay`` (status, values, tooltip per id),
``overlayPaths`` (edges as one SVG path per status and width step) and
``overlayBuses``. Both come from the model and settings that were run. Any change
after a run (an edit, undo, redo, a move, new settings) makes them ``resultsStale``
until the next run - even an undo back to the state that was run. The voltage profile
goes to the waveform window through GridController.show_profile (rule 7).
``focusElement`` selects a bus or element and gives its position (click-to-select in
the feasibility panel).
"""

from __future__ import annotations

import copy
import dataclasses
import math
import re
import threading
import types
import typing
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtQml import QmlElement, qmlEngine

import bridge.grid_controller as grid_controller
from bridge.grid_controller import (
    MAX_CONSOLE_LINES,
    _fmt,
    _path_of,
    overlay_limits,
    overlay_map,
    overlay_paths,
)
from bridge.schematic_controller import SyncModel
from core.grid import design_io
from core.grid.analysis import (
    CHECK_LINE_LOADING,
    CHECK_TRANSFORMER_LOADING,
    CHECK_VOLTAGE_MAX,
    CHECK_VOLTAGE_MIN,
    SEVERITY_STATUS,
    SNAPSHOT_LIMITATION,
    FeasibilityCheck,
    FeasibilityReport,
    ResultsOverlay,
    feasibility_report,
    results_overlay,
)
from core.grid.config import PowerFlowConfig
from core.grid.design_io import GridDesign
from core.grid.diagram.commands import (
    KINDS,
    NAME_PATTERN,
    REQUIRED_VALUES,
    AddBus,
    AddElement,
    AddRegulator,
    Command,
    Compound,
    Connect,
    Disconnect,
    MoveGlyph,
    MoveGlyphs,
    RemoveBus,
    RemoveElement,
    Rename,
    RotateGlyph,
    SetProperty,
    SetSwitchOpen,
    field_label,
    read_only_reason,
    resolve,
)
from core.grid.diagram.hittest import HitIndex, design_shapes, element_segments
from core.grid.diagram.layout import build_diagram
from core.grid.diagram.model import DiagramModel, check_consistency
from core.grid.engines.opendss.writer import write as write_dss
from core.grid.model import (
    CONNECTIONS,
    LOAD_MODELS,
    Capacitor,
    GridModel,
    Line,
    Load,
    PVSystem,
    Regulator,
    Source,
    Switch,
    Terminal,
    Transformer,
    Winding,
)
from core.grid.result import GridResult
from core.grid.runner import CANCELLED, OK, GridRunner, GridRunOutcome
from core.grid.topology import GridGraph, connected_elements
from core.grid.validate import validate
from core.netlist.model import ERROR, INFO, WARNING
from core.physical.component import NetworkDiagnostic

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

VALIDATE_DELAY_MS = 50

_SEVERITY_RANK = {ERROR: 3, WARNING: 2, INFO: 1}

BUS_ROLES = ["glyphId", "name", "x", "y", "orientation", "length", "zoneKv", "phases",
             "selected", "severity"]  # fmt: skip
ELEMENT_ROLES = ["glyphId", "kind", "name", "x", "y", "rotation", "labelDx", "labelDy",
                 "open", "regulated", "selected", "severity"]  # fmt: skip
EDGE_ROLES = ["edgeId", "elementId", "kind", "x1", "y1", "x2", "y2", "open", "selected",
              "severity"]  # fmt: skip

_DRAWN_KIND = {"sources": "vsource", "lines": "line", "switches": "switch",
               "transformers": "transformer", "loads": "load", "capacitors": "capacitor",
               "pvsystems": "pvsystem"}  # fmt: skip
_SHUNT_KINDS = ("vsource", "load", "capacitor", "pvsystem")
_BRANCH_KINDS = ("line", "switch", "transformer", "regulator")

LIBRARY: tuple[tuple[str, str, int, int, bool], ...] = (
    # key, label, smallest and largest number of terminals, drawn
    ("bus", "Bus", 0, 0, True),
    ("vsource", "Source", 1, 1, True),
    ("linecode", "Line code", 0, 0, False),
    ("line", "Line", 2, 2, True),
    ("switch", "Switch", 2, 2, True),
    ("transformer", "Transformer", 2, 3, True),
    ("regulator", "Regulator", 2, 2, True),
    ("load", "Load", 1, 1, True),
    ("capacitor", "Capacitor", 1, 1, True),
    ("pvsystem", "PV system", 1, 1, True),
)
"""The designer's library: the v0.4 model classes only (CLAUDE.md "out of scope")."""

_REGULATOR_REQUIRED = REQUIRED_VALUES["regcontrol"]

# Canonical units of the property sheet (CLAUDE.md "Grid model"); display only.
_UNITS = {
    "kv": "kV", "kv_base_ln": "kV", "kw": "kW", "kvar": "kvar", "kva": "kVA",
    "pmpp_kw": "kW", "length_m": "m", "r_matrix": "ohm/km", "x_matrix": "ohm/km",
    "c_matrix": "nF/km", "norm_amps": "A", "emerg_amps": "A", "ct_primary": "A",
    "r_ohm": "ohm", "x_ohm": "ohm", "r1_ohm": "ohm", "x1_ohm": "ohm", "r0_ohm": "ohm",
    "x0_ohm": "ohm", "rneut_ohm": "ohm", "xneut_ohm": "ohm", "pct_r": "%", "xhl": "%",
    "xht": "%", "xlt": "%", "pct_noload_loss": "%", "pct_imag": "%", "pu": "pu",
    "tap": "pu", "min_tap": "pu", "max_tap": "pu", "vminpu": "pu", "vmaxpu": "pu",
    "angle_deg": "°", "vreg": "V", "band": "V", "r": "V", "x": "V", "delay_s": "s",
    "tap_delay_s": "s", "irradiance": "kW/m²", "ppm_antifloat": "ppm",
}  # fmt: skip

_HIDDEN = {"name", "generated", "x", "y"}
"""Fields not shown: the name has its own row (Rename); Bus x/y are the .dss file's
coordinates, which the designer replaces by glyph positions."""

_CODE_FIELDS = {
    "unknown_linecode": "linecode",
    "nonpositive_length": "length_m",
    "linecode_phases": "phases",
    "element_kv": "kv",
    "transformer_kva": "kva",
}
"""validate.py codes whose diagnostic belongs to one property-sheet row."""

_TERMINAL_LABELS = {"bus1": "bus 1", "bus2": "bus 2", "terminal": "terminal"}

_CHECK_LABELS = {
    CHECK_VOLTAGE_MIN: "Lowest voltage",
    CHECK_VOLTAGE_MAX: "Highest voltage",
    CHECK_LINE_LOADING: "Line loading",
    CHECK_TRANSFORMER_LOADING: "Transformer loading",
}

_SPLIT = re.compile(r"[\s,;]+")


def engine_available() -> bool:
    """OpenDSSEngine.available() as the G6 view sees it (tests replace it there)."""
    return bool(grid_controller.engine_available())


def _has_error(diags: list[NetworkDiagnostic]) -> bool:
    return any(d.severity == ERROR for d in diags)


def _finite(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ------------------------------------------------------------------ input values


def _split_optional(tp: Any) -> tuple[Any, bool]:
    if typing.get_origin(tp) in (types.UnionType, typing.Union):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return (args[0] if len(args) == 1 else tp), len(args) < len(typing.get_args(tp))
    return tp, False


def _field_type(element: Any, path: str) -> Any:
    """The type hint of a SetProperty path of ``element`` (None if unknown)."""
    parts = path.split(".")
    cls: type = type(element)
    if len(parts) == 3 and parts[0] == "windings":
        cls, parts = Winding, parts[2:]
    if len(parts) != 1:
        return None
    return typing.get_type_hints(cls).get(parts[0])


def _class_field_type(cls: type, path: str) -> Any:
    parts = path.split(".")
    if len(parts) == 3 and parts[0] == "windings":
        cls, parts = Winding, parts[2:]
    return typing.get_type_hints(cls).get(parts[0]) if len(parts) == 1 else None


def _from_text(inner: Any, text: str) -> Any:
    t = text.strip()
    if inner is float:
        try:
            return float(t)
        except ValueError:
            return text
    if inner is int:
        try:
            return int(t)
        except ValueError:
            try:
                f = float(t)
            except ValueError:
                return text
            return int(f) if f.is_integer() else f
    if inner is bool:
        low = t.lower()
        if low in ("true", "yes", "1", "on"):
            return True
        if low in ("false", "no", "0", "off"):
            return False
        return text
    if typing.get_origin(inner) is tuple:
        item = typing.get_args(inner)[0]
        return [_from_text(item, p) for p in _SPLIT.split(t) if p]
    return text


def coerce_input(tp: Any, value: object) -> Any:
    """A value from QML (text from a field, or a JS number) as the field type ``tp``
    wants it: "" -> None for an optional field, "1.5" -> 1.5, 3.0 -> 3 for an int,
    "1 2 3" -> [1.0, 2.0, 3.0] for a matrix. Anything that does not parse is passed on
    unchanged, so the core command reports it (it never raises)."""
    if tp is None:
        return value
    inner, nullable = _split_optional(tp)
    if isinstance(value, str):
        if not value.strip() and nullable:
            return None
        if inner is str:
            return value
        return _from_text(inner, value)
    if inner is int and isinstance(value, float) and value.is_integer():
        return int(value)
    if typing.get_origin(inner) is tuple and isinstance(value, (list, tuple)):
        item = typing.get_args(inner)[0]
        return [coerce_input(item, v) for v in value]
    return value


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return " ".join(_display(v) for v in value)
    return str(value)


def _type_name(tp: Any) -> str:
    inner, _ = _split_optional(tp)
    if inner is bool:
        return "bool"
    if inner is int:
        return "int"
    if inner is float:
        return "number"
    if typing.get_origin(inner) is tuple:
        return "list"
    return "text"


# ------------------------------------------------------------------ undo


class _UndoCommand(QUndoCommand):
    """One core command on the QUndoStack (rule 22). The command is applied before it
    is pushed, so the first ``redo`` (QUndoStack.push) does nothing; later redos apply
    it again and undos revert it. The stack owns the commands, so a command keeps only
    a weak reference to the controller (as in SchematicController)."""

    def __init__(self, ctl: GridDiagramController, cmd: Command, electrical: bool) -> None:
        super().__init__(cmd.description)
        self._ctl = weakref.ref(ctl)
        self.cmd = cmd
        self.electrical = electrical
        self._pushed = False
        self.setObsolete(cmd.obsolete)

    def redo(self) -> None:
        if not self._pushed:
            self._pushed = True
            return
        ctl = self._ctl()
        if ctl is not None:
            ctl._replay(self.cmd.apply, self.electrical, self.cmd)

    def undo(self) -> None:
        ctl = self._ctl()
        if ctl is not None:
            ctl._replay(self.cmd.revert, self.electrical, self.cmd)

    def id(self) -> int:
        return self.cmd.merge_id

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802 - Qt API
        if not isinstance(other, _UndoCommand) or not self.cmd.merge(other.cmd):
            return False
        self.setText(self.cmd.description)
        self.setObsolete(self.cmd.obsolete)
        return True


# ------------------------------------------------------------------ the controller


@QmlElement
class GridDiagramController(QObject):
    """Edit one grid design, check it while it is edited, and run it."""

    designChanged = Signal()  # a new design or none (name, path, frequency)
    diagramChanged = Signal()  # glyphs or edges changed (after an edit, undo or redo)
    selectionChanged = Signal()
    propertySheetChanged = Signal()
    undoStateChanged = Signal()
    modifiedChanged = Signal()
    diagnosticsChanged = Signal()
    validated = Signal()  # the debounced check ran
    busyChanged = Signal()
    statusTextChanged = Signal()
    consoleTextChanged = Signal()
    resultChanged = Signal()  # a new result or none (summary, feasibility, overlay)
    staleChanged = Signal()
    overlayPathsChanged = Signal()  # the result or the drawing changed
    lineCodesChanged = Signal()  # only when the list changes (not on every move)
    resultReady = Signal()
    configChanged = Signal()  # new settings or limits of the next run (set_config)
    editRefused = Signal(str)  # a command was not applied: why (also in the Console)
    designOpened = Signal()  # openDssAsDesign or load finished with a design
    openFailed = Signal()
    _opened = Signal(object)  # (runner, outcome) from the open thread

    def __init__(
        self, parent: QObject | None = None, grid_controller: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._grid_controller = grid_controller
        self.config = PowerFlowConfig()
        """Settings and limits of the next run (per design in the project)."""
        self.timeout: float | None = None
        """Open timeout in s; None = GridRunner's default. Tests may set it."""
        self._design: GridDesign | None = None
        self._path = ""
        self._results_folder = ""
        self._undo = QUndoStack(self)
        self._selection: list[str] = []
        self._gesture = 0
        self._diagnostics: list[NetworkDiagnostic] = []
        self._by_id: dict[str, list[NetworkDiagnostic]] = {}
        self._checked = False
        self._result: GridResult | None = None
        self._report: FeasibilityReport | None = None
        self._overlay = ResultsOverlay()
        self._stale = False
        self._run_model: GridModel | None = None
        """A copy of the GridModel of the last run (the result's model)."""
        self._run_config = self.config
        self._last_outcome: GridRunOutcome | None = None
        self._opener: GridRunner | None = None
        self._open_thread: threading.Thread | None = None
        self._running = False
        self._listening: QObject | None = None
        self._status = ""
        self._log_lines: list[str] = []
        self._buses = SyncModel(BUS_ROLES, self)
        self._elements = SyncModel(ELEMENT_ROLES, self)
        self._edges = SyncModel(EDGE_ROLES, self)
        self._sheet: list[dict[str, Any]] = []
        self._paths: dict[str, str] = {}
        self._codes: list[dict[str, Any]] = []
        self._edge_rows: list[dict[str, Any]] = []
        self._row_of: dict[str, int] = {}
        """Row of each glyph id in busGlyphs / elementGlyphs (after a full refresh)."""
        self._edges_of: dict[str, list[int]] = {}
        """Edge rows of each element id."""
        self._drawn: dict[str, tuple[str, Any]] = {}
        self._at_bus: dict[str, list[str]] = {}
        """Caches of the incremental redraw; cleared by every full refresh."""
        self._index: HitIndex | None = None
        """Hit-test index of what is drawn; built on the first query after a change."""
        self._graph: tuple[GridGraph | None, dict[str, float]] | None = None
        """Topology of the design for the glyph rows; kept over presentation edits."""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(VALIDATE_DELAY_MS)
        self._timer.timeout.connect(self._validate_now)
        # QUndoStack sends all of these on every push, merged moves included; QML hears
        # only real changes (a drag on IEEE 123 must stay inside a frame)
        self._undo_state: tuple[Any, ...] = ()
        self._was_modified = False
        for signal in (
            self._undo.canUndoChanged,
            self._undo.canRedoChanged,
            self._undo.undoTextChanged,
            self._undo.redoTextChanged,
            self._undo.cleanChanged,
            self._undo.indexChanged,
        ):
            signal.connect(self._on_undo_state)
        self._opened.connect(self._on_opened, Qt.ConnectionType.QueuedConnection)
        self.diagramChanged.connect(self.overlayPathsChanged)
        self.resultChanged.connect(self.overlayPathsChanged)

    # ================================================================ Python side

    def design(self) -> GridDesign | None:
        """The live design (do not change it: use the slots)."""
        return self._design

    def undo_stack(self) -> QUndoStack:
        return self._undo

    def diagnostics(self) -> list[NetworkDiagnostic]:
        """The last check's diagnostics (``validated``)."""
        return list(self._diagnostics)

    def result(self) -> GridResult | None:
        return self._result

    def report(self) -> FeasibilityReport | None:
        """The FeasibilityReport of the last run (None before a run)."""
        return self._report

    def results_overlay(self) -> ResultsOverlay:
        return self._overlay

    def last_outcome(self) -> GridRunOutcome | None:
        return self._last_outcome

    def set_config(self, config: PowerFlowConfig) -> None:
        """Settings and limits of the next run; results already shown become stale.
        ``configChanged`` only when they change (the project keeps them per design)."""
        if config == self.config:
            return
        self._mark_stale()
        self.config = config
        self.configChanged.emit()

    def set_grid_controller(self, ctl: QObject | None) -> None:
        self._grid_controller = ctl

    def set_design(self, design: GridDesign | None, path: str = "") -> None:
        """Make ``design`` the one being edited (a new, opened or loaded design; None:
        no design): clears the undo stack, selection and result, and checks it at once."""
        self._timer.stop()
        self._undo.clear()
        self._design = design
        self._graph = None
        self._path = path
        self._selection = []
        self._set_result(None)
        self._undo.setClean()
        self._refresh()
        self.designChanged.emit()
        self.selectionChanged.emit()
        self._was_modified = self._get_modified()
        self.modifiedChanged.emit()
        self._on_undo_state()
        self._validate_now()

    # ================================================================ properties

    def _get_has_design(self) -> bool:
        return self._design is not None

    def _get_name(self) -> str:
        return self._design.grid.name if self._design is not None else ""

    def _get_base_frequency(self) -> float:
        return float(self._design.grid.base_frequency) if self._design is not None else 0.0

    def _get_path(self) -> str:
        return self._path

    @Slot()
    def _on_undo_state(self) -> None:
        state = (self._undo.canUndo(), self._undo.canRedo(), self._undo.undoText(),
                 self._undo.redoText())  # fmt: skip
        if state != self._undo_state:
            self._undo_state = state
            self.undoStateChanged.emit()
        modified = self._get_modified()
        if modified != self._was_modified:
            self._was_modified = modified
            self.modifiedChanged.emit()

    def _get_modified(self) -> bool:
        return self._design is not None and not self._undo.isClean()

    def _get_can_undo(self) -> bool:
        return self._undo.canUndo()

    def _get_can_redo(self) -> bool:
        return self._undo.canRedo()

    def _get_undo_text(self) -> str:
        return self._undo.undoText()

    def _get_redo_text(self) -> str:
        return self._undo.redoText()

    def _get_buses(self) -> QObject:
        return self._buses

    def _get_elements(self) -> QObject:
        return self._elements

    def _get_edges(self) -> QObject:
        return self._edges

    def _get_selection(self) -> list[str]:
        return list(self._selection)

    def _get_sheet(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._sheet]

    def _get_library(self) -> list[dict[str, Any]]:
        out = []
        for key, label, lo, hi, drawn in LIBRARY:
            required = _REGULATOR_REQUIRED if key == "regulator" else REQUIRED_VALUES.get(key, ())
            out.append(
                {
                    "kind": key,
                    "label": label,
                    "minTerminals": lo,
                    "maxTerminals": hi,
                    "drawn": drawn,
                    "required": [field_label(f) for f in required],
                    "requiredFields": list(required),
                }
            )
        return out

    def _get_paths(self) -> dict[str, str]:
        return dict(self._paths)

    def _get_element_count(self) -> int:
        if self._design is None:
            return 0
        counts = self._design.grid.counts()
        return sum(counts.values()) - counts["buses"]

    def _get_bus_count(self) -> int:
        return len(self._design.grid.buses) if self._design is not None else 0

    def _get_line_codes(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._codes]

    def _line_code_rows(self) -> list[dict[str, Any]]:
        if self._design is None:
            return []
        return [
            {"id": lc.id, "name": lc.name, "phases": lc.phases, "severity": self._severity(lc.id),
             "selected": lc.id in self._selection}
            for lc in self._design.grid.linecodes.values()
        ]  # fmt: skip

    def _get_diagnostic_rows(self) -> list[dict[str, str]]:
        return [
            {"location": d.block, "severity": d.severity, "message": d.message, "code": d.code}
            for d in self._diagnostics
        ]

    def _count(self, severity: str) -> int:
        return sum(1 for d in self._diagnostics if d.severity == severity)

    def _get_errors(self) -> int:
        return self._count(ERROR)

    def _get_warnings(self) -> int:
        return self._count(WARNING)

    def _get_available(self) -> bool:
        return engine_available()

    def _get_busy(self) -> bool:
        return self._opener is not None or self._running

    def _get_run_reason(self) -> str:
        return self._run_refusal(check=False)

    def _get_can_run(self) -> bool:
        return self._run_refusal(check=False) == ""

    def _get_status(self) -> str:
        return self._status

    def _get_console(self) -> str:
        lines = list(self._log_lines)
        if self._design is not None and self._checked:
            if lines:
                lines.append("")
            e, w = self._get_errors(), self._get_warnings()
            lines.append(
                f"Check of {self._get_name()}: {e} error{'s' if e != 1 else ''}, "
                f"{w} warning{'s' if w != 1 else ''}"
            )
            lines.extend(f"  {d.severity}: {d.block}: {d.message}" for d in self._diagnostics)
        return "\n".join(lines)

    def _get_has_result(self) -> bool:
        return self._result is not None and self._result.converged

    def _get_summary(self) -> dict[str, Any]:
        r = self._result
        if r is None or not r.converged:
            return {}
        out: dict[str, Any] = {
            "iterations": r.iterations,
            "controlIterations": r.control_iterations,
            "totalKw": r.total_power_kw,
            "totalKvar": r.total_power_kvar,
            "lossesKw": r.total_losses_kw,
            "lossesKvar": r.total_losses_kvar,
        }
        if self._last_outcome is not None:
            out["scriptPath"] = self._last_outcome.script_path
        return out

    hasDesign = Property(bool, _get_has_design, notify=designChanged)  # noqa: N815
    designName = Property(str, _get_name, notify=designChanged)  # noqa: N815
    baseFrequency = Property(float, _get_base_frequency, notify=designChanged)  # noqa: N815
    filePath = Property(str, _get_path, notify=designChanged)  # noqa: N815
    modified = Property(bool, _get_modified, notify=modifiedChanged)
    canUndo = Property(bool, _get_can_undo, notify=undoStateChanged)  # noqa: N815
    canRedo = Property(bool, _get_can_redo, notify=undoStateChanged)  # noqa: N815
    undoText = Property(str, _get_undo_text, notify=undoStateChanged)  # noqa: N815
    redoText = Property(str, _get_redo_text, notify=undoStateChanged)  # noqa: N815
    busGlyphs = Property(QObject, _get_buses, constant=True)  # noqa: N815
    elementGlyphs = Property(QObject, _get_elements, constant=True)  # noqa: N815
    edges = Property(QObject, _get_edges, constant=True)
    selection = Property("QVariantList", _get_selection, notify=selectionChanged)
    propertySheet = Property("QVariantList", _get_sheet, notify=propertySheetChanged)  # noqa: N815
    libraryItems = Property("QVariantList", _get_library, constant=True)  # noqa: N815
    edgePaths = Property("QVariantMap", _get_paths, notify=diagramChanged)  # noqa: N815
    elementCount = Property(int, _get_element_count, notify=diagramChanged)  # noqa: N815
    busCount = Property(int, _get_bus_count, notify=diagramChanged)  # noqa: N815
    lineCodes = Property("QVariantList", _get_line_codes, notify=lineCodesChanged)  # noqa: N815
    diagnosticRows = Property("QVariantList", _get_diagnostic_rows, notify=diagnosticsChanged)  # noqa: N815
    errorCount = Property(int, _get_errors, notify=diagnosticsChanged)  # noqa: N815
    warningCount = Property(int, _get_warnings, notify=diagnosticsChanged)  # noqa: N815
    available = Property(bool, _get_available, constant=True)
    busy = Property(bool, _get_busy, notify=busyChanged)
    canRun = Property(bool, _get_can_run, notify=busyChanged)  # noqa: N815
    runDisabledReason = Property(str, _get_run_reason, notify=busyChanged)  # noqa: N815
    statusText = Property(str, _get_status, notify=statusTextChanged)  # noqa: N815
    consoleText = Property(str, _get_console, notify=consoleTextChanged)  # noqa: N815
    hasResult = Property(bool, _get_has_result, notify=resultChanged)  # noqa: N815
    summary = Property("QVariantMap", _get_summary, notify=resultChanged)

    def _get_stale(self) -> bool:
        return self._stale

    def _get_has_report(self) -> bool:
        return self._report is not None

    def _get_overlay(self) -> dict[str, dict[str, Any]]:
        return overlay_map(self._overlay)

    def _get_overlay_paths(self) -> list[dict[str, Any]]:
        if not self._overlay.solved:
            return []
        segs = [(e["elementId"], e["x1"], e["y1"], e["x2"], e["y2"]) for e in self._edge_rows]
        return overlay_paths(segs, self._overlay)

    def _get_overlay_buses(self) -> list[dict[str, Any]]:
        if not self._overlay.solved or self._design is None:
            return []
        out = []
        for name, g in self._design.diagram.bus_glyphs.items():
            item = self._overlay.items.get(f"bus.{name}")
            if item is None:
                continue
            half = float(g.length) / 2.0
            dx, dy = (0.0, half) if g.orientation == 90 else (half, 0.0)
            x, y = float(g.x), float(g.y)
            out.append({"id": f"bus.{name}", "status": item.status, "x1": x - dx,
                        "y1": y - dy, "x2": x + dx, "y2": y + dy})  # fmt: skip
        return out

    def _get_overlay_limits(self) -> dict[str, float]:
        return overlay_limits(self._run_config)

    def _get_feasibility(self) -> dict[str, Any]:
        return _feasibility_map(self._report)

    def _get_snapshot_note(self) -> str:
        return SNAPSHOT_LIMITATION

    resultsStale = Property(bool, _get_stale, notify=staleChanged)  # noqa: N815
    hasReport = Property(bool, _get_has_report, notify=resultChanged)  # noqa: N815
    overlay = Property("QVariantMap", _get_overlay, notify=resultChanged)
    overlayPaths = Property("QVariantList", _get_overlay_paths, notify=overlayPathsChanged)  # noqa: N815
    overlayBuses = Property("QVariantList", _get_overlay_buses, notify=overlayPathsChanged)  # noqa: N815
    overlayLimits = Property("QVariantMap", _get_overlay_limits, notify=resultChanged)  # noqa: N815
    feasibility = Property("QVariantMap", _get_feasibility, notify=resultChanged)
    snapshotNote = Property(str, _get_snapshot_note, constant=True)  # noqa: N815

    # ================================================================ undo

    @Slot()
    def undo(self) -> None:
        self._undo.undo()

    @Slot()
    def redo(self) -> None:
        self._undo.redo()

    def _push(self, cmd: Command, electrical: bool = True) -> bool:
        """Apply ``cmd`` and push it as one undo step; False (and why, in the Console
        and ``editRefused``) when it was not applied."""
        if self._design is None:
            self._refuse("No design: create or open one first")
            return False
        diags = cmd.apply(self._design)
        if _has_error(diags):
            first = next(d for d in diags if d.severity == ERROR)
            self._refuse(f"Not done: {first.block}: {first.message}", diags)
            return False
        self._undo.push(_UndoCommand(self, cmd, electrical))
        self._changed(electrical, cmd)
        return True

    def _replay(
        self,
        step: Callable[[GridDesign], list[NetworkDiagnostic]],
        electrical: bool,
        cmd: Command | None = None,
    ) -> None:
        if self._design is None:
            return
        diags = step(self._design)
        if _has_error(diags):  # cannot happen with stack discipline; never hide it
            self._log([f"  {d.severity}: {d.block}: {d.message}" for d in diags])
        self._changed(electrical, cmd)

    def _changed(self, electrical: bool, cmd: Command | None = None) -> None:
        if self._design is None:
            return
        self._mark_stale()  # any change after a run, even an undo back to it
        known = [i for i in self._selection if self._exists(i)]
        if known != self._selection:
            self._selection = known
            self.selectionChanged.emit()
        if electrical:
            self._graph = None
        # a move or rotation (presentation only) redraws just what it touched, so a
        # drag on IEEE 123 stays far inside a frame (spike S1)
        if electrical or cmd is None or not self._refresh_glyphs(cmd.affected()):
            self._refresh()
        if electrical:
            self._timer.start()
            self.busyChanged.emit()  # canRun may change after the check

    def _exists(self, element_id: str) -> bool:
        return self._design is not None and resolve(self._design.grid, element_id) is not None

    # ================================================================ files

    @Slot(str, float, result=bool)
    @Slot(str, float, str, float, float, float, float, float, result=bool)
    def newDesign(  # noqa: N802 - QML API
        self,
        name: str,
        baseFrequency: float,  # noqa: N803
        sourceBus: str = "",  # noqa: N803
        sourceKv: float = math.nan,  # noqa: N803
        r1: float = math.nan,
        x1: float = math.nan,
        r0: float = math.nan,
        x0: float = math.nan,
    ) -> bool:
        """Start a new design (New Design dialog). The base frequency is required (no
        default, never 60 Hz by assumption). With ``sourceBus`` the design starts with
        that bus and a 3-phase source on it (kV LL and R1/X1/R0/X0 in ohm, all typed by
        the user); without, it starts empty. Voltage bases are derived (topology.py)."""
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name.strip().lower()):
            self._refuse(f"Not created: name {name!r} is not lower-case letters, digits and _")
            return False
        if not (_finite(baseFrequency) and baseFrequency > 0):
            self._refuse("Not created: the base frequency must be a number > 0 Hz")
            return False
        grid = GridModel(name.strip().lower(), float(baseFrequency), derive_voltage_bases=True)
        design = GridDesign(grid, DiagramModel())
        if sourceBus:
            values = {"kv": sourceKv, "r1_ohm": r1, "x1_ohm": x1, "r0_ohm": r0, "x0_ohm": x0}
            bad = [field_label(k) for k, v in values.items() if not _finite(v)]
            if bad:
                self._refuse(f"Not created: the source needs {', '.join(bad)}")
                return False
            diags = AddBus(sourceBus, (0.0, 0.0)).apply(design)
            if not _has_error(diags):
                diags = AddElement("vsource", [sourceBus], name="source", values=values).apply(
                    design
                )
            if _has_error(diags):
                first = next(d for d in diags if d.severity == ERROR)
                self._refuse(f"Not created: {first.block}: {first.message}", diags)
                return False
        self._log_lines = []
        self.set_design(design)
        self._log([f"New design {grid.name} ({grid.base_frequency:g} Hz)"])
        self._set_status(f"New design {grid.name}")
        return True

    @Slot(str, result=bool)
    def openDssAsDesign(self, url_or_path: str) -> bool:  # noqa: N802 - QML API
        """Read a .dss file into a new design in a GridRunner child, as the G6 view
        does (OpenDSS compiles it; rule 16). The file is never written. Returns False
        when the read could not start; ``designOpened`` / ``openFailed`` follow."""
        if not engine_available():
            self._refuse("Not opened: reading a .dss file needs dss-python")
            return False
        if self._get_busy():
            self._refuse("Not opened: the designer is busy")
            return False
        path = _path_of(url_or_path).expanduser()
        if not path.is_file():
            self._refuse(f"Not opened: file not found: {path}")
            return False
        path = path.resolve()
        timeout = {} if self.timeout is None else {"timeout": self.timeout}
        runner = GridRunner(path, self.config, solve=False, **timeout)
        self._opener = runner
        self._log([f"Opening {path} as a design"])
        self._set_status("Opening…")
        self._open_thread = threading.Thread(target=self._wait_open, args=(runner,), daemon=True)
        self._open_thread.start()
        self.busyChanged.emit()
        return True

    def _wait_open(self, runner: GridRunner) -> None:
        self._opened.emit((runner, runner.run()))

    @Slot(object)
    def _on_opened(self, payload: tuple[GridRunner, GridRunOutcome]) -> None:
        runner, outcome = payload
        if runner is not self._opener:
            return
        self._opener = None
        self.busyChanged.emit()
        if outcome.status != OK:
            why = "stopped" if outcome.status == CANCELLED else outcome.message
            self._refuse(f"Not opened: {why}")
            self.openFailed.emit()
            return
        self._log_diagnostics(outcome.diagnostics)
        errors = [d for d in outcome.diagnostics if d.severity == ERROR]
        if errors or outcome.model is None:
            first = errors[0].message if errors else "the circuit could not be read"
            self._refuse(f"Not opened: {first}")
            self.openFailed.emit()
            return
        self.open_model_as_design(outcome.model)
        self.designOpened.emit()

    def open_model_as_design(self, model: GridModel) -> None:
        """A copy of ``model`` (read from a .dss file) as a new, unsaved design. It
        keeps the file's voltage bases; the diagram comes from its bus coordinates or
        the automatic layout (layout.build_diagram)."""
        grid = copy.deepcopy(model)
        design = GridDesign(grid, build_diagram(grid))
        self.set_design(design)
        counts = grid.counts()
        self._set_status(
            f"Opened {grid.name} as a design: {counts['buses']} buses, "
            f"{counts['lines'] + counts['switches']} lines, "
            f"{counts['transformers']} transformers"
        )
        self._log([self._status])

    @Slot(str, result=bool)
    def load(self, url_or_path: str) -> bool:
        """Open a .qgrid design. Nothing changes when the file has an error."""
        path = _path_of(url_or_path).expanduser()
        design, diags = design_io.load(path)
        if design is None or _has_error(diags):
            first = next((d for d in diags if d.severity == ERROR), None)
            why = f"{first.block}: {first.message}" if first else "the file could not be read"
            self._refuse(f"Not opened: {why}", diags)
            self.openFailed.emit()
            return False
        self._log_lines = []
        self.set_design(design, str(path.resolve()))
        self._log_diagnostics(diags)
        self._set_status(f"Opened {path.name}")
        self._log([self._status])
        self.designOpened.emit()
        return True

    @Slot(result=bool)
    @Slot(str, result=bool)
    def save(self, url_or_path: str = "") -> bool:
        """Save as .qgrid (``url_or_path`` "" = the design's own file). Results are
        never stored in it."""
        if self._design is None:
            self._refuse("Not saved: no design")
            return False
        if not url_or_path and not self._path:
            self._refuse("Not saved: choose a file name (Save As)")
            return False
        path = _path_of(url_or_path).expanduser() if url_or_path else Path(self._path)
        if path.suffix.lower() != design_io.SUFFIX:
            path = path.with_name(path.name + design_io.SUFFIX)
        diags = design_io.save(path, self._design)
        if _has_error(diags):
            self._refuse(f"Not saved: {diags[0].message}", diags)
            return False
        self._path = str(path.resolve())
        self._undo.setClean()
        self.designChanged.emit()
        self._set_status(f"Saved {path.name}")
        self._log([self._status])
        return True

    @Slot(str, result=bool)
    def exportDss(self, url_or_path: str) -> bool:  # noqa: N802 - QML API
        """Write the design as a self-contained OpenDSS script (writer.py), with the
        bus glyph positions as bus coordinates (y up). A draft design is refused with
        its missing values. The design itself does not change."""
        if self._design is None:
            self._refuse("Not exported: no design")
            return False
        grid = copy.deepcopy(self._design.grid)
        for name, bus in grid.buses.items():
            g = self._design.diagram.bus_glyphs.get(name)
            if g is not None:
                bus.x, bus.y = float(g.x), -float(g.y) + 0.0
        text, diags = write_dss(grid)
        if text is None:
            self._refuse(f"Not exported: {len(diags)} problem(s), see the Console", diags)
            return False
        path = _path_of(url_or_path).expanduser()
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
        except OSError as err:
            self._refuse(f"Not exported: cannot write {path.name}: {err.strerror or err}")
            return False
        self._log_diagnostics(diags)
        self._set_status(f"Exported {path.name}")
        self._log([self._status])
        return True

    @Slot()
    def closeDesign(self) -> None:  # noqa: N802 - QML API
        """No design any more (a project without designs was opened or created): the
        drawing, undo stack, selection, diagnostics and result are cleared. The
        settings are kept; the project sets them."""
        self.set_design(None)

    @Slot(str)
    def setResultsFolder(self, url_or_path: str) -> None:  # noqa: N802 - QML API
        """The project's ``name_results`` folder; run scripts go to
        ``<folder>/grid/<design>/``. "" = next to the .qgrid file."""
        self._results_folder = str(_path_of(url_or_path)) if url_or_path else ""

    def script_path(self) -> Path | None:
        """Where the next run's DSS script is saved; None for an unsaved design
        without a results folder."""
        if self._design is None:
            return None
        if self._results_folder:
            root = Path(self._results_folder)
        elif self._path:
            p = Path(self._path)
            root = p.parent / f"{p.stem}_results"
        else:
            return None
        name = self._design.grid.name
        return root / "grid" / name / f"{name}.dss"

    # ================================================================ editing slots

    @Slot(str, "QVariantList", float, float, result=str)
    @Slot(str, "QVariantList", float, float, "QVariantMap", result=str)
    @Slot(str, "QVariantList", float, float, "QVariantMap", str, result=str)
    def addElement(  # noqa: N802 - QML API
        self,
        kind: str,
        terminals: list[Any],
        x: float = math.nan,
        y: float = math.nan,
        values: dict[str, Any] | None = None,
        copyFrom: str = "",  # noqa: N803
    ) -> str:
        """Add a library item: "bus", a KINDS key or "regulator". ``terminals`` are
        "<bus>[.<node>...]" texts; x, y the glyph position (NaN = the layout default);
        ``values`` fields typed by the user ("name" names it, "phases" sets the phases;
        a regulator's RegControl and transformer fields go to the right half);
        ``copyFrom`` an element of the same kind whose values are copied. Returns the
        new id, or "" when it was not added (why: Console)."""
        values = dict(values or {})
        terms = [str(t) for t in (terminals or [])]
        pos = (float(x), float(y)) if _finite(x) and _finite(y) else None
        name = values.pop("name", None)
        name = str(name) if name not in (None, "") else None
        if kind == "bus":
            cmd: Command = AddBus(name, pos)
            if not self._push(cmd):
                return ""
            return f"bus.{cmd.name}"  # type: ignore[attr-defined]
        if kind == "regulator":
            if len(terms) != 2:
                self._refuse("Not done: a regulator needs two terminals")
                return ""
            reg_fields = {f.name for f in dataclasses.fields(Regulator)}
            reg_values = {
                k: coerce_input(_class_field_type(Regulator, k), v)
                for k, v in values.items()
                if k in reg_fields
            }
            tx_name = values.pop("transformer_name", None)
            tx_values = {
                k: coerce_input(_class_field_type(Transformer, k), v)
                for k, v in values.items()
                if k not in reg_fields and k != "transformer_name"
            }
            reg = AddRegulator(
                terms[0],
                terms[1],
                pos,
                transformer_name=str(tx_name) if tx_name else None,
                regulator_name=name,
                transformer_values=tx_values,
                regulator_values=reg_values,
            )
            if not self._push(reg):
                return ""
            return f"regcontrol.{reg.children[1].name}"  # type: ignore[index,union-attr]
        k = KINDS.get(kind)
        if k is None or kind == "regcontrol":
            self._refuse(f"Not done: {kind!r} is not in the library")
            return ""
        phases = values.pop("phases", None)
        phases = coerce_input(int, phases) if phases not in (None, "") else None
        typed = {
            path: coerce_input(_class_field_type(k.cls, path), v) for path, v in values.items()
        }
        add = AddElement(
            kind,
            terms,
            pos,
            name=name,
            phases=phases,
            values=typed,
            copy_from=copyFrom or None,
        )
        if not self._push(add):
            return ""
        return add.element_id

    @Slot(str, int, str, result=bool)
    @Slot(str, int, str, "QVariantList", result=bool)
    def connectTerminal(  # noqa: N802 - QML API
        self,
        elementId: str,
        terminal: int,
        bus: str,
        nodes: list[Any] | None = None,  # noqa: N803
    ) -> bool:
        """Connect terminal ``terminal`` (1-based) of an element to ``bus`` with
        ``nodes`` ([] = 1..phases); ``bus`` "" disconnects it."""
        if not bus:
            return self._push(Disconnect(elementId, int(terminal)))
        node_list = [coerce_input(int, n) for n in (nodes or [])]
        return self._push(Connect(elementId, int(terminal), bus.strip().lower(), node_list))

    @Slot(str, str, "QVariant", result=bool)
    @Slot(str, str, "QVariant", str, result=bool)
    def setProperty(  # noqa: N802 - QML API
        self,
        elementId: str,
        path: str,
        value: Any,
        mergeKey: str = "",  # noqa: N803
    ) -> bool:
        """Set one field (``path``: "kw", "windings.1.kva"). Text from a field is read
        as the field's type ("" = not set). Sets with the same ``mergeKey`` (one
        spin-box session) merge into one undo step."""
        if self._design is None:
            self._refuse("No design: create or open one first")
            return False
        ref = resolve(self._design.grid, elementId)
        typed = coerce_input(_field_type(ref.obj, path), value) if ref is not None else value
        return self._push(SetProperty(elementId, path, typed, mergeKey))

    @Slot(result=int)
    def newGesture(self) -> int:  # noqa: N802 - QML API
        """A new gesture id: moves with the same id (one drag) are one undo step."""
        self._gesture += 1
        return self._gesture

    @Slot(str, float, float, result=bool)
    @Slot(str, float, float, int, result=bool)
    def moveGlyph(  # noqa: N802 - QML API
        self,
        glyphId: str,
        x: float,
        y: float,
        gesture: int = -1,  # noqa: N803
    ) -> bool:
        """Move a glyph ("bus.<name>" or an element id). Presentation only. Without a
        gesture (-1) every move is its own undo step."""
        if gesture < 0:
            gesture = self.newGesture()
        return self._push(MoveGlyph(glyphId, x, y, gesture), electrical=False)

    @Slot(str, result=bool)
    @Slot(str, int, result=bool)
    def rotateGlyph(self, glyphId: str, quarterTurns: int = 1) -> bool:  # noqa: N802, N803
        return self._push(RotateGlyph(glyphId, int(quarterTurns)), electrical=False)

    @Slot(str, str, result=bool)
    def rename(self, elementId: str, newName: str) -> bool:  # noqa: N803
        """Rename a bus or element and every reference to it; the selection follows."""
        cmd = Rename(elementId, newName)
        was = elementId in self._selection
        if not self._push(cmd):
            return False
        if was and self._design is not None:
            cls = elementId.partition(".")[0]
            new_id = f"{cls}.{str(newName).strip().lower()}"
            if self._exists(new_id) and new_id not in self._selection:
                self._selection.append(new_id)
                self.selectionChanged.emit()
                self._refresh()
        return True

    @Slot(str, bool, result=bool)
    def setSwitchOpen(self, elementId: str, open: bool) -> bool:  # noqa: N802, N803
        return self._push(SetSwitchOpen(elementId, bool(open)))

    @Slot(result=bool)
    def deleteSelection(self) -> bool:  # noqa: N802 - QML API
        """Remove every selected bus and element as one undo step. A bus takes what is
        connected to it along (RemoveBus); a transformer its regulators."""
        if self._design is None or not self._selection:
            return False
        grid = self._design.grid
        buses = [i for i in self._selection if i.startswith("bus.") and self._exists(i)]
        gone: set[str] = set()
        for b in buses:
            gone.update(connected_elements(grid, b[4:]))
        removed_tx = {
            i[len("transformer.") :]
            for i in [*self._selection, *gone]
            if i.startswith("transformer.")
        }
        # buses first: a linecode is free only once the lines on a removed bus are gone
        children: list[Command] = [RemoveBus(b) for b in buses]
        for i in self._selection:
            if i.startswith("bus.") or i in gone or not self._exists(i):
                continue
            if i.startswith("regcontrol."):
                reg = grid.regulators.get(i.partition(".")[2])
                if reg is not None and reg.transformer in removed_tx:
                    continue
            children.append(RemoveElement(i))
        if not children:
            return False
        n = len(self._selection)
        text = children[0].description if len(children) == 1 else f"Delete {n} items"
        ok = self._push(children[0] if len(children) == 1 else Compound(text, children))
        if ok and self._selection:
            self._selection = []
            self.selectionChanged.emit()
            self._refresh()
        return ok

    # ---- canvas gestures (D6): one slot per action

    @Slot(float, float, float, bool, result=str)
    def clickAt(self, x: float, y: float, tolerance: float, additive: bool) -> str:  # noqa: N802
        """Select what is nearest to (x, y) within ``tolerance`` (diagram units: 6 px /
        zoom on the canvas; S1). A click on something already selected keeps the
        selection (to drag it); nothing there clears it, unless ``additive`` (Ctrl),
        which toggles what was hit. Returns the id hit ("" = none)."""
        hit = self.glyphAt(x, y, tolerance)
        if hit:
            if additive or hit not in self._selection:
                self.select(hit, additive)
        elif not additive:
            self.clearSelection()
        return hit

    @Slot(float, float, float, result=str)
    def glyphAt(self, x: float, y: float, tolerance: float) -> str:  # noqa: N802
        """The id nearest to (x, y) within ``tolerance`` ("" = none); changes nothing."""
        if self._design is None:
            return ""
        return self._hits().hit(float(x), float(y), float(tolerance)) or ""

    @Slot(float, float, float, result=str)
    def busAt(self, x: float, y: float, tolerance: float) -> str:  # noqa: N802
        """The name of the bus whose bar is nearest to (x, y) within ``tolerance``
        ("" = none), ignoring the elements drawn there (a drop, the branch tool)."""
        if self._design is None:
            return ""
        buses = HitIndex(sh for sh in self._hits().shapes if sh.id.startswith("bus."))
        hit = buses.hit(float(x), float(y), float(tolerance))
        return hit[4:] if hit else ""

    @Slot(float, float, float, float, bool)
    def selectRect(  # noqa: N802 - QML API
        self, x0: float, y0: float, x1: float, y1: float, additive: bool
    ) -> None:
        """Rubber band: select every bus and element wholly inside the rectangle
        (``additive``: added to the selection)."""
        if self._design is None:
            return
        ids = self._hits().in_rect(float(x0), float(y0), float(x1), float(y1))
        sel = list(self._selection) if additive else []
        sel.extend(i for i in ids if i not in sel)
        self._set_selection(sel)

    @Slot(float, float, int, result=bool)
    def moveSelection(self, dx: float, dy: float, gesture: int) -> bool:  # noqa: N802
        """Move every selected glyph by (dx, dy) from where it is now. Calls with the
        same ``gesture`` (one drag, ``newGesture``) are one undo step."""
        ids = [i for i in self._selection if self._glyph_exists(i)]
        if not ids:
            return False
        return self._push(MoveGlyphs(ids, float(dx), float(dy), int(gesture)), electrical=False)

    @Slot(result=bool)
    def rotateSelection(self) -> bool:  # noqa: N802 - QML API
        """Rotate every selected glyph a quarter turn (key R), as one undo step."""
        ids = [i for i in self._selection if self._glyph_exists(i)]
        if not ids:
            return False
        if len(ids) == 1:
            return self._push(RotateGlyph(ids[0]), electrical=False)
        cmds: list[Command] = [RotateGlyph(i) for i in ids]
        return self._push(Compound(f"Rotate {len(ids)} items", cmds), electrical=False)

    def _glyph_exists(self, glyph_id: str) -> bool:
        d = self._design
        if d is None:
            return False
        if glyph_id.startswith("bus."):
            return glyph_id[4:] in d.diagram.bus_glyphs
        return glyph_id in d.diagram.element_glyphs

    @Slot(str, result=bool)
    def toggleSwitch(self, elementId: str) -> bool:  # noqa: N802, N803 - QML API
        """Open a closed switch or close an open one (double-click). Anything else
        (a double-click on a line) does nothing and returns False."""
        if self._design is None or not elementId.startswith("line."):
            return False
        sw = self._design.grid.switches.get(elementId[len("line.") :])
        if sw is None:
            return False
        return self._push(SetSwitchOpen(elementId, sw.closed))

    @Slot(str, str, str, result=str)
    @Slot(str, str, str, "QVariantMap", result=str)
    def drawBranch(  # noqa: N802 - QML API
        self,
        kind: str,
        busA: str,  # noqa: N803
        busB: str,  # noqa: N803
        values: dict[str, Any] | None = None,
    ) -> str:
        """The branch tool: a line, switch, transformer or regulator from bus A to bus
        B (its glyph between them), with the ``values`` the user typed in the
        required-values popup (``requiredValues``), selected. Returns its id ("" = not
        added)."""
        if kind not in _BRANCH_KINDS:
            self._refuse(f"Not done: {kind!r} is not a branch")
            return ""
        new = self.addElement(kind, [busA, busB], math.nan, math.nan, values or {})
        if new:
            self._set_selection([new])
        return new

    @Slot(str, float, float, float, result=str)
    @Slot(str, float, float, float, "QVariantMap", result=str)
    def dropItem(  # noqa: N802 - QML API
        self,
        kind: str,
        x: float,
        y: float,
        tolerance: float,
        values: dict[str, Any] | None = None,
    ) -> str:
        """A library item dropped at (x, y): a bus there, or a source, load, capacitor
        or PV system on the bus under the drop (within ``tolerance``) with its glyph in
        the bus's row of shunts; a line code (no glyph) is added. The new item is selected.
        ``values`` are what the user typed in the required-values popup. Branches are
        drawn with the branch tool instead. Returns the new id ("" = not added; why:
        Console)."""
        if self._design is None:
            self._refuse("No design: create or open one first")
            return ""
        if kind in _BRANCH_KINDS:
            self._refuse(f"Not done: draw a {kind} from one bus to another")
            return ""
        if kind == "bus":
            new = self.addElement("bus", [], x, y)
        elif kind == "linecode":
            new = self.addElement("linecode", [], math.nan, math.nan)
        elif kind in _SHUNT_KINDS:
            bus = self.busAt(x, y, tolerance)
            if not bus:
                label = dict((k, lab) for k, lab, *_ in LIBRARY)[kind].lower()
                self._refuse(f"Not done: drop the {label} onto a bus")
                return ""
            # the symbol goes in the bus's row of shunts (layout), not onto the bar
            new = self.addElement(kind, [bus], math.nan, math.nan, values or {})
        else:
            self._refuse(f"Not done: {kind!r} is not in the library")
            return ""
        if new:
            self._set_selection([new])
        return new

    @Slot(str, str, result="QVariantList")
    def requiredValues(self, kind: str, bus: str) -> list[dict[str, str]]:  # noqa: N802
        """The fields a new ``kind`` on ``bus`` cannot be added without (rule 24: never
        a default): {field, label, unit} rows for the editor's popup. A source needs
        its kV and impedances, a switch its R, a regulator its control settings, and a
        load, capacitor or PV system its kV when the bus has no voltage zone yet."""
        if self._design is None:
            return []
        key = "regcontrol" if kind == "regulator" else kind
        fields = list(REQUIRED_VALUES.get(key, ()))
        if kind in ("load", "capacitor", "pvsystem") and bus:
            zones = self._graph[1] if self._graph is not None else {}  # set by _refresh
            if bus not in zones:
                fields.append("kv")
        return [{"field": f, "label": field_label(f), "unit": _UNITS.get(f, "")} for f in fields]

    @Slot(str, str, str, result=bool)
    def setTerminal(self, elementId: str, field: str, text: str) -> bool:  # noqa: N802, N803
        """Connect a terminal row of the property sheet ("bus1", "bus2", "terminal",
        "windings.<i>.terminal") to the bus and nodes typed ("671.1.2.3"; "671" =
        1..phases; "" disconnects)."""
        if self._design is None:
            self._refuse("No design: create or open one first")
            return False
        ref = resolve(self._design.grid, elementId)
        terminal = _terminal_number(ref.obj if ref is not None else None, field)
        if terminal is None:
            self._refuse(f"Not done: {elementId} has no terminal {field!r} to connect")
            return False
        t = Terminal.parse(text.strip().lower()) if text.strip() else None
        if t is None:
            return self.connectTerminal(elementId, terminal, "")
        return self.connectTerminal(elementId, terminal, t.bus, list(t.nodes))

    @Slot(result="QVariantList")
    def contentBounds(self) -> list[float]:  # noqa: N802 - QML API
        """[x0, y0, x1, y1] around everything drawn ([] = nothing), for Fit."""
        b = self._hits().bounds() if self._design is not None else None
        return list(b) if b is not None else []

    @Slot(result=bool)
    def editGridAsDesign(self) -> bool:  # noqa: N802 - QML API
        """Edit as Design in the read-only grid view (rule 28): a copy of the feeder
        that GridController has open becomes a new, unsaved design. The .dss file is
        never written and the grid view keeps its feeder."""
        ctl = self._grid_controller_object()
        model = ctl.model() if ctl is not None else None  # type: ignore[attr-defined]
        if model is None:
            self._refuse("Not opened: open a .dss feeder in the Grid view first")
            return False
        self._log_lines = []
        self.open_model_as_design(model)
        self.designOpened.emit()
        return True

    # ---- selection (view state, not undoable)

    @Slot(str)
    @Slot(str, bool)
    def select(self, elementId: str, additive: bool = False) -> None:  # noqa: N803
        """Select one bus or element ("" clears); ``additive`` toggles it in the
        selection."""
        if not elementId:
            self.clearSelection()
            return
        if not self._exists(elementId):
            return
        if additive:
            sel = list(self._selection)
            if elementId in sel:
                sel.remove(elementId)
            else:
                sel.append(elementId)
        else:
            sel = [elementId]
        self._set_selection(sel)

    @Slot("QVariantList")
    def setSelection(self, ids: list[Any]) -> None:  # noqa: N802 - QML API
        sel: list[str] = []
        for i in ids or []:
            if isinstance(i, str) and self._exists(i) and i not in sel:
                sel.append(i)
        self._set_selection(sel)

    @Slot()
    def clearSelection(self) -> None:  # noqa: N802 - QML API
        self._set_selection([])

    def _set_selection(self, sel: list[str]) -> None:
        if sel == self._selection:
            return
        self._selection = sel
        self.selectionChanged.emit()
        self._refresh()

    @Slot(str, result="QVariantList")
    def diagnosticsFor(self, elementId: str) -> list[dict[str, str]]:  # noqa: N802 - QML API
        """The last check's diagnostics at one bus or element."""
        return [
            {"severity": d.severity, "message": d.message, "code": d.code}
            for d in self._by_id.get(elementId, [])
        ]

    # ================================================================ run

    def _run_refusal(self, check: bool) -> str:
        """Why Run cannot start now ("" = it can). ``check`` validates the design
        first instead of trusting the last debounced check."""
        if not engine_available():
            return 'Run needs dss-python (pip install -e ".[grid]")'
        if self._design is None:
            return "No design"
        if self._get_busy():
            return "Busy"
        if self._grid_controller_object() is None:
            return "No grid controller to run the power flow"
        if check:
            self._timer.stop()
            self._validate_now()
        if _has_error(self._diagnostics):
            return f"The design has {self._get_errors()} error(s) (see the Console)"
        problems = self.config.problems()
        if problems:
            return f"Power-flow settings: {problems[0]}"
        return ""

    def _grid_controller_object(self) -> QObject | None:
        if self._grid_controller is not None:
            return self._grid_controller
        engine = qmlEngine(self)
        if engine is None:
            return None
        try:
            found = engine.singletonInstance("QtSpice", "GridController")
        except Exception:  # not registered in this engine
            return None
        if found is not None:
            self._grid_controller = found
        return found

    @Slot(QObject)
    def attachGridController(self, ctl: QObject) -> None:  # noqa: N802 - QML API
        self._grid_controller = ctl

    @Slot(result=bool)
    def runPowerFlow(self) -> bool:  # noqa: N802 - QML API
        """Check the design and solve it through GridController.runModel (rule 23).
        False (why: ``statusText``, Console) when it cannot start."""
        why = self._run_refusal(check=True)
        if why:
            self._refuse(f"Not started: {why}")
            self.busyChanged.emit()
            return False
        assert self._design is not None
        ctl = self._grid_controller_object()
        assert ctl is not None
        script = self.script_path()
        model = copy.deepcopy(self._design.grid)
        config = self.config
        if self._listening is not ctl:
            ctl.modelRunFinished.connect(self._on_run_done)  # type: ignore[attr-defined]
            self._listening = ctl
        why = ctl.runModel(model, config, script)  # type: ignore[attr-defined]
        if why:
            self._refuse(f"Not started: {why}")
            return False
        self._run_model, self._run_config = copy.deepcopy(model), config
        self._running = True
        self.busyChanged.emit()
        self._log(["", f"Power flow of design {model.name}"])
        self._set_status("Running…")
        return True

    @Slot(object)
    def _on_run_done(self, outcome: GridRunOutcome) -> None:
        if not self._running:
            return  # a model run that is not ours
        self._running = False
        self._last_outcome = outcome
        self.busyChanged.emit()
        if outcome.status != OK:
            why = "Stopped" if outcome.status == CANCELLED else f"Failed: {outcome.message}"
            self._set_status(why)
            if outcome.traceback:
                self._log(outcome.traceback.rstrip().split("\n"))
            self._log([f"Run: {why}"])
            self._set_result(None)
            return
        self._log_diagnostics(outcome.diagnostics)
        if outcome.script_path:
            self._log([f"DSS script: {outcome.script_path}"])
        result = outcome.result
        if result is None or not result.converged:
            errors = [d for d in outcome.diagnostics if d.severity == ERROR]
            if result is None:
                why = errors[0].message if errors else "the design could not be solved"
                self._set_status(f"Not solved: {why}")
            else:
                self._set_status(
                    f"Failed: power flow did not converge after {result.iterations} iterations"
                )
            self._log([self._status])
            report = self._make_report(result) if result is not None else None
            self._set_result(None, report)
            if report is not None:
                self._log(_report_lines(report))
            return
        report = self._make_report(result)
        self._set_result(result, report)
        self._show_profile(result)
        self._set_status(
            f"Finished: converged in {result.iterations} iterations "
            f"({result.control_iterations} control), losses {result.total_losses_kw:.1f} kW"
        )
        self._log(
            [
                f"Total power from the source: {result.total_power_kw:.1f} kW, "
                f"{result.total_power_kvar:.1f} kvar",
                f"Losses: {result.total_losses_kw:.2f} kW, {result.total_losses_kvar:.2f} kvar",
                *(_report_lines(report) if report is not None else []),
                self._status,
            ]
        )
        self.resultReady.emit()

    def _make_report(self, result: GridResult) -> FeasibilityReport | None:
        if self._run_model is None:
            return None
        return feasibility_report(self._run_model, result, self._run_config)

    def _show_profile(self, result: GridResult) -> None:
        """The voltage profile to the waveform window, through GridController (rule 7)."""
        ctl = self._grid_controller_object()
        show = getattr(ctl, "show_profile", None)
        if self._run_model is None or show is None:
            return
        self._log_diagnostics(show(self._run_model, result, self._run_config))

    @Slot()
    def stop(self) -> None:
        """Stop an open or a run in progress."""
        if self._opener is not None:
            self._opener.cancel()
        if self._running:
            ctl = self._grid_controller_object()
            if ctl is not None:
                ctl.stop()  # type: ignore[attr-defined]

    @Slot()
    def shutdown(self) -> None:
        if self._opener is not None:
            self._opener.cancel()
        thread = self._open_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)
        self._timer.stop()

    def _set_result(
        self, result: GridResult | None, report: FeasibilityReport | None = None
    ) -> None:
        """A new result (None: no converged one) and the report of the run; the
        results are fresh again."""
        if result is None and self._result is None and report is None and self._report is None:
            self._set_stale(False)
            return
        self._result = result
        self._report = report
        model = self._run_model
        self._overlay = (
            results_overlay(model, result, self._run_config)
            if model is not None and result is not None
            else ResultsOverlay()
        )
        self._set_stale(False)
        self.resultChanged.emit()

    def _mark_stale(self) -> None:
        if self._result is not None or self._report is not None:
            self._set_stale(True)

    def _set_stale(self, stale: bool) -> None:
        if stale != self._stale:
            self._stale = stale
            self.staleChanged.emit()

    @Slot(str, result="QVariantList")
    def focusElement(self, elementId: str) -> list[float]:  # noqa: N802, N803 - QML API
        """Select the bus or element ``elementId`` (a feasibility row) and return its
        glyph position [x, y] in diagram units to centre on; [] when it is not in the
        design (anymore) or has no glyph."""
        design = self._design
        if design is None or not elementId:
            return []
        self.select(elementId)
        if elementId.startswith("bus."):
            g = design.diagram.bus_glyphs.get(elementId[4:])
        else:
            g = design.diagram.element_glyphs.get(elementId)
        if g is None:
            return []
        return [float(g.x), float(g.y)]

    # ================================================================ validation

    def _validate_now(self) -> None:
        design = self._design
        if design is None:
            diags: list[NetworkDiagnostic] = []
        else:
            diags = validate(design.grid) + check_consistency(design.grid, design.diagram)
        self._diagnostics = diags
        by_id: dict[str, list[NetworkDiagnostic]] = {}
        for d in diags:
            by_id.setdefault(d.block, []).append(d)
        self._by_id = by_id
        self._checked = design is not None
        self._refresh()
        self.diagnosticsChanged.emit()
        self.consoleTextChanged.emit()
        self.busyChanged.emit()
        self.validated.emit()

    def _severity(self, element_id: str) -> str:
        diags = self._by_id.get(element_id)
        if not diags:
            return ""
        return max((d.severity for d in diags), key=lambda s: _SEVERITY_RANK.get(s, 0))

    # ================================================================ models

    def _refresh(self) -> None:
        design = self._design
        if design is None:
            self._row_of, self._edges_of, self._edge_rows = {}, {}, []
            self._buses.sync([])
            self._elements.sync([])
            self._edges.sync([])
            self._paths = _edge_paths([])
            self._index = None
            self._set_codes()
            self._set_sheet([])
            self.diagramChanged.emit()
            return
        grid, diagram = design.grid, design.diagram
        selected = set(self._selection)
        if self._graph is None:
            try:
                graph = GridGraph.build(grid)
                self._graph = (graph, graph.voltage_zones())
            except Exception:  # a malformed draft; draw it anyway
                self._graph = (None, {})
        graph, zones = self._graph
        buses = []
        for name, g in diagram.bus_glyphs.items():
            bid = f"bus.{name}"
            phases = graph.available_nodes(name) if graph is not None else ()
            buses.append(
                {
                    "glyphId": bid,
                    "name": name,
                    "x": float(g.x),
                    "y": float(g.y),
                    "orientation": int(g.orientation),
                    "length": float(g.length),
                    "zoneKv": float(zones.get(name, math.nan)),
                    "phases": ".".join(str(n) for n in phases),
                    "selected": bid in selected,
                    "severity": self._severity(bid),
                }
            )
        regulated = {r.transformer for r in grid.regulators.values()}
        elements, edges = [], []
        for attr, kind in _DRAWN_KIND.items():
            for el in getattr(grid, attr).values():
                g = diagram.element_glyphs.get(el.id)
                if g is None:
                    continue
                if kind == "line" and el.name in grid.switches:
                    continue  # a shared id; check_consistency reports it
                is_open = isinstance(el, Switch) and not el.closed
                sev = self._severity(el.id)
                sel = el.id in selected
                elements.append(
                    {
                        "glyphId": el.id,
                        "kind": kind,
                        "name": el.name,
                        "x": float(g.x),
                        "y": float(g.y),
                        "rotation": int(g.rotation),
                        "labelDx": float(g.label_dx),
                        "labelDy": float(g.label_dy),
                        "open": is_open,
                        "regulated": kind == "transformer" and el.name in regulated,
                        "selected": sel,
                        "severity": sev,
                    }
                )
                common = {"elementId": el.id, "kind": kind, "open": is_open, "selected": sel,
                          "severity": sev}  # fmt: skip
                for k, (a, b) in enumerate(element_segments(attr, el, g, diagram.bus_glyphs)):
                    edges.append(
                        {
                            "edgeId": f"{el.id}#{k}",
                            **common,
                            "x1": a[0],
                            "y1": a[1],
                            "x2": b[0],
                            "y2": b[1],
                        }  # fmt: skip
                    )
        self._buses.sync(buses)
        self._elements.sync(elements)
        self._edges.sync(edges)
        self._edge_rows = edges
        self._drawn, self._at_bus = {}, {}
        self._row_of = {r["glyphId"]: i for i, r in enumerate(buses)}
        self._row_of.update({r["glyphId"]: i for i, r in enumerate(elements)})
        self._edges_of = {}
        for i, e in enumerate(edges):
            self._edges_of.setdefault(e["elementId"], []).append(i)
        self._paths = _edge_paths(edges)
        self._index = None
        self._set_codes()
        self._set_sheet(self._build_sheet())
        self.diagramChanged.emit()

    def _refresh_glyphs(self, ids: set[str]) -> bool:
        """Update only the rows of the glyphs ``ids`` (moved or rotated) and the edges
        drawn from them. False when that is not possible (then refresh everything)."""
        design = self._design
        if design is None or not ids or not self._row_of:
            return False
        grid, diagram = design.grid, design.diagram
        if not self._drawn:  # cached until the next full refresh (an electrical edit)
            for attr in _DRAWN_KIND:
                for el in getattr(grid, attr).values():
                    self._drawn[el.id] = (attr, el)
        drawn = self._drawn
        redo: set[str] = set()
        for gid in ids:
            if gid not in self._row_of:
                return False
            if gid.startswith("bus."):
                g = diagram.bus_glyphs.get(gid[4:])
                if g is None:
                    return False
                at = {"x": float(g.x), "y": float(g.y), "orientation": int(g.orientation),
                      "length": float(g.length)}  # fmt: skip
                self._buses.update(self._row_of[gid], **at)
                bus = gid[4:]
                if bus not in self._at_bus:
                    self._at_bus[bus] = connected_elements(grid, bus)
                redo.update(self._at_bus[bus])
            else:
                g = diagram.element_glyphs.get(gid)
                if g is None:
                    return False
                at = {"x": float(g.x), "y": float(g.y), "rotation": int(g.rotation),
                      "labelDx": float(g.label_dx), "labelDy": float(g.label_dy)}  # fmt: skip
                self._elements.update(self._row_of[gid], **at)
                redo.add(gid)
        for eid in redo:
            if eid not in drawn:
                continue  # a line code or regulator has no edges
            attr, el = drawn[eid]
            g = diagram.element_glyphs.get(eid)
            rows = self._edges_of.get(eid, [])
            segs = element_segments(attr, el, g, diagram.bus_glyphs) if g is not None else []
            if len(segs) != len(rows):
                return False
            for i, (a, b) in zip(rows, segs, strict=True):
                new = {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}
                self._edge_rows[i] = {**self._edge_rows[i], **new}
                self._edges.update(i, **new)
        self._paths = _edge_paths(self._edge_rows)
        self._index = None
        self.diagramChanged.emit()
        return True

    def _set_codes(self) -> None:
        rows = self._line_code_rows()
        if rows != self._codes:
            self._codes = rows
            self.lineCodesChanged.emit()

    def _hits(self) -> HitIndex:
        if self._index is None:
            self._index = HitIndex(design_shapes(self._design) if self._design else [])
        return self._index

    # ---- property sheet

    def _set_sheet(self, rows: list[dict[str, Any]]) -> None:
        if rows != self._sheet:
            self._sheet = rows
            self.propertySheetChanged.emit()

    def _build_sheet(self) -> list[dict[str, Any]]:
        """Rows of the one selected bus or element: {field, label, unit, value,
        editable, type, choices, diagnostic}. Terminals are shown read-only (Connect);
        the name is read-only here (Rename)."""
        if self._design is None or len(self._selection) != 1:
            return []
        ref = resolve(self._design.grid, self._selection[0])
        if ref is None:
            return []
        el = ref.obj
        diags = self._by_id.get(ref.id, [])
        rows = [self._row(ref.id, "name", "name", el.name, str, False, diags)]
        for f in dataclasses.fields(el):
            if f.name in _HIDDEN:
                continue
            value = getattr(el, f.name)
            if f.name == "windings":
                for i, w in enumerate(value):
                    for wf in dataclasses.fields(w):
                        path = f"windings.{i}.{wf.name}"
                        rows.append(
                            self._row(ref.id, path, wf.name, getattr(w, wf.name),
                                      typing.get_type_hints(Winding)[wf.name], True, diags)
                        )  # fmt: skip
                continue
            tp = typing.get_type_hints(type(el))[f.name]
            rows.append(self._row(ref.id, f.name, f.name, value, tp, True, diags))
        return rows

    def _row(
        self,
        element_id: str,
        path: str,
        fname: str,
        value: Any,
        tp: Any,
        settable: bool,
        diags: list[NetworkDiagnostic],
    ) -> dict[str, Any]:
        is_terminal = isinstance(value, Terminal)
        editable = settable and not is_terminal and read_only_reason(fname) is None
        label = _TERMINAL_LABELS.get(fname, field_label(path)) if path != "name" else "name"
        if path.startswith("windings.") and fname == "terminal":
            label = f"winding {int(path.split('.')[1]) + 1} terminal"
        choices: list[str] = []
        if fname == "conn":
            choices = list(CONNECTIONS)
        elif fname == "model" and element_id.startswith("load."):
            choices = list(LOAD_MODELS)
        elif fname == "linecode" and self._design is not None:
            choices = list(self._design.grid.linecodes)
        text = [
            d.message
            for d in diags
            if d.message.startswith(label + " ") or _CODE_FIELDS.get(d.code) == path
        ]
        return {
            "field": path,
            "label": label,
            "unit": _UNITS.get(fname, ""),
            "value": _display(value),
            "editable": editable,
            "type": "terminal" if is_terminal else ("choice" if choices else _type_name(tp)),
            "choices": choices,
            "diagnostic": "; ".join(text),
        }

    # ================================================================ console and status

    def _refuse(self, status: str, diags: list[NetworkDiagnostic] | None = None) -> None:
        self._set_status(status)
        self._log([status])
        if diags:
            self._log_diagnostics(diags)
        self.editRefused.emit(status)

    def _set_status(self, text: str) -> None:
        if text != self._status:
            self._status = text
            self.statusTextChanged.emit()

    def _log_diagnostics(self, diagnostics: list[NetworkDiagnostic]) -> None:
        self._log([f"  {d.severity}: {d.block}: {d.message}" for d in diagnostics])

    def _log(self, lines: list[str]) -> None:
        if not lines:
            return
        self._log_lines.extend(lines)
        if len(self._log_lines) > MAX_CONSOLE_LINES:
            del self._log_lines[: len(self._log_lines) - MAX_CONSOLE_LINES]
        self.consoleTextChanged.emit()


def _terminal_number(el: Any, field: str) -> int | None:
    """1-based terminal (Connect) of a terminal row of the property sheet."""
    if isinstance(el, Transformer):
        parts = field.split(".")
        if len(parts) == 3 and parts[0] == "windings" and parts[2] == "terminal":
            k = int(parts[1]) if parts[1].isdigit() else -1
            return k + 1 if 0 <= k < len(el.windings) else None
        return None
    if isinstance(el, (Line, Switch)):
        return {"bus1": 1, "bus2": 2}.get(field)
    if isinstance(el, Source):
        return 1 if field == "bus1" else None  # bus2 (the neutral side) follows bus1
    if isinstance(el, (Load, Capacitor, PVSystem)):
        return 1 if field == "terminal" else None
    return None


PATH_STYLES = ("line", "switch", "open", "transformer", "stub", "selected")
"""One ShapePath per style on the canvas (S1 variant b); "selected" draws on top."""


def _check_row(c: FeasibilityCheck) -> dict[str, Any]:
    digits = 4 if c.unit == "pu" else 1
    value = _fmt(c.worst_value, digits)
    limit = f"{c.limit:g} {c.unit}"
    label = _CHECK_LABELS.get(c.name, c.name)
    if c.worst_value is None:
        text = f"{label}: nothing to check"
    else:
        text = f"{label}: {value} {c.unit} at {c.worst_element} (limit {limit}, {c.count} checked)"
    return {
        "name": c.name,
        "label": label,
        "status": c.status,
        "value": f"{value} {c.unit}" if c.worst_value is not None else "—",
        "element": c.worst_element,
        "limit": limit,
        "count": c.count,
        "text": text,
    }


def _feasibility_map(report: FeasibilityReport | None) -> dict[str, Any]:
    """The report for GridFeasibilityPanel; {} before a run."""
    if report is None:
        return {}
    summary: list[dict[str, str]] = []
    s = report.summary
    if s is not None:

        def pq(p: float, q: float, digits: int) -> str:
            return f"{_fmt(p, digits)} kW, {_fmt(q, digits)} kvar"

        at = f" at bus.{s.max_voltage_drop_bus}" if s.max_voltage_drop_bus else ""
        summary = [
            {"label": "Load", "value": pq(s.load_kw, s.load_kvar, 1)},
            {"label": "Source", "value": pq(s.source_kw, s.source_kvar, 1)},
            {"label": "Losses", "value": pq(s.losses_kw, s.losses_kvar, 2)},
            {"label": "Largest voltage drop", "value": f"{_fmt(s.max_voltage_drop_pu, 4)} pu{at}"},
        ]
    return {
        "status": report.status,
        "solved": report.solved,
        "limitation": report.limitation,
        "level1Status": report.level1_status,
        "level2Status": report.level2_status,
        "level1": [
            {
                "status": SEVERITY_STATUS.get(d.severity, "none"),
                "severity": d.severity,
                "element": d.block,
                "message": d.message,
                "code": d.code,
            }
            for d in report.level1
        ],
        "level2": [_check_row(c) for c in report.level2],
        "info": [
            {
                "status": SEVERITY_STATUS.get(d.severity, "none"),
                "element": d.block,
                "message": d.message,
            }  # fmt: skip
            for d in report.diagnostics
        ],
        "summary": summary,
    }


def _report_lines(report: FeasibilityReport) -> list[str]:
    """The feasibility report for the Console."""
    lines = [f"Feasibility (one snapshot): {report.status.upper()}"]
    e = sum(1 for d in report.level1 if d.severity == ERROR)
    w = sum(1 for d in report.level1 if d.severity == WARNING)
    lines.append(f"  Level 1 (topology): {report.level1_status}, {e} error(s), {w} warning(s)")
    if not report.solved:
        lines.append("  Level 2 (electrical): fail, not solved")
    for c in report.level2:
        lines.append(f"  Level 2: {c.status}: {_check_row(c)['text']}")
    lines.extend(f"  {d.severity}: {d.block}: {d.message}" for d in report.diagnostics)
    lines.append(f"  Note: {report.limitation}")
    return lines


def _edge_paths(edges: list[dict[str, Any]]) -> dict[str, str]:
    """SVG path strings (diagram units) of the edge rows, one per PATH_STYLES style."""
    parts: dict[str, list[str]] = {k: [] for k in PATH_STYLES}
    for e in edges:
        if e["selected"]:
            style = "selected"
        elif e["kind"] in ("line", "transformer"):
            style = e["kind"]
        elif e["kind"] == "switch":
            style = "open" if e["open"] else "switch"
        else:
            style = "stub"
        parts[style].append(f"M{e['x1']:.6g} {e['y1']:.6g}L{e['x2']:.6g} {e['y2']:.6g}")
    return {k: "".join(v) for k, v in parts.items()}


__all__ = ["GridDiagramController", "coerce_input"]
