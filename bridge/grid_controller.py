"""GridController: power-flow analysis of a .dss feeder in the UI (G6).

QML uses it as ``import QtSpice`` / ``GridController.openFile(url)``.

- ``openFile`` loads the file in a GridRunner child with ``solve=False``: OpenDSS
  compiles it, the engine checks it (core/grid/validate.py) and reads it back as a
  GridModel. Errors go to the Console and the file cannot be run (``canRun``); the
  single-line view (``geometry``) is drawn from the model's bus coordinates.
- ``run`` solves in a new GridRunner child (rule 5: OpenDSS never runs in the UI
  process); ``stop`` terminates it. Both children are waited for on a Python thread
  that reports back through a queued signal, as DiagramController does.
- Results: ``nodes``, ``buses`` (one row per bus), ``branches`` and ``violations``
  (table models: sort, filter, CSV), ``summary`` (totals, losses %, lowest and
  highest phase voltage), ``geometry`` (single-line view), ``busVoltages(bus)``.
  Violations are computed by core/grid/analysis.py from the PowerFlowConfig limits
  (rule 19), never here and never in QML.
- The voltage profile goes to the waveform window as a SimulationResult
  (``GridResult.to_simulation_result``, rule 7), with two flat limit traces
  (``show_profile``, also used for the designer's runs).
- Results overlay (D8): ``overlay`` (status, value and tooltip text per bus and
  element id, from analysis.results_overlay), ``overlayPaths`` (the branches of the
  single-line view in file coordinates, one SVG path per status and width step) and
  ``overlayBuses``; QML only maps a status to a colour.

The project (G7, ProjectManager.attachGrid) stores ``filePath`` and ``config``;
``set_config`` replaces the settings and limits (``configChanged``) and recomputes the
violations of a result already shown; ``clear`` forgets the open file.

``runModel(model)`` (v0.5, rule 23) solves a GridModel from the Grid Network Designer
(GridDiagramController) in a GridRunner child, which writes the DSS script with
writer.py; the outcome comes back as ``modelRunFinished`` and leaves the open file, its
result and the tables of this view untouched. One run (file or model) at a time.

``available`` is OpenDSSEngine.available(), read once: without dss-python the grid
menu entries and the Grid tab are hidden and every slot refuses politely.
"""

from __future__ import annotations

import csv
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import (
    Property,
    QAbstractTableModel,
    QCoreApplication,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtQml import QmlElement, QmlSingleton

from core.grid.analysis import (
    OFF,
    OVERLOAD,
    OVERVOLTAGE,
    PHASE_LETTERS,
    UNDERVOLTAGE,
    ResultsOverlay,
    Violation,
    find_violations,
    loss_summary,
    results_overlay,
    voltage_profile,
)
from core.grid.config import PowerFlowConfig
from core.grid.engines.opendss.engine import OpenDSSEngine
from core.grid.model import GridModel
from core.grid.result import GridResult
from core.grid.runner import CANCELLED, OK, TIMEOUT, GridRunner, GridRunOutcome
from core.netlist.model import ERROR
from core.physical.component import NetworkDiagnostic
from core.sim.result import SimulationResult

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

MAX_CONSOLE_LINES = 5000
LIMIT_LOW = "v_min"
"""Flat trace at the lower voltage limit in the profile (pu)."""
LIMIT_HIGH = "v_max"

_KIND_TEXT = {UNDERVOLTAGE: "Undervoltage", OVERVOLTAGE: "Overvoltage", OVERLOAD: "Overload"}


def engine_available() -> bool:
    """OpenDSSEngine.available(); a function so tests can replace it."""
    return OpenDSSEngine.available()


def _path_of(url_or_path: str) -> Path:
    url = QUrl(url_or_path)
    if url.isLocalFile():
        return Path(url.toLocalFile())
    return Path(url_or_path)


def _fmt(value: float | None, digits: int) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.{digits}f}"


def _split_node(node: str) -> tuple[str, int | None]:
    bus, _, tail = node.rpartition(".")
    if not bus or not tail.isdigit():
        return node, None
    return bus, int(tail)


# ---- results overlay (D8), shared with GridDiagramController

_PATH_ORDER = {OFF: 0, "none": 1, "pass": 2, "warn": 3, "fail": 4}
"""Draw order of the overlay paths: the worst status on top."""


def _num(value: float | None) -> float:
    return math.nan if value is None else float(value)


def overlay_map(overlay: ResultsOverlay) -> dict[str, dict[str, Any]]:
    """{id: {status, value, limit, unit, ratio, weight, text}} for QML (NaN = none)."""
    return {
        key: {
            "status": item.status,
            "value": _num(item.value),
            "limit": _num(item.limit),
            "unit": item.unit,
            "ratio": _num(item.ratio),
            "weight": item.weight,
            "text": item.text,
        }
        for key, item in overlay.items.items()
    }


def overlay_paths(
    segments: list[tuple[str, float, float, float, float]], overlay: ResultsOverlay
) -> list[dict[str, Any]]:
    """The segments (element id, x1, y1, x2, y2) of the elements in ``overlay`` as one
    SVG path per (status, weight): [{key, status, weight, path}], in drawing order
    (de-energized and unchecked first, failures last)."""
    parts: dict[tuple[str, int], list[str]] = {}
    for element, x1, y1, x2, y2 in segments:
        item = overlay.items.get(element)
        if item is None:
            continue
        parts.setdefault((item.status, item.weight), []).append(
            f"M{x1:.6g} {y1:.6g}L{x2:.6g} {y2:.6g}"
        )
    keys = sorted(parts, key=lambda k: (_PATH_ORDER.get(k[0], 0), k[1]))
    return [
        {
            "key": f"{status}_{weight}",
            "status": status,
            "weight": weight,
            "path": "".join(parts[(status, weight)]),
        }  # fmt: skip
        for status, weight in keys
    ]


def overlay_limits(config: PowerFlowConfig) -> dict[str, float]:
    """The limits the overlay was computed with, for its legend."""
    return {
        "voltageMinPu": config.voltage_min_pu,
        "voltageMaxPu": config.voltage_max_pu,
        "lineLimitPct": config.line_loading_limit_pct,
        "transformerLimitPct": config.transformer_loading_limit_pct,
        "warnPct": config.warn_fraction * 100.0,
    }


# ---- table models


@dataclass
class _Row:
    cells: list[str]
    keys: list[Any]
    """Sort keys, one per column (numbers or lower-case text)."""
    highlight: bool = False
    data: dict[str, Any] = field(default_factory=dict)


class GridTableModel(QAbstractTableModel):
    """Rows of one results table. Read-only, sortable by any column (``sortBy``),
    filtered by text and to the highlighted rows (``setFilter``, ``setOnlyHighlighted``),
    and saved as CSV (``saveCsv``)."""

    HighlightRole = Qt.ItemDataRole.UserRole + 1
    NumericRole = Qt.ItemDataRole.UserRole + 2

    sortChanged = Signal()
    filterChanged = Signal()
    countChanged = Signal()

    def __init__(self, columns: list[tuple[str, bool]], parent: QObject | None = None) -> None:
        """``columns``: (title, numeric) per column; numeric columns align right."""
        super().__init__(parent)
        self._columns = columns
        self._all: list[_Row] = []
        self._rows: list[_Row] = []  # the shown rows: filtered, then sorted
        self._sort_column = -1
        self._ascending = True
        self._filter = ""
        self._only_highlighted = False

    # Qt model API
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.cells[index.column()]
        if role == self.HighlightRole:
            return row.highlight
        if role == self.NumericRole:
            return self._columns[index.column()][1]
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0) -> Any:  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._columns):
            title = self._columns[section][0]
            if section == self._sort_column:
                title += " ▲" if self._ascending else " ▼"
            return title
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return {
            Qt.ItemDataRole.DisplayRole: b"display",
            self.HighlightRole: b"highlight",
            self.NumericRole: b"numeric",
        }

    # ours
    def set_rows(self, rows: list[_Row]) -> None:
        self.beginResetModel()
        self._all = list(rows)
        self._apply_filter()
        self.endResetModel()
        self.countChanged.emit()

    def rows(self) -> list[_Row]:
        """The shown rows (after the filter), in their shown order."""
        return list(self._rows)

    def _apply_filter(self) -> None:
        text = self._filter.strip().lower()
        text_columns = [i for i, (_, numeric) in enumerate(self._columns) if not numeric]

        def shown(row: _Row) -> bool:
            if self._only_highlighted and not row.highlight:
                return False
            return not text or any(text in row.cells[i].lower() for i in text_columns)

        self._rows = [r for r in self._all if shown(r)]
        self._sort_rows()

    def _refilter(self) -> None:
        self.beginResetModel()
        self._apply_filter()
        self.endResetModel()
        self.filterChanged.emit()
        self.countChanged.emit()

    def _get_filter(self) -> str:
        return self._filter

    def _get_only_highlighted(self) -> bool:
        return self._only_highlighted

    def _get_count(self) -> int:
        return len(self._rows)

    def _get_total(self) -> int:
        return len(self._all)

    filterText = Property(str, _get_filter, notify=filterChanged)  # noqa: N815
    onlyHighlighted = Property(bool, _get_only_highlighted, notify=filterChanged)  # noqa: N815
    count = Property(int, _get_count, notify=countChanged)
    totalCount = Property(int, _get_total, notify=countChanged)  # noqa: N815

    @Slot(str)
    def setFilter(self, text: str) -> None:  # noqa: N802 - QML API
        """Show only rows with ``text`` in a text column (bus, element, location...)."""
        if text != self._filter:
            self._filter = text
            self._refilter()

    @Slot(bool)
    def setOnlyHighlighted(self, on: bool) -> None:  # noqa: N802 - QML API
        """Show only the highlighted rows (those in violation)."""
        if on != self._only_highlighted:
            self._only_highlighted = on
            self._refilter()

    @Slot(str, result=bool)
    def saveCsv(self, url_or_path: str) -> bool:  # noqa: N802 - QML API
        """Write the shown rows, in their shown order, as CSV; False if it cannot."""
        path = _path_of(url_or_path)
        try:
            with path.open("w", encoding="utf-8", newline="") as f:
                out = csv.writer(f)
                out.writerow([title for title, _ in self._columns])
                for row in self._rows:
                    out.writerow(["" if c == "—" else c for c in row.cells])
        except OSError:
            return False
        return True

    def _get_sort_column(self) -> int:
        return self._sort_column

    def _get_ascending(self) -> bool:
        return self._ascending

    sortColumn = Property(int, _get_sort_column, notify=sortChanged)  # noqa: N815
    sortAscending = Property(bool, _get_ascending, notify=sortChanged)  # noqa: N815

    @Slot(int)
    def sortBy(self, column: int) -> None:  # noqa: N802 - QML API
        """Sort by ``column``; the same column again reverses the order."""
        if not 0 <= column < len(self._columns):
            return
        if column == self._sort_column:
            self._ascending = not self._ascending
        else:
            self._sort_column, self._ascending = column, True
        self.layoutAboutToBeChanged.emit()
        self._sort_rows()
        self.layoutChanged.emit()
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self._columns) - 1)
        self.sortChanged.emit()

    def _sort_rows(self) -> None:
        col = self._sort_column
        if col < 0:
            return

        def key(row: _Row) -> tuple[int, Any]:
            value = row.keys[col]
            missing = value is None or (isinstance(value, float) and math.isnan(value))
            # missing values last in either direction
            return (1 if missing else 0, 0 if missing else value)

        present = [r for r in self._rows if key(r)[0] == 0]
        missing = [r for r in self._rows if key(r)[0] == 1]
        present.sort(key=key, reverse=not self._ascending)
        self._rows = present + missing

    @Slot(int, int, result=str)
    def cell(self, row: int, column: int) -> str:
        """Text of one cell ("" outside the table); for tests and copy."""
        if 0 <= row < len(self._rows) and 0 <= column < len(self._columns):
            return self._rows[row].cells[column]
        return ""


NODE_COLUMNS = [
    ("Bus", False),
    ("Phase", False),
    ("|V| (kV)", True),
    ("V (pu)", True),
    ("Angle (°)", True),
]
BUS_COLUMNS = [
    ("Bus", False),
    ("Va (pu)", True),
    ("Vb (pu)", True),
    ("Vc (pu)", True),
    ("Min (pu)", True),
    ("Max (pu)", True),
]
"""One row per bus: the node table folded by phase (phase nodes a, b, c only)."""
BRANCH_COLUMNS = [
    ("Element", False),
    ("From", False),
    ("To", False),
    ("|I| per phase (A)", True),
    ("P (kW)", True),
    ("Q (kvar)", True),
    ("Losses (kW)", True),
    ("Losses (kvar)", True),
    ("Loading (%)", True),
]
VIOLATION_COLUMNS = [
    ("Kind", False),
    ("Location", False),
    ("Value", True),
    ("Limit", True),
    ("Message", False),
]


# ---- the controller


@QmlElement
@QmlSingleton
class GridController(QObject):
    """Open a .dss feeder, run a snapshot power flow and show the results."""

    fileChanged = Signal()
    runningChanged = Signal()
    statusTextChanged = Signal()
    consoleTextChanged = Signal()
    modelChanged = Signal()  # a new GridModel (geometry, canRun)
    resultChanged = Signal()  # a new result or none (tables, summary, geometry colours)
    selectedBusChanged = Signal()
    configChanged = Signal()  # new settings or limits (set_config)
    resultReady = Signal()
    loadFailed = Signal()
    modelRunFinished = Signal(object)  # GridRunOutcome of a runModel run
    _done = Signal(object)  # (runner, outcome), emitted from the wait thread

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._available: bool | None = None
        self.config = PowerFlowConfig()
        """Settings and limits of the next run (stored in the project); change it
        with ``set_config``."""
        self.timeout: float | None = None
        """Child timeout in s; None = GridRunner's default. Tests may set it."""
        self._path = ""
        self._model: GridModel | None = None
        self._load_diagnostics: list[NetworkDiagnostic] = []
        self._result: GridResult | None = None
        self._violations: list[Violation] = []
        self._losses: list[dict[str, Any]] = []
        self._overlay = ResultsOverlay()
        self._runner: GridRunner | None = None
        self._thread: threading.Thread | None = None
        self._status = ""
        self._console: list[str] = []
        self._selected_bus = ""
        self._waveforms: QObject | None = None
        self.profile: SimulationResult | None = None
        """The last voltage profile handed to the waveform window."""
        self._nodes = GridTableModel(NODE_COLUMNS, self)
        self._buses = GridTableModel(BUS_COLUMNS, self)
        self._branches = GridTableModel(BRANCH_COLUMNS, self)
        self._violation_rows = GridTableModel(VIOLATION_COLUMNS, self)
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # ---- properties

    def _get_available(self) -> bool:
        if self._available is None:
            self._available = bool(engine_available())
        return self._available

    def _get_file_path(self) -> str:
        return self._path

    def _get_file_name(self) -> str:
        return Path(self._path).name if self._path else ""

    def _get_running(self) -> bool:
        return self._runner is not None

    def _get_can_run(self) -> bool:
        return (
            self._get_available()
            and self._runner is None
            and self._model is not None
            and not self._has_errors(self._load_diagnostics)
        )

    def _get_status(self) -> str:
        return self._status

    def _get_console(self) -> str:
        return "\n".join(self._console)

    def _get_has_result(self) -> bool:
        return self._result is not None and self._result.converged

    def _get_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        model = self._model
        if model is not None:
            counts = model.counts()
            out["circuit"] = model.name
            out["elements"] = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
            out["baseFrequency"] = model.base_frequency
        r = self._result
        if r is not None and r.converged:
            out.update(
                converged=True,
                iterations=r.iterations,
                controlIterations=r.control_iterations,
                totalKw=_fmt(r.total_power_kw, 1),
                totalKvar=_fmt(r.total_power_kvar, 1),
                lossesKw=_fmt(r.total_losses_kw, 1),
                lossesKvar=_fmt(r.total_losses_kvar, 1),
                violationCount=len(self._violations),
                losses=list(self._losses),
            )
            total, lost = r.total_power_kw, r.total_losses_kw
            if total is not None and lost is not None and math.isfinite(total) and total > 0:
                out["lossesPct"] = _fmt(100.0 * lost / total, 2)
            phase_pu = self._bus_min_pu(), self._bus_max_pu()
            if phase_pu[0]:
                low = min(phase_pu[0].items(), key=lambda kv: kv[1])
                high = max(phase_pu[1].items(), key=lambda kv: kv[1])
                out.update(
                    minPu=_fmt(low[1], 4),
                    minPuBus=low[0],
                    maxPu=_fmt(high[1], 4),
                    maxPuBus=high[0],
                )
        return out

    def _get_geometry(self) -> dict[str, Any]:
        return self._geometry()

    def _get_overlay(self) -> dict[str, dict[str, Any]]:
        return overlay_map(self._overlay)

    def _get_overlay_paths(self) -> list[dict[str, Any]]:
        if not self._overlay.solved:
            return []
        segs = [(b["id"], b["x1"], b["y1"], b["x2"], b["y2"]) for b in self._geometry()["branches"]]
        return overlay_paths(segs, self._overlay)

    def _get_overlay_buses(self) -> list[dict[str, Any]]:
        if not self._overlay.solved:
            return []
        out = []
        for b in self._geometry()["buses"]:
            item = self._overlay.items.get(f"bus.{b['name']}")
            if item is not None:
                out.append({"id": f"bus.{b['name']}", "status": item.status, "x1": b["x"],
                            "y1": b["y"], "x2": b["x"], "y2": b["y"]})  # fmt: skip
        return out

    def _get_overlay_limits(self) -> dict[str, float]:
        return overlay_limits(self.config)

    def _get_selected_bus(self) -> str:
        return self._selected_bus

    def _get_selected_voltages(self) -> list[dict[str, Any]]:
        return self.busVoltages(self._selected_bus) if self._selected_bus else []

    def _get_nodes(self) -> QObject:
        return self._nodes

    def _get_buses(self) -> QObject:
        return self._buses

    def _get_branches(self) -> QObject:
        return self._branches

    def _get_violations(self) -> QObject:
        return self._violation_rows

    def _get_vmin(self) -> float:
        return self.config.voltage_min_pu

    def _get_vmax(self) -> float:
        return self.config.voltage_max_pu

    available = Property(bool, _get_available, constant=True)
    filePath = Property(str, _get_file_path, notify=fileChanged)  # noqa: N815
    fileName = Property(str, _get_file_name, notify=fileChanged)  # noqa: N815
    running = Property(bool, _get_running, notify=runningChanged)
    canRun = Property(bool, _get_can_run, notify=runningChanged)  # noqa: N815
    statusText = Property(str, _get_status, notify=statusTextChanged)  # noqa: N815
    consoleText = Property(str, _get_console, notify=consoleTextChanged)  # noqa: N815
    hasResult = Property(bool, _get_has_result, notify=resultChanged)  # noqa: N815
    summary = Property("QVariantMap", _get_summary, notify=resultChanged)
    geometry = Property("QVariantMap", _get_geometry, notify=resultChanged)
    overlay = Property("QVariantMap", _get_overlay, notify=resultChanged)
    overlayPaths = Property("QVariantList", _get_overlay_paths, notify=resultChanged)  # noqa: N815
    overlayBuses = Property("QVariantList", _get_overlay_buses, notify=resultChanged)  # noqa: N815
    overlayLimits = Property("QVariantMap", _get_overlay_limits, notify=resultChanged)  # noqa: N815
    selectedBus = Property(str, _get_selected_bus, notify=selectedBusChanged)  # noqa: N815
    selectedVoltages = Property(  # noqa: N815
        "QVariantList", _get_selected_voltages, notify=selectedBusChanged
    )
    nodes = Property(QObject, _get_nodes, constant=True)
    buses = Property(QObject, _get_buses, constant=True)
    branches = Property(QObject, _get_branches, constant=True)
    violations = Property(QObject, _get_violations, constant=True)
    voltageMinPu = Property(float, _get_vmin, notify=configChanged)  # noqa: N815
    voltageMaxPu = Property(float, _get_vmax, notify=configChanged)  # noqa: N815

    def model(self) -> GridModel | None:
        return self._model

    def result(self) -> GridResult | None:
        return self._result

    def violation_list(self) -> list[Violation]:
        return list(self._violations)

    # ---- QML API

    def set_config(self, config: PowerFlowConfig) -> None:
        """Use ``config`` for the next run. The violations and the limit traces of a
        result already shown are computed again with the new limits."""
        if config == self.config:
            return
        self.config = config
        self.configChanged.emit()
        if self._result is not None:
            self._set_result(self._result)

    def clear(self) -> None:
        """Forget the open file, its model and result (a new or other project). Stops
        a run in progress."""
        self.stop()
        self._runner = None
        self.runningChanged.emit()
        self._path = ""
        self._model = None
        self._load_diagnostics = []
        self._set_result(None)
        self._selected_bus = ""
        self._console = []
        self.consoleTextChanged.emit()
        self._set_status("")
        self.selectedBusChanged.emit()
        self.fileChanged.emit()
        self.modelChanged.emit()

    @Slot(QObject)
    def attachWaveforms(self, model: QObject) -> None:  # noqa: N802 - QML API
        """The WaveformModel that shows the voltage profile."""
        self._waveforms = model

    @Slot(str, result=bool)
    def openFile(self, url_or_path: str) -> bool:  # noqa: N802 - QML API
        """Load a .dss file (in a child process) to check it and draw it.

        Returns False when the load could not start (no engine, a run in progress,
        no such file); the reason is in the Console and ``statusText``.
        """
        if not self._get_available():
            self._refuse("Not opened: dss-python is not installed")
            return False
        if self._runner is not None:
            self._refuse("Not opened: a grid run is in progress")
            return False
        path = _path_of(url_or_path).expanduser()
        if not path.is_file():
            self._refuse(f"Not opened: file not found: {path}")
            return False
        self._path = str(path.resolve())
        self._model = None
        self._load_diagnostics = []
        self._set_result(None)
        self._selected_bus = ""
        self.selectedBusChanged.emit()
        self.fileChanged.emit()
        self.modelChanged.emit()
        self._console = []
        self._log([f"Opening {self._path}"])
        self._start(GridRunner(self._path, self.config, **self._timeout(), solve=False))
        self._set_status("Opening…")
        return True

    @Slot(result=bool)
    def run(self) -> bool:
        """Solve the open file in a child process. False (and why, in the Console)
        when it cannot start."""
        if not self._get_available():
            self._refuse("Not started: dss-python is not installed")
            return False
        if self._runner is not None:
            self._refuse("Not started: a grid run is in progress")
            return False
        if not self._path:
            self._refuse("Not started: open a .dss file first")
            return False
        if self._model is None or self._has_errors(self._load_diagnostics):
            self._refuse("Not started: the file has errors (see the Console)")
            return False
        problems = self.config.problems()
        if problems:
            self._refuse(f"Not started: power-flow settings: {problems[0]}")
            return False
        self._log(["", f"Power flow of {Path(self._path).name}"])
        self._start(GridRunner(self._path, self.config, **self._timeout()))
        self._set_status("Running…")
        return True

    def runModel(  # noqa: N802 - named as the .dss path's openFile/run
        self,
        model: GridModel,
        config: PowerFlowConfig | None = None,
        script_path: str | Path | None = None,
    ) -> str:
        """Solve ``model`` in a GridRunner child (rule 23), which writes its DSS script
        and saves it to ``script_path`` when given. Returns "" when the run started,
        else why not. The outcome is sent with ``modelRunFinished``."""
        if not self._get_available():
            return "dss-python is not installed"
        if self._runner is not None:
            return "a grid run is in progress"
        config = config or self.config
        problems = config.problems()
        if problems:
            return f"power-flow settings: {problems[0]}"
        self._start(GridRunner(model, config, **self._timeout(), script_path=script_path))
        return ""

    @Slot()
    def stop(self) -> None:
        if self._runner is not None:
            self._runner.cancel()

    @Slot()
    def shutdown(self) -> None:
        """Stop any run and wait for its thread (the app quits)."""
        self.stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)

    @Slot(str)
    def selectBus(self, bus: str) -> None:  # noqa: N802 - QML API
        bus = bus.lower()
        if bus != self._selected_bus:
            self._selected_bus = bus
            self.selectedBusChanged.emit()

    @Slot(str, result="QVariantList")
    def busVoltages(self, bus: str) -> list[dict[str, Any]]:  # noqa: N802 - QML API
        """Phase voltages of ``bus`` from the last result: [{node, phase, kv, pu,
        angle}] as text, one entry per node; [] without a result."""
        r = self._result
        if r is None or not r.converged:
            return []
        bus = bus.lower()
        out = []
        for i, node in enumerate(r.node_names):
            name, number = _split_node(node)
            if name != bus:
                continue
            v = complex(r.node_voltage[i])
            out.append(
                {
                    "node": node,
                    "phase": PHASE_LETTERS.get(number or 0, str(number)),
                    "kv": _fmt(abs(v) / 1000.0, 4),
                    "pu": _fmt(float(r.node_voltage_pu[i]), 5),
                    "angle": _fmt(math.degrees(math.atan2(v.imag, v.real)), 2),
                }
            )
        return out

    # ---- running

    def _timeout(self) -> dict[str, float]:
        return {} if self.timeout is None else {"timeout": self.timeout}

    def _start(self, runner: GridRunner) -> None:
        self._runner = runner
        self._thread = threading.Thread(target=self._wait, args=(runner,), daemon=True)
        self._thread.start()
        self.runningChanged.emit()

    def _wait(self, runner: GridRunner) -> None:
        outcome = runner.run()
        self._done.emit((runner, outcome))

    @Slot(object)
    def _on_done(self, payload: tuple[GridRunner, GridRunOutcome]) -> None:
        runner, outcome = payload
        if runner is not self._runner:
            return
        self._runner = None
        if runner.from_model:
            self.runningChanged.emit()
            self.modelRunFinished.emit(outcome)
            return
        if runner.solve:
            self._run_done(outcome)
        else:
            self._load_done(outcome)
        self.runningChanged.emit()

    def _process_failed(self, outcome: GridRunOutcome, verb: str) -> bool:
        if outcome.status == OK:
            return False
        if outcome.status == CANCELLED:
            self._set_status("Stopped")
        elif outcome.status == TIMEOUT:
            self._set_status(f"Failed: {outcome.message}")
        else:
            self._set_status(f"Failed: {outcome.message}")
            if outcome.traceback:
                self._log(outcome.traceback.rstrip().split("\n"))
        self._log([f"{verb}: {self._status}"])
        return True

    def _load_done(self, outcome: GridRunOutcome) -> None:
        if self._process_failed(outcome, "Open"):
            self.loadFailed.emit()
            return
        self._load_diagnostics = list(outcome.diagnostics)
        self._model = outcome.model
        self._log_diagnostics(outcome.diagnostics)
        errors = [d for d in outcome.diagnostics if d.severity == ERROR]
        if errors or self._model is None:
            first = errors[0].message if errors else "the circuit could not be read"
            self._set_status(f"Not opened: {first}")
            self.loadFailed.emit()
        else:
            counts = self._model.counts()
            self._set_status(
                f"Opened {self._model.name}: {counts['buses']} buses, "
                f"{counts['lines'] + counts['switches']} lines, "
                f"{counts['transformers']} transformers"
            )
        self._log([self._status])
        self.modelChanged.emit()
        self.resultChanged.emit()

    def _run_done(self, outcome: GridRunOutcome) -> None:
        if self._process_failed(outcome, "Run"):
            return
        self._log_diagnostics(outcome.diagnostics)
        result = outcome.result
        if result is None:
            errors = [d for d in outcome.diagnostics if d.severity == ERROR]
            first = errors[0].message if errors else "the file did not load"
            self._set_status(f"Not started: {first}")
            self._log([self._status])
            return
        if outcome.model is not None:
            self._model = outcome.model  # regulator taps after the solve
            self.modelChanged.emit()
        if not result.converged:
            self._set_result(None)
            self._set_status(
                f"Failed: power flow did not converge after {result.iterations} iterations"
            )
            self._log([self._status])
            return
        self._set_result(result)
        r = result
        self._set_status(
            f"Finished: converged in {r.iterations} iterations "
            f"({r.control_iterations} control), losses {r.total_losses_kw:.1f} kW, "
            f"{len(self._violations)} violation{'s' if len(self._violations) != 1 else ''}"
        )
        self._log(
            [
                f"Total power from the source: {r.total_power_kw:.1f} kW, "
                f"{r.total_power_kvar:.1f} kvar",
                f"Losses: {r.total_losses_kw:.2f} kW, {r.total_losses_kvar:.2f} kvar",
                *[f"  warning: {v.to_diagnostic().block}: {v.message()}" for v in self._violations],
                self._status,
            ]
        )
        self.resultReady.emit()

    # ---- results

    def _set_result(self, result: GridResult | None) -> None:
        self._result = result
        self._violations = []
        self._losses = []
        self._overlay = ResultsOverlay()
        self.profile = None
        if result is not None and result.converged:
            self._violations = find_violations(result, self.config, self._model)
            if self._model is not None:
                self._overlay = results_overlay(self._model, result, self.config)
                self._losses = [
                    {
                        "kind": row.element_class,
                        "count": row.count,
                        "kw": _fmt(row.kw, 2),
                        "kvar": _fmt(row.kvar, 2),
                    }
                    for row in loss_summary(self._model, result)
                ]
                self._log_diagnostics(self.show_profile(self._model, result))
        self._fill_tables()
        self.resultChanged.emit()
        self.selectedBusChanged.emit()

    def show_profile(
        self, model: GridModel, result: GridResult, config: PowerFlowConfig | None = None
    ) -> list[NetworkDiagnostic]:
        """Hand the voltage profile of ``result`` (the solve of ``model``) to the
        waveform window as a SimulationResult (rule 7), with flat traces at the
        voltage limits of ``config`` (default: this view's); kept in ``profile``.
        Returns the profile's diagnostics (unreachable buses) for the caller's
        Console."""
        config = config or self.config
        profile = voltage_profile(model, result)
        sim = result.to_simulation_result(profile)
        n = len(sim.vectors[sim.axis])
        sim.vectors[LIMIT_LOW] = np.full(n, config.voltage_min_pu)
        sim.vectors[LIMIT_HIGH] = np.full(n, config.voltage_max_pu)
        sim.units[LIMIT_LOW] = sim.units[LIMIT_HIGH] = "pu"
        self.profile = sim
        if self._waveforms is not None and n:
            self._waveforms.set_result(sim)  # type: ignore[attr-defined]
        return profile.diagnostics

    def _fill_tables(self) -> None:
        r = self._result
        if r is None or not r.converged:
            for table in (self._nodes, self._buses, self._branches, self._violation_rows):
                table.set_rows([])
            return
        bad_nodes = {v.location for v in self._violations if v.kind != OVERLOAD}
        overloaded = {v.location for v in self._violations if v.kind == OVERLOAD}

        nodes = []
        for i, node in enumerate(r.node_names):
            bus, number = _split_node(node)
            v = complex(r.node_voltage[i])
            kv = abs(v) / 1000.0
            pu = float(r.node_voltage_pu[i])
            angle = math.degrees(math.atan2(v.imag, v.real))
            phase = PHASE_LETTERS.get(number or 0, str(number) if number is not None else "")
            nodes.append(
                _Row(
                    [bus, phase, _fmt(kv, 4), _fmt(pu, 5), _fmt(angle, 2)],
                    [bus, phase, kv, pu, angle],
                    node in bad_nodes,
                )
            )
        self._nodes.set_rows(nodes)

        bad_buses = {_split_node(node)[0] for node in bad_nodes}
        buses = []
        for bus, phases in self._phase_pu().items():
            values = [phases.get(p, math.nan) for p in ("a", "b", "c")]
            low, high = min(phases.values()), max(phases.values())
            buses.append(
                _Row(
                    [bus, *(_fmt(v, 5) for v in values), _fmt(low, 5), _fmt(high, 5)],
                    [bus, *values, low, high],
                    bus in bad_buses,
                )
            )
        self._buses.set_rows(buses)

        branches = []
        for element, br in r.branches.items():
            mags = np.abs(np.asarray(br.current))
            current = " / ".join(_fmt(float(m), 1) for m in mags)
            imax = float(mags.max()) if len(mags) else math.nan
            loading = br.loading_pct
            branches.append(
                _Row(
                    [
                        element,
                        br.bus1,
                        br.bus2,
                        current,
                        _fmt(br.p_kw, 1),
                        _fmt(br.q_kvar, 1),
                        _fmt(br.losses_kw, 2),
                        _fmt(br.losses_kvar, 2),
                        _fmt(loading, 1),
                    ],
                    [
                        element,
                        br.bus1,
                        br.bus2,
                        imax,
                        br.p_kw,
                        br.q_kvar,
                        br.losses_kw,
                        br.losses_kvar,
                        math.nan if loading is None else loading,
                    ],
                    element in overloaded,
                )
            )
        self._branches.set_rows(branches)

        rows = []
        for v in self._violations:
            digits = 1 if v.unit == "%" else 4
            rows.append(
                _Row(
                    [
                        _KIND_TEXT.get(v.kind, v.kind),
                        v.location,
                        f"{v.value:.{digits}f} {v.unit}",
                        f"{v.limit:g} {v.unit}",
                        v.message(),
                    ],
                    [v.kind, v.location, v.value, v.limit, v.message()],
                    True,
                )
            )
        self._violation_rows.set_rows(rows)

    def _geometry(self) -> dict[str, Any]:
        """The single-line view: buses with coordinates and the branches between them.

        {hasCoordinates, missing (buses without), minX, maxX, minY, maxY,
        buses: [{name, x, y, minPu (NaN before a run), source}],
        branches: [{id, kind, x1, y1, x2, y2, open, overloaded}]}
        """
        model = self._model
        if model is None:
            return {"hasCoordinates": False, "buses": [], "branches": [], "missing": 0}
        xy = {
            name: (bus.x, bus.y)
            for name, bus in model.buses.items()
            if bus.x is not None and bus.y is not None
        }
        if not xy:
            return {
                "hasCoordinates": False,
                "buses": [],
                "branches": [],
                "missing": len(model.buses),
            }
        min_pu = self._bus_min_pu()
        sources = {s.bus1.bus for s in model.sources.values()}
        overloaded = {v.location for v in self._violations if v.kind == OVERLOAD}
        buses = [
            {
                "name": name,
                "x": float(x),
                "y": float(y),
                "minPu": min_pu.get(name, math.nan),
                "source": name in sources,
            }
            for name, (x, y) in sorted(xy.items())
        ]
        branches: list[dict[str, Any]] = []

        def add(element: str, kind: str, a: str, b: str, open_: bool) -> None:
            if a in xy and b in xy and a != b:
                branches.append(
                    {
                        "id": element,
                        "kind": kind,
                        "x1": float(xy[a][0]),
                        "y1": float(xy[a][1]),
                        "x2": float(xy[b][0]),
                        "y2": float(xy[b][1]),
                        "open": open_,
                        "overloaded": element in overloaded,
                    }
                )

        for line in model.lines.values():
            add(line.id, "line", line.bus1.bus, line.bus2.bus, not line.enabled)
        for sw in model.switches.values():
            add(sw.id, "switch", sw.bus1.bus, sw.bus2.bus, not (sw.closed and sw.enabled))
        for tr in model.transformers.values():
            first = tr.windings[0].terminal.bus if tr.windings else ""
            for w in tr.windings[1:]:
                add(tr.id, "transformer", first, w.terminal.bus, not tr.enabled)
        xs = [b["x"] for b in buses]
        ys = [b["y"] for b in buses]
        return {
            "hasCoordinates": True,
            "missing": len(model.buses) - len(xy),
            "minX": min(xs),
            "maxX": max(xs),
            "minY": min(ys),
            "maxY": max(ys),
            "buses": buses,
            "branches": branches,
        }

    def _phase_pu(self) -> dict[str, dict[str, float]]:
        """{bus: {phase letter: pu}} of the phase nodes (a, b, c) of the last result."""
        r = self._result
        out: dict[str, dict[str, float]] = {}
        if r is None or not r.converged:
            return out
        for node, pu in zip(r.node_names, r.node_voltage_pu, strict=False):
            bus, number = _split_node(node)
            pu = float(pu)
            if number not in PHASE_LETTERS or math.isnan(pu):
                continue
            out.setdefault(bus, {})[PHASE_LETTERS[number]] = pu
        return out

    def _bus_min_pu(self) -> dict[str, float]:
        return {bus: min(p.values()) for bus, p in self._phase_pu().items()}

    def _bus_max_pu(self) -> dict[str, float]:
        return {bus: max(p.values()) for bus, p in self._phase_pu().items()}

    # ---- console and status

    @staticmethod
    def _has_errors(diagnostics: list[NetworkDiagnostic]) -> bool:
        return any(d.severity == ERROR for d in diagnostics)

    def _refuse(self, status: str) -> None:
        self._set_status(status)
        self._log([status])

    def _set_status(self, text: str) -> None:
        if text != self._status:
            self._status = text
            self.statusTextChanged.emit()

    def _log_diagnostics(self, diagnostics: list[NetworkDiagnostic]) -> None:
        self._log([f"  {d.severity}: {d.block}: {d.message}" for d in diagnostics])

    def _log(self, lines: list[str]) -> None:
        if not lines:
            return
        self._console.extend(lines)
        if len(self._console) > MAX_CONSOLE_LINES:
            del self._console[: len(self._console) - MAX_CONSOLE_LINES]
        self.consoleTextChanged.emit()
