"""SchematicController: the QML face of the schematic editor.

Editor side (S5-S8): one sheet, shown to QML as the list models ``instances``,
``wires`` and ``flags`` (net labels, ground and port flags) plus ``junctions`` and
``wirePath``. Drawing data and hit-testing come from core.schematic.geometry, so QML
only draws. Every edit goes through the controller's own QUndoStack; a live session
(placing, dragging, pasting) is one undo step. Selection is view state.

Library side (L8): symbol and library search paths, the symbol list,
searching models and subcircuits, and "Use with symbol..." which pairs a library entry
with a symbol and fills SpiceModel/ModelFile. The result is kept as ``pendingPart``
for the part picker of the schematic editor (S tasks) to place.

N0: one default library folder (``defaultLibraryFolder``: ``library/`` in the
repository when QtSpice runs from source, ``Documents/QtSpice/library`` in the
packaged app, whose install folder is not writable; the app creates it at start-up)
holds the user's
``.lib`` files and ``.asy`` symbols. It is searched first among the user's symbol
folders and library folders, and ``include_folders()`` (it, then the library folders)
is where ``.include <name>`` looks after the netlist's own folder: the Simulator gets
the list in ``attachSimulator`` and again whenever it changes.

Search paths are per user, not per project, and live in QSettings (as JSON text, like
ProjectManager's lists). Every folder is read-only: nothing here writes to it.
Changing a path rescans what it affects and emits ``symbolsChanged`` or
``libraryChanged``, so the browser's lists refresh.
"""

from __future__ import annotations

import copy
import json
import math
import sys
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import (
    Property,
    QModelIndex,
    QObject,
    QSettings,
    QStandardPaths,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QUndoCommand, QUndoStack
from PySide6.QtQml import QmlElement, QmlSingleton

from bridge.diagram_controller import RowModel
from bridge.waveform_model import si
from core.hierarchy.ports import (
    GROUND_NAMES,
    PortRow,
    name_problem,
    netlist_ports,
    schematic_ports,
)
from core.hierarchy.symbolgen import GeneratedSymbol, SymbolGenerator, symbol_name_problem
from core.netlist.libindex import LIBRARY_SUFFIXES, LibraryIndex
from core.netlist.model import ERROR
from core.netlist.parser import parse
from core.schematic import edit as edit_ops
from core.schematic import probe as probe_ops
from core.schematic.connectivity import NetMap, connect
from core.schematic.geometry import (
    GROUND,
    INSTANCE,
    LABEL,
    PORT,
    TEXT,
    WIRE,
    bounds,
    hit_test,
    instance_box,
    instance_texts,
    items_in_rect,
    items_of,
    on_segment,
    orientation_matrix,
    part_value,
    pin_near,
    snap,
    symbol_box,
    symbol_svg,
    turn,
)
from core.schematic.model import (
    PORT_DIRECTIONS,
    GroundFlag,
    Instance,
    NetLabel,
    PartInstance,
    PortFlag,
    Schematic,
    TextItem,
    Wire,
    pin_points,
)
from core.schematic.netlister import (
    NET_FLAG_SYMBOLS,
    NetlistedSchematic,
    netlist_part,
    netlist_schematic,
)
from core.schematic.probe import Probe
from core.sim.result import SimulationResult
from core.symbols.asy import Symbol
from core.symbols.library import PROJECT, SymbolLibrary

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

SYMBOL_PATHS_KEY = "library/symbolPaths"
LIBRARY_PATHS_KEY = "library/libraryPaths"
LTSPICE_FOLDER_KEY = "library/ltspiceFolder"
DEFAULT_LIBRARY_KEY = "library/defaultFolder"


REPO_LIBRARY = Path(__file__).resolve().parents[1] / "library"


def default_library_folder() -> Path:
    """Where the user keeps .lib files and symbols unless they chose another folder (N0):
    ``library/`` in the repository when running from source, else (packaged app)
    ``Documents/QtSpice/library``."""
    if not getattr(sys, "frozen", False):
        return REPO_LIBRARY
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(documents or Path.home()) / "QtSpice" / "library"


def ensure_default_library_folder(settings: QSettings | None = None) -> Path | None:
    """Create the chosen (or default) library folder if it is missing; called once at
    app start-up. Returns the folder, or None when it cannot be made."""
    settings = settings if settings is not None else QSettings()
    folder = Path(str(settings.value(DEFAULT_LIBRARY_KEY, "") or default_library_folder()))
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return folder


MAX_RESULTS = 500
# LTspice keeps its standard models in lib/cmp as standard.bjt, standard.dio, ...
LTSPICE_SUFFIXES = LIBRARY_SUFFIXES | frozenset({".bjt", ".dio", ".mos", ".jft"})

# .model type -> (device letter it needs, built-in symbol to suggest)
_MODEL_TYPES: dict[str, tuple[str, str]] = {
    "d": ("D", "diode"),
    "npn": ("Q", "npn"),
    "pnp": ("Q", "pnp"),
    "lpnp": ("Q", "pnp"),
    "nmos": ("M", "nmos"),
    "pmos": ("M", "pmos"),
    "vdmos": ("M", "nmos"),
}


@QmlElement
@QmlSingleton
class SchematicController(QObject):
    symbolPathsChanged = Signal()
    libraryPathsChanged = Signal()
    ltspiceFolderChanged = Signal()
    defaultLibraryFolderChanged = Signal()
    symbolsChanged = Signal()
    libraryChanged = Signal()
    pendingPartChanged = Signal()
    sheetChanged = Signal()  # the sheet's models, paths or junctions changed

    def __init__(self, parent: QObject | None = None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings()
        self._symbol_paths = self._read_list(SYMBOL_PATHS_KEY)
        self._library_paths = self._read_list(LIBRARY_PATHS_KEY)
        self._ltspice = str(self._settings.value(LTSPICE_FOLDER_KEY, "") or "")
        self._default_library = str(
            self._settings.value(DEFAULT_LIBRARY_KEY, "") or default_library_folder()
        )
        self._symbols = SymbolLibrary(self._user_symbol_dirs(), self._ltspice or None)
        self._index: LibraryIndex | None = None
        self._pending: dict[str, object] = {}
        self._init_editor()

    # ---- search paths

    def _get_symbol_paths(self) -> list[str]:
        return list(self._symbol_paths)

    def _set_symbol_paths(self, paths: list[str]) -> None:
        paths = _clean(paths)
        if paths != self._symbol_paths:
            self._symbol_paths = paths
            self._write(SYMBOL_PATHS_KEY, json.dumps(paths))
            self.symbolPathsChanged.emit()
            self._rescan_symbols()

    def _get_library_paths(self) -> list[str]:
        return list(self._library_paths)

    def _set_library_paths(self, paths: list[str]) -> None:
        paths = _clean(paths)
        if paths != self._library_paths:
            self._library_paths = paths
            self._write(LIBRARY_PATHS_KEY, json.dumps(paths))
            self.libraryPathsChanged.emit()
            self._rescan_library()

    def _get_ltspice_folder(self) -> str:
        return self._ltspice

    def _set_ltspice_folder(self, folder: str) -> None:
        folder = folder.strip()
        if folder != self._ltspice:
            self._ltspice = folder
            self._write(LTSPICE_FOLDER_KEY, folder)
            self.ltspiceFolderChanged.emit()
            self._rescan_symbols()
            self._rescan_library()

    symbolPaths = Property(  # noqa: N815
        "QStringList", _get_symbol_paths, _set_symbol_paths, notify=symbolPathsChanged
    )
    libraryPaths = Property(  # noqa: N815
        "QStringList", _get_library_paths, _set_library_paths, notify=libraryPathsChanged
    )
    ltspiceFolder = Property(  # noqa: N815
        str, _get_ltspice_folder, _set_ltspice_folder, notify=ltspiceFolderChanged
    )

    # ---- N0: the default library folder and where .include looks

    def _get_default_library_folder(self) -> str:
        return self._default_library

    def _set_default_library_folder(self, folder: str) -> None:
        folder = _local(folder).strip() or str(default_library_folder())
        if folder != self._default_library:
            self._default_library = folder
            self._write(DEFAULT_LIBRARY_KEY, folder)
            self.defaultLibraryFolderChanged.emit()
            self._rescan_symbols()
            self._rescan_library()

    defaultLibraryFolder = Property(  # noqa: N815
        str,
        _get_default_library_folder,
        _set_default_library_folder,
        notify=defaultLibraryFolderChanged,
    )

    @Slot(result=bool)
    def openDefaultLibraryFolder(self) -> bool:  # noqa: N802
        """Show the library folder in the file manager, creating it if it is missing."""
        folder = Path(self._default_library)
        existed = folder.is_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        if not existed:  # it exists now: start searching it
            self._rescan_symbols()
            self._rescan_library()
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))

    @Slot()
    def resetDefaultLibraryFolder(self) -> None:  # noqa: N802
        """Back to the default location (forget a chosen folder)."""
        self._settings.remove(DEFAULT_LIBRARY_KEY)
        self._settings.sync()
        folder = str(default_library_folder())
        if folder != self._default_library:
            self._default_library = folder
            self.defaultLibraryFolderChanged.emit()
            self._rescan_symbols()
            self._rescan_library()

    def _default_dirs(self) -> list[str]:
        """The default library folder when it exists (a missing one is no error)."""
        return [self._default_library] if Path(self._default_library).is_dir() else []

    def _user_symbol_dirs(self) -> list[str]:
        return _clean(self._default_dirs() + self._symbol_paths)

    def include_folders(self) -> list[Path]:
        """Where ``.include <name>`` looks after the netlist's folder: the default
        library folder, then the library folders, in order."""
        return [Path(p) for p in _clean([self._default_library, *self._library_paths])]

    def _push_include_folders(self) -> None:
        sim = self._simulator
        setter = getattr(sim, "set_include_folders", None) if sim is not None else None
        if setter is not None:
            setter(self.include_folders())

    @Slot(str, str)
    def addFolder(self, kind: str, folder: str) -> None:  # noqa: N802
        """Append a folder (path or file:// URL) to "symbols" or "libraries"."""
        path = _local(folder)
        if not path:
            return
        if kind == "symbols":
            self._set_symbol_paths([*self._symbol_paths, path])
        elif kind == "libraries":
            self._set_library_paths([*self._library_paths, path])

    @Slot(str, int)
    def removeFolder(self, kind: str, index: int) -> None:  # noqa: N802
        if kind == "symbols" and 0 <= index < len(self._symbol_paths):
            self._set_symbol_paths(self._symbol_paths[:index] + self._symbol_paths[index + 1 :])
        elif kind == "libraries" and 0 <= index < len(self._library_paths):
            self._set_library_paths(self._library_paths[:index] + self._library_paths[index + 1 :])

    @Slot(str)
    def setLtspiceFolderUrl(self, folder: str) -> None:  # noqa: N802
        """Set the LTspice folder from a path or file:// URL; "" clears it."""
        self._set_ltspice_folder(_local(folder))

    @Slot()
    def refresh(self) -> None:
        """Re-read every folder (files may have changed on disk)."""
        self._rescan_symbols()
        self._rescan_library()

    # ---- symbols

    def _get_symbol_names(self) -> list[str]:
        return self._symbols.names()

    symbolNames = Property("QStringList", _get_symbol_names, notify=symbolsChanged)  # noqa: N815

    @Slot(str, result="QVariantList")
    def symbolPins(self, name: str) -> list[dict[str, object]]:  # noqa: N802
        """Pins of a symbol in SpiceOrder: [{name, order}]."""
        symbol, _ = self._symbols.lookup(name)
        if symbol is None:
            return []
        return [{"name": p.name, "order": p.spice_order or 0} for p in symbol.pins]

    def _rescan_symbols(self) -> None:
        self._symbols.set_paths(self._user_symbol_dirs(), self._ltspice or None)
        self._symbols.refresh()
        self.symbolsChanged.emit()

    # ---- library index

    def index(self) -> LibraryIndex:
        if self._index is None:
            index = LibraryIndex.scan(self._default_dirs() + self._library_paths)
            lt = self._ltspice_library_folders()
            if lt:
                extra = LibraryIndex.scan(lt, LTSPICE_SUFFIXES)
                index.models += extra.models
                index.subcircuits += extra.subcircuits
                index.files += extra.files
                index.diagnostics += extra.diagnostics
            self._index = index
        return self._index

    def _ltspice_library_folders(self) -> list[Path]:
        if not self._ltspice:
            return []
        root = Path(self._ltspice)
        subs = [root / name for name in ("sub", "cmp") if (root / name).is_dir()]
        return subs or [root]

    def _rescan_library(self) -> None:
        self._index = None
        self._push_include_folders()
        self.libraryChanged.emit()

    def _get_library_status(self) -> str:
        index = self.index()
        text = (
            f"{len(index.models)} models, {len(index.subcircuits)} subcircuits "
            f"in {len(index.files)} files"
        )
        if encrypted := len(index.encrypted_files):
            text += f" ({encrypted} encrypted, not indexed)"
        if index.diagnostics:
            text += f"; {index.diagnostics[0].message}"
        return text

    libraryStatus = Property(str, _get_library_status, notify=libraryChanged)  # noqa: N815

    @Slot(str, result="QVariantList")
    def searchLibrary(self, text: str) -> list[dict[str, object]]:  # noqa: N802
        """Models and subcircuits whose name (or model type) contains ``text``."""
        key = text.strip().casefold()
        index = self.index()
        found: list[dict[str, object]] = []
        for s in index.subcircuits:
            if key in s.name.casefold():
                found.append(
                    _entry("subckt", s.name, "subckt", list(s.pins), s.file, s.line, s.section)
                )
        for m in index.models:
            if key in m.name.casefold() or key == m.type.casefold():
                found.append(_entry("model", m.name, m.type, [], m.file, m.line, m.section))
        found.sort(key=lambda e: (str(e["name"]).casefold(), str(e["file"])))
        return found[:MAX_RESULTS]

    @Slot("QVariantMap", result=str)
    def suggestedSymbol(self, entry: dict[str, object]) -> str:  # noqa: N802
        """A symbol that fits a search result, or "" when none is obvious."""
        if entry.get("kind") == "subckt":
            pins = entry.get("pins") or []
            return "opamp" if len(pins) == 5 and self._symbols.find("opamp") else ""
        _, symbol = _MODEL_TYPES.get(str(entry.get("type", "")).casefold(), ("", ""))
        return symbol if symbol and self._symbols.find(symbol) else ""

    # ---- use with symbol

    def _get_pending_part(self) -> dict[str, object]:
        return dict(self._pending)

    pendingPart = Property("QVariantMap", _get_pending_part, notify=pendingPartChanged)  # noqa: N815

    @Slot("QVariantMap", str, result="QVariantMap")
    def useWithSymbol(self, entry: dict[str, object], symbol_name: str) -> dict[str, object]:  # noqa: N802
        """Pair a search result with a symbol: {ok, symbol, attrs, message}.

        On success ``attrs`` holds SpiceModel and ModelFile and the pair becomes
        ``pendingPart``. A symbol that cannot use the entry (wrong device letter) is an
        error; a pin-count mismatch or a .lib section only a warning in ``message``.
        """
        symbol, _ = self._symbols.lookup(symbol_name)
        name = str(entry.get("name", ""))
        if symbol is None or not name:
            return {"ok": False, "message": f"symbol not found: {symbol_name}"}
        letter = symbol.prefix[:1].upper()
        notes: list[str] = []
        if entry.get("kind") == "subckt":
            if letter != "X":
                return {
                    "ok": False,
                    "message": f"{symbol_name} has Prefix {symbol.prefix or '(none)'}; "
                    "a subcircuit needs a Prefix X symbol",
                }
            pins = list(entry.get("pins") or [])
            if len(pins) != len(symbol.pins):
                notes.append(
                    f"warning: {name} has {len(pins)} pins, {symbol_name} has {len(symbol.pins)}"
                )
        else:
            kind = str(entry.get("type", ""))
            needed, _ = _MODEL_TYPES.get(kind.casefold(), ("", ""))
            if needed and letter != needed:
                return {
                    "ok": False,
                    "message": f"a {kind} model needs a Prefix {needed} symbol; "
                    f"{symbol_name} has Prefix {symbol.prefix or '(none)'}",
                }
        if entry.get("section"):
            notes.append(
                f"warning: {name} is in .lib section {entry['section']}; "
                "ModelFile includes the whole file"
            )
        attrs = {"SpiceModel": name, "ModelFile": str(entry.get("file", ""))}
        self._pending = {"symbol": symbol_name, "attrs": attrs}
        self.pendingPartChanged.emit()
        return {"ok": True, "symbol": symbol_name, "attrs": attrs, "message": "; ".join(notes)}

    # ================================================================ schematic editor
    #
    # The sheet is one core Schematic. QML sees it through the list models
    # ``instances``, ``wires``, ``flags`` and ``junctions`` and draws all wires as one
    # path (``wirePath``). Every edit is a _Snapshot command on ``undoStack``; a live
    # session (placing, dragging, pasting) changes the sheet while the mouse moves and
    # becomes one undo step when it ends. Selection is view state, not undoable.

    def _init_editor(self) -> None:
        self._schematic = Schematic()
        self._undo = QUndoStack(self)
        self._live: _Live | None = None
        self._clipboard: Schematic | None = None
        self._simulator: QObject | None = None
        self._problems: list[dict[str, object]] = []
        self._ran: _Ran | None = None
        self._waveforms: QObject | None = None
        self._awaiting = False
        self._probe_result: SimulationResult | None = None
        self._probe_message = ""
        self._probed: list[Probe] = []
        self._selection: set[tuple[str, int]] = set()
        self._symbol_cache: dict[str, Symbol | None] = {}
        self._geometry_cache: dict[str, dict[str, object]] = {}
        self._instances = SyncModel(INSTANCE_ROLES, self)
        self._wires = SyncModel(WIRE_ROLES, self)
        self._flags = SyncModel(FLAG_ROLES, self)
        self._junctions: list[dict[str, int]] = []
        self._texts: list[dict[str, object]] = []
        self._ports: list[dict[str, object]] = []
        self._port_rows: list[PortRow] = []
        self._project_file = ""
        self._project_symbols: list[Path] = []
        self._wire_path = ""
        self._selected_wire_path = ""
        for signal in (
            self._undo.canUndoChanged,
            self._undo.canRedoChanged,
            self._undo.undoTextChanged,
            self._undo.redoTextChanged,
        ):
            signal.connect(self.undoStateChanged)
        self._undo.indexChanged.connect(self.schematicEdited)
        self.schematicEdited.connect(self.probeChanged)
        self.symbolsChanged.connect(self._symbols_rescanned)

    # ---- Python side

    def schematic(self) -> Schematic:
        """A copy of the sheet as it is now."""
        return copy.deepcopy(self._schematic)

    def is_empty(self) -> bool:
        s = self._schematic
        return not (
            s.title or s.instances or s.wires or s.labels or s.grounds or s.ports or s.texts
        )

    def schematic_or_none(self) -> Schematic | None:
        """The sheet for saving: None when it is empty (projects without one stay so)."""
        return None if self.is_empty() else self.schematic()

    def netlist_text(self) -> str:
        """The sheet as netlist text (title first, ``.end`` last), as Run would send it,
        without touching ``problems``. "" for an empty sheet."""
        if self.is_empty():
            return ""
        return netlist_schematic(
            self._schematic, self.lookup, self._base_dir(), self.include_folders()
        ).text

    def load_schematic(self, schematic: Schematic | None) -> None:
        """Replace the sheet (opening a project). Clears the undo stack and selection."""
        self._live = None
        self._undo.clear()
        self._selection = set()
        self._set(copy.deepcopy(schematic) if schematic is not None else Schematic())
        self.schematicEdited.emit()
        self.selectionChanged.emit()

    def undo_stack(self) -> QUndoStack:
        return self._undo

    def lookup(self, name: str) -> Symbol | None:
        if name not in self._symbol_cache:
            self._symbol_cache[name] = self._symbols.lookup(name)[0]
        return self._symbol_cache[name]

    def _symbols_rescanned(self) -> None:
        self._symbol_cache.clear()
        self._geometry_cache.clear()
        self._set(self._schematic)

    # ---- models

    def _get_instances(self) -> QObject:
        return self._instances

    def _get_wires(self) -> QObject:
        return self._wires

    def _get_flags(self) -> QObject:
        return self._flags

    def _get_junctions(self) -> list[dict[str, int]]:
        return list(self._junctions)

    def _get_texts(self) -> list[dict[str, object]]:
        return list(self._texts)

    def _get_wire_path(self) -> str:
        return self._wire_path

    def _get_ports(self) -> list[dict[str, object]]:
        return list(self._ports)

    def _get_selected_wire_path(self) -> str:
        return self._selected_wire_path

    instances = Property(QObject, _get_instances, constant=True)
    wires = Property(QObject, _get_wires, constant=True)
    flags = Property(QObject, _get_flags, constant=True)
    junctions = Property("QVariantList", _get_junctions, notify=sheetChanged)
    texts = Property("QVariantList", _get_texts, notify=sheetChanged)
    # H1: one row per port flag: index, name, direction, net, severity, message.
    ports = Property("QVariantList", _get_ports, notify=sheetChanged)
    wirePath = Property(str, _get_wire_path, notify=sheetChanged)  # noqa: N815
    selectedWirePath = Property(str, _get_selected_wire_path, notify=sheetChanged)  # noqa: N815

    def _set(self, schematic: Schematic, live: bool = False) -> None:
        """Show ``schematic``. ``live`` skips the junction search (mouse moves)."""
        self._schematic = schematic
        count = {kind: len(items_of(schematic, kind)) for kind in KINDS}
        self._selection = {(k, i) for k, i in self._selection if i < count[k]}
        self._refresh(live)

    def _refresh(self, live: bool = False) -> None:
        s = self._schematic
        sel = self._selection
        self._instances.sync(
            [
                self._instance_row(i, inst, (INSTANCE, i) in sel)
                for i, inst in enumerate(s.instances)
            ]
        )
        self._wires.sync(
            [
                {
                    "index": i,
                    "x1": w.x1,
                    "y1": w.y1,
                    "x2": w.x2,
                    "y2": w.y2,
                    "selected": (WIRE, i) in sel,
                }
                for i, w in enumerate(s.wires)
            ]
        )
        flags: list[dict[str, object]] = []
        for i, n in enumerate(s.labels):
            flags.append(_flag_row(LABEL, i, n.x, n.y, n.name, "", (LABEL, i) in sel))
        for i, g in enumerate(s.grounds):
            flags.append(_flag_row(GROUND, i, g.x, g.y, "0", "", (GROUND, i) in sel))
        for i, p in enumerate(s.ports):
            flags.append(_flag_row(PORT, i, p.x, p.y, p.name, p.direction, (PORT, i) in sel))
        self._flags.sync(flags)
        self._wire_path = " ".join(
            f"M {w.x1} {w.y1} L {w.x2} {w.y2}"
            for i, w in enumerate(s.wires)
            if (WIRE, i) not in sel
        )
        self._selected_wire_path = " ".join(
            f"M {w.x1} {w.y1} L {w.x2} {w.y2}" for i, w in enumerate(s.wires) if (WIRE, i) in sel
        )
        self._texts = [
            {
                "index": i,
                "posX": t.x,
                "posY": t.y,
                "text": t.text,
                "directive": t.directive,
                "selected": (TEXT, i) in sel,
            }
            for i, t in enumerate(s.texts)
        ]
        if not live:
            nets = connect(s, self.lookup)
            self._junctions = [{"x": x, "y": y} for x, y in nets.junctions]
            self._port_rows = schematic_ports(s, nets).rows
        self._ports = [
            {
                "index": r.index,
                "name": r.name,
                "direction": r.direction,
                "net": r.net,
                "severity": r.severity,
                "message": r.message,
                "selected": (PORT, r.index) in sel,
            }
            for r in self._port_rows
            if r.index < len(s.ports)
        ]
        self.sheetChanged.emit()

    def _instance_row(self, i: int, inst: Instance, selected: bool) -> dict[str, object]:
        symbol = self.lookup(inst.symbol)
        return {
            "index": i,
            "instName": inst.name,
            "symbolName": inst.symbol,
            "posX": inst.x,
            "posY": inst.y,
            "rotation": inst.rotation,
            "mirror": inst.mirror,
            "matrix": list(orientation_matrix(inst.rotation, inst.mirror)),
            "value": part_value(inst, symbol),
            "texts": instance_texts(inst, symbol),
            "missing": symbol is None,
            "selected": selected,
        }

    @Slot(str, result="QVariantMap")
    def symbolGeometry(self, name: str) -> dict[str, object]:  # noqa: N802
        """Drawing data of a symbol: {path, pins: [{x, y, name}], box: [x1, y1, x2, y2],
        missing}. Cached per symbol."""
        cached = self._geometry_cache.get(name)
        if cached is None:
            symbol = self.lookup(name)
            box = symbol_box(symbol)
            if symbol is None:
                x1, y1, x2, y2 = box
                path = f"M {x1} {y1} L {x2} {y1} L {x2} {y2} L {x1} {y2} Z M {x1} {y1} L {x2} {y2}"
                pins: list[dict[str, object]] = []
            else:
                path = symbol_svg(symbol)
                pins = [{"x": p.x, "y": p.y, "name": p.name} for p in symbol.pins]
            cached = {"path": path, "pins": pins, "box": list(box), "missing": symbol is None}
            self._geometry_cache[name] = cached
        return dict(cached)

    # ---- hit-testing and view helpers

    @Slot(float, float, float, result="QVariantMap")
    def itemAt(self, x: float, y: float, tol: float) -> dict[str, object]:  # noqa: N802
        """{kind, index} of the thing at sheet point (x, y), or {} for nothing."""
        hit = hit_test(self._schematic, self.lookup, x, y, tol)
        return {} if hit is None else {"kind": hit.kind, "index": hit.index}

    @Slot(float, float, float, result="QVariantMap")
    def snapPoint(self, x: float, y: float, tol: float) -> dict[str, object]:  # noqa: N802
        """The grid point nearest (x, y), or a pin within ``tol``: {x, y, pin}."""
        pin = pin_near(self._schematic, self.lookup, x, y, tol)
        if pin is not None:
            return {"x": pin[0], "y": pin[1], "pin": True}
        return {"x": snap(x), "y": snap(y), "pin": False}

    @Slot(result="QVariantList")
    def contentBounds(self) -> list[int]:  # noqa: N802
        """[x1, y1, x2, y2] around everything, or [] for an empty sheet."""
        box = bounds(self._schematic, self.lookup)
        return [] if box is None else list(box)

    # ---- selection

    selectionChanged = Signal()

    def _get_selection_count(self) -> int:
        return len(self._selection)

    selectionCount = Property(int, _get_selection_count, notify=selectionChanged)  # noqa: N815

    def selection(self) -> set[tuple[str, int]]:
        return set(self._selection)

    def _select(self, items: set[tuple[str, int]]) -> None:
        if items != self._selection:
            self._selection = items
            self._refresh(live=True)
            self.selectionChanged.emit()

    @Slot(str, int, bool)
    def select(self, kind: str, index: int, add: bool) -> None:
        """Select one thing; with ``add`` (Ctrl+click) toggle it in the selection."""
        if kind not in KINDS or not 0 <= index < len(items_of(self._schematic, kind)):
            return
        key = (kind, index)
        if add:
            self._select(self._selection ^ {key})
        else:
            self._select({key})

    @Slot(str, int, result=bool)
    def isSelected(self, kind: str, index: int) -> bool:  # noqa: N802
        return (kind, index) in self._selection

    @Slot(float, float, float, float, bool)
    def selectRect(self, x1: float, y1: float, x2: float, y2: float, add: bool) -> None:  # noqa: N802
        """Rubber band: select what lies wholly inside the box."""
        box = (int(math.floor(x1)), int(math.floor(y1)), int(math.ceil(x2)), int(math.ceil(y2)))
        found = {(h.kind, h.index) for h in items_in_rect(self._schematic, self.lookup, box)}
        self._select(self._selection | found if add else found)

    @Slot()
    def selectAll(self) -> None:  # noqa: N802
        s = self._schematic
        self._select({(k, i) for k in KINDS for i in range(len(items_of(s, k)))})

    @Slot()
    def clearSelection(self) -> None:  # noqa: N802
        self._select(set())

    # ---- undo

    undoStateChanged = Signal()
    schematicEdited = Signal()  # an undo step was done, undone or redone; or a load

    def _get_can_undo(self) -> bool:
        return self._undo.canUndo()

    def _get_can_redo(self) -> bool:
        return self._undo.canRedo()

    def _get_undo_text(self) -> str:
        return self._undo.undoText()

    def _get_redo_text(self) -> str:
        return self._undo.redoText()

    canUndo = Property(bool, _get_can_undo, notify=undoStateChanged)  # noqa: N815
    canRedo = Property(bool, _get_can_redo, notify=undoStateChanged)  # noqa: N815
    undoText = Property(str, _get_undo_text, notify=undoStateChanged)  # noqa: N815
    redoText = Property(str, _get_redo_text, notify=undoStateChanged)  # noqa: N815

    @Slot()
    def undo(self) -> None:
        self.cancelLive()
        self._undo.undo()

    @Slot()
    def redo(self) -> None:
        self.cancelLive()
        self._undo.redo()

    def _edit(self, text: str, change: Callable[[Schematic], object]) -> object:
        """Apply ``change`` to a copy of the sheet and push it as one undo step.
        Returns what ``change`` returned; nothing is pushed when the sheet is unchanged."""
        self.cancelLive()
        after = copy.deepcopy(self._schematic)
        result = change(after)
        if after != self._schematic:
            self._undo.push(_Snapshot(self, text, self._schematic, after))
        return result

    def _apply_snapshot(self, schematic: Schematic) -> None:
        self._set(copy.deepcopy(schematic))

    # ---- live sessions (placing, dragging, pasting)

    def _begin_live(self, text: str) -> _Live:
        self.cancelLive()
        self._live = _Live(text, copy.deepcopy(self._schematic))
        self.liveChanged.emit()
        return self._live

    def _live_set(self, schematic: Schematic) -> None:
        self._set(schematic, live=True)

    liveChanged = Signal()

    def _get_live(self) -> bool:
        return self._live is not None

    live = Property(bool, _get_live, notify=liveChanged)

    @Slot()
    def commitLive(self) -> None:  # noqa: N802
        """End the live session; its changes become one undo step."""
        live, self._live = self._live, None
        if live is None:
            return
        self.liveChanged.emit()
        after = copy.deepcopy(self._schematic)
        if after != live.before:
            self._undo.push(_Snapshot(self, live.text, live.before, after))
        else:
            self._refresh()

    @Slot()
    def cancelLive(self) -> None:  # noqa: N802
        """End the live session and put the sheet back as it was before it."""
        live, self._live = self._live, None
        if live is None:
            return
        self.liveChanged.emit()
        self._set(live.before)

    # ---- placing parts (S6)
    #
    # beginPlace(symbol) starts a live session with one floating part; placeMoveTo
    # moves it with the pointer, placeCommit drops it (one undo step) and floats the
    # next one, cancelLive (Esc) removes the floating part. The orientation of the last
    # placed part is kept for the next one, as in LTspice.

    @Slot(result="QStringList")
    def placeableSymbols(self) -> list[str]:  # noqa: N802
        """Symbols the part picker lists: all but ground and label, which have tools."""
        return [n for n in self._symbols.names() if n.casefold() not in NET_FLAG_SYMBOLS]

    @Slot(str, result=str)
    def symbolDescription(self, name: str) -> str:  # noqa: N802
        symbol = self.lookup(name)
        if symbol is None:
            return ""
        prefix = symbol.prefix or "-"
        text = symbol.attr("Description") or name
        return f"{text} (Prefix {prefix}, {len(symbol.pins)} pins)"

    @Slot(str, result=str)
    def nextName(self, symbol_name: str) -> str:  # noqa: N802
        """The next free InstName for a symbol: R1, R2... (X symbols: U1, U2...)."""
        return _next_name(self._name_base(symbol_name), self._schematic.instances)

    def _name_base(self, symbol_name: str) -> str:
        symbol = self.lookup(symbol_name)
        if symbol is None:
            return "U"
        named = symbol.attr("InstName").strip().rstrip("0123456789")
        if named:
            return named
        letter = symbol.prefix[:1].upper()
        return "U" if letter in ("", "X") else letter

    @Slot(str, float, float, result=str)
    def beginPlace(self, symbol_name: str, x: float, y: float) -> str:  # noqa: N802
        """Start placing ``symbol_name`` with the pointer at (x, y); returns its name."""
        if self.lookup(symbol_name) is None:
            return ""
        orientation = (0, False)
        if self._live is not None and self._live.data.get("kind") == "place":
            orientation = self._live.data["orientation"]  # type: ignore[assignment]
        live = self._begin_live("")
        live.data.update(kind="place", symbol=symbol_name, orientation=orientation)
        return self._float_part(live, x, y)

    def _float_part(self, live: _Live, x: float, y: float) -> str:
        symbol_name = str(live.data["symbol"])
        rotation, mirror = live.data["orientation"]  # type: ignore[misc]
        s = self.schematic()
        name = _next_name(self._name_base(symbol_name), s.instances)
        attrs: dict[str, str] = {}
        pending = self._pending
        if pending.get("symbol") == symbol_name:
            attrs = {str(k): str(v) for k, v in dict(pending.get("attrs") or {}).items()}
        inst = Instance(symbol_name, name, 0, 0, rotation, mirror, attrs)
        s.instances.append(inst)
        live.text = f"place {name}"
        live.data["index"] = len(s.instances) - 1
        self._place_at(s, inst, x, y)
        self._live_set(s)
        return name

    def _place_at(self, s: Schematic, inst: Instance, x: float, y: float) -> None:
        """Put the part's box centre near (x, y) with its origin on the grid."""
        x1, y1, x2, y2 = instance_box(
            Instance(inst.symbol, inst.name, 0, 0, inst.rotation, inst.mirror),
            self.lookup(inst.symbol),
        )
        inst.x = snap(x - (x1 + x2) / 2)
        inst.y = snap(y - (y1 + y2) / 2)

    def _floating(self) -> tuple[Schematic, Instance] | None:
        live = self._live
        if live is None or live.data.get("kind") != "place":
            return None
        s = self.schematic()
        return s, s.instances[int(live.data["index"])]  # type: ignore[call-overload]

    @Slot(float, float)
    def placeMoveTo(self, x: float, y: float) -> None:  # noqa: N802
        found = self._floating()
        if found is None:
            return
        s, inst = found
        old = (inst.x, inst.y)
        self._place_at(s, inst, x, y)
        if (inst.x, inst.y) != old:
            self._live_set(s)

    @Slot(float, float, result=str)
    def placeCommit(self, x: float, y: float) -> str:  # noqa: N802
        """Drop the floating part (one undo step) and float the next one; returns the
        next part's name."""
        live = self._live
        if live is None or live.data.get("kind") != "place":
            return ""
        self.placeMoveTo(x, y)
        data = dict(live.data)
        self.commitLive()
        nxt = self._begin_live("")
        nxt.data.update(kind="place", symbol=data["symbol"], orientation=data["orientation"])
        return self._float_part(nxt, x, y)

    def _turn_floating(self, rotate: bool) -> bool:
        found = self._floating()
        if found is None:
            return False
        s, inst = found
        box = instance_box(inst, self.lookup(inst.symbol))
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if rotate:
            inst.rotation, inst.mirror = turn(inst.rotation, inst.mirror, 90, False)
        else:
            inst.rotation, inst.mirror = turn(inst.rotation, inst.mirror, 0, True)
        assert self._live is not None
        self._live.data["orientation"] = (inst.rotation, inst.mirror)
        self._place_at(s, inst, cx, cy)
        self._live_set(s)
        return True

    @Slot(result=bool)
    def placeRotate(self) -> bool:  # noqa: N802
        """Ctrl+R while placing: turn the floating part 90 degrees clockwise."""
        return self._turn_floating(True)

    @Slot(result=bool)
    def placeMirror(self) -> bool:  # noqa: N802
        """Ctrl+E while placing: mirror the floating part left-right."""
        return self._turn_floating(False)

    def _get_placing(self) -> str:
        live = self._live
        if live is None or live.data.get("kind") != "place":
            return ""
        return str(live.data["symbol"])

    placing = Property(str, _get_placing, notify=liveChanged)

    @Slot()
    def stopPlacing(self) -> None:  # noqa: N802
        """Esc while placing: drop the floating part and forget a pending library part."""
        if self._live is not None and self._live.data.get("kind") == "place":
            self.cancelLive()
        if self._pending:
            self._pending = {}
            self.pendingPartChanged.emit()

    # ---- wires, net labels and ground (S7)

    @Slot("QVariantList", result=bool)
    def addWires(self, segments: list) -> bool:  # noqa: N802
        """Add wire segments [[x1, y1, x2, y2], ...] as one undo step ("wire").
        Zero-length segments are skipped; returns False when nothing was added."""
        wires: list[Wire] = []
        for seg in segments:
            try:
                x1, y1, x2, y2 = (int(round(float(v))) for v in seg)
            except (TypeError, ValueError):
                return False
            if (x1, y1) != (x2, y2):
                wires.append(Wire(x1, y1, x2, y2))
        if not wires:
            return False
        self._edit("wire", lambda s: s.wires.extend(wires))
        return True

    @Slot(float, float, result=bool)
    def isConnectionPoint(self, x: float, y: float) -> bool:  # noqa: N802
        """True when (x, y) is a pin, a wire end, a point on a wire or a flag: the wire
        tool stops there."""
        point = (int(round(x)), int(round(y)))
        s = self._schematic
        for inst in s.instances:
            symbol = self.lookup(inst.symbol)
            if symbol is not None and point in pin_points(inst, symbol):
                return True
        for w in s.wires:
            if point in w.ends or on_segment(point, w):
                return True
        flags = [(f.x, f.y) for f in [*s.labels, *s.grounds, *s.ports]]
        return point in flags

    @Slot(float, float, str, result=bool)
    def addLabel(self, x: float, y: float, name: str) -> bool:  # noqa: N802
        """Place a net label (one undo step). The name must be one word."""
        name = name.strip()
        if not name or any(c.isspace() for c in name):
            return False
        point = (int(round(x)), int(round(y)))
        self._edit(f"label {name}", lambda s: s.labels.append(NetLabel(*point, name)))
        return True

    @Slot(float, float, result=bool)
    def addGround(self, x: float, y: float) -> bool:  # noqa: N802
        """Place a ground flag (one undo step)."""
        point = (int(round(x)), int(round(y)))
        self._edit("ground", lambda s: s.grounds.append(GroundFlag(*point)))
        return True

    # ---- port flags (H1)

    @Slot(str, result=str)
    def portNameProblem(self, name: str) -> str:  # noqa: N802
        """Why ``name`` cannot be a port ("" when it can)."""
        return name_problem(name.strip())

    @Slot(float, float, str, str, result=bool)
    def addPort(self, x: float, y: float, name: str, direction: str) -> bool:  # noqa: N802
        """Place a port flag (one undo step). ``direction`` is in, out or bidir."""
        name = name.strip()
        if name_problem(name) or direction not in PORT_DIRECTIONS:
            return False
        point = (int(round(x)), int(round(y)))
        self._edit(f"port {name}", lambda s: s.ports.append(PortFlag(*point, name, direction)))
        return True

    @Slot(int, str, str, result=bool)
    def setPort(self, index: int, name: str, direction: str) -> bool:  # noqa: N802
        """Rename port flag ``index`` and/or change its direction (one undo step)."""
        name = name.strip()
        ports = self._schematic.ports
        if not 0 <= index < len(ports) or name_problem(name) or direction not in PORT_DIRECTIONS:
            return False
        if (ports[index].name, ports[index].direction) == (name, direction):
            return True

        def change(s: Schematic) -> None:
            s.ports[index].name = name
            s.ports[index].direction = direction

        self._edit(f"edit port {name}", change)
        return True

    @Slot(int, result="QVariantMap")
    def portAt(self, index: int) -> dict[str, object]:  # noqa: N802
        ports = self._schematic.ports
        if not 0 <= index < len(ports):
            return {}
        p = ports[index]
        return {"index": index, "name": p.name, "direction": p.direction, "x": p.x, "y": p.y}

    # ---- netlist ports (H1): the user ticks which netlist nodes become pins

    @Slot(str, result="QStringList")
    def netlistNodes(self, text: str) -> list[str]:  # noqa: N802
        """The netlist's top-level nodes that can be ports (ground left out)."""
        nodes = parse(text, self._base_dir(), self.include_folders()).circuit.nodes()
        return [n for n in nodes if n.casefold() not in GROUND_NAMES]

    @Slot(str, "QVariantList", result="QVariantList")
    def checkNetlistPorts(self, text: str, chosen: list) -> list[dict[str, object]]:  # noqa: N802
        """Problems with the ticked ``chosen`` ({node, direction}) ports, as rows of
        {severity, message}; an empty list means they are fine."""
        found = netlist_ports(self.netlistNodes(text), _chosen(chosen))
        if not found.ports and found.ok:
            return [{"severity": ERROR, "message": "tick at least one node as a port"}]
        return [{"severity": d.severity, "message": d.message} for d in found.diagnostics]

    # ---- circuit symbols (H2): the sheet or a netlist becomes <name>.sub + <name>.asy
    #
    # The files go into the project folder, so the project must have been saved. An
    # existing pair is only replaced when ``overwrite`` is true (QML asks first).

    symbolMade = Signal(str, str)  # name, path of the .asy
    projectSymbolsChanged = Signal()

    # H3: the project's circuit symbols are SymbolLibrary PROJECT entries, so the part
    # picker lists them and the netlister turns a placed one into an X line plus the
    # .include of its ModelFile (the .sub beside the .asy). ProjectManager stores them.

    def project_symbols(self) -> list[Path]:
        """The project's circuit symbols (.asy paths), in the order they were made."""
        return list(self._project_symbols)

    def set_project_symbols(self, paths: list[str | Path]) -> None:
        """Use these circuit symbols (opening a project). Set by ProjectManager."""
        symbols: list[Path] = []
        for p in paths:
            path = Path(p)
            if path not in symbols:
                symbols.append(path)
        self._project_symbols = symbols
        self._symbols.set_project_files(symbols)
        self.symbolsChanged.emit()

    def _add_project_symbol(self, asy: Path) -> None:
        if asy not in self._project_symbols:
            self._project_symbols.append(asy)
            self._symbols.set_project_files(self._project_symbols)
            self.projectSymbolsChanged.emit()
        self._symbols.refresh()  # re-read a replaced .asy
        self.symbolsChanged.emit()

    def set_project_file(self, path: str) -> None:
        """The project's .qsp path ("" = not saved yet). Set by ProjectManager."""
        self._project_file = str(path or "")

    def project_folder(self) -> Path | None:
        return Path(self._project_file).parent if self._project_file else None

    @Slot(str, result=str)
    def symbolNameProblem(self, name: str) -> str:  # noqa: N802
        """Why ``name`` cannot be a new circuit symbol ("" when it can)."""
        name = name.strip()
        problem = symbol_name_problem(name)
        if problem:
            return problem
        entry = self._symbols.find(name)
        folder = self.project_folder()
        own = entry is not None and (
            entry.source == PROJECT or (folder is not None and entry.path.parent == folder)
        )
        if entry is not None and not own:
            return f"there is already a symbol called {entry.key} ({entry.source})"
        return ""

    @Slot(str, bool, result="QVariantMap")
    def makeSymbol(self, name: str, overwrite: bool) -> dict[str, object]:  # noqa: N802
        """Make a circuit symbol from the sheet; its port flags are the pins."""
        self.cancelLive()
        name = name.strip()
        return self._make_symbol(
            name,
            overwrite,
            lambda folder: SymbolGenerator.from_schematic(
                name, self._schematic, self.lookup, self._base_dir() or folder
            ),
        )

    @Slot(str, str, "QVariantList", bool, result="QVariantMap")
    def makeSymbolFromNetlist(  # noqa: N802
        self, text: str, name: str, ports: list, overwrite: bool
    ) -> dict[str, object]:
        """Make a circuit symbol from a netlist; ``ports`` are {node, direction} rows."""
        name = name.strip()
        chosen = _chosen(ports)
        return self._make_symbol(
            name,
            overwrite,
            lambda folder: SymbolGenerator.from_netlist(
                name, text, chosen, self._base_dir() or folder
            ),
        )

    def _make_symbol(
        self, name: str, overwrite: bool, generate: Callable[[Path], GeneratedSymbol]
    ) -> dict[str, object]:
        def fail(message: str, **extra: object) -> dict[str, object]:
            return {"ok": False, "message": message, **extra}

        folder = self.project_folder()
        if folder is None:
            return fail(
                "Save the project first: the symbol files go into the project folder",
                needsSave=True,
            )
        problem = self.symbolNameProblem(name)
        if problem:
            return fail(problem)
        made = generate(folder)
        errors = [d.message for d in made.diagnostics if d.severity == ERROR]
        warnings = [d.message for d in made.diagnostics if d.severity != ERROR]
        if not made.ok:
            return fail("; ".join(errors) or "the symbol could not be made", warnings=warnings)
        sub, asy = made.paths(folder)
        if (sub.exists() or asy.exists()) and not overwrite:
            return fail(f"{asy.name} and {sub.name} already exist in {folder}", exists=True)
        written = made.write(folder)
        if written:
            return fail("; ".join(d.message for d in written))
        self._add_project_symbol(asy)
        self.symbolMade.emit(name, str(asy))
        return {
            "ok": True,
            "message": f"Made {asy.name} and {sub.name} in {folder}",
            "asy": str(asy),
            "sub": str(sub),
            "pins": [p.name for p in made.ports],
            "warnings": warnings,
        }

    # ---- editing the selection (S8)
    #
    # Ctrl+R / Ctrl+E turn the selection (or the part being placed), Delete removes it.
    # beginMove / moveTo / endMove is one live session: Drag (follow=True, the default
    # for a mouse drag) keeps wires on the pins that move, Move (follow=False) detaches
    # them. Copy keeps the selection on the controller's own clipboard; beginPaste
    # floats a copy that moveTo moves and endMove drops (one "paste" step).

    @Slot(result=bool)
    def rotateSelection(self) -> bool:  # noqa: N802
        if self._get_placing():
            return self.placeRotate()
        if not self._selection:
            return False
        sel = set(self._selection)
        self._edit("rotate", lambda s: _replace(s, edit_ops.rotate(s, self.lookup, sel)))
        return True

    @Slot(result=bool)
    def mirrorSelection(self) -> bool:  # noqa: N802
        if self._get_placing():
            return self.placeMirror()
        if not self._selection:
            return False
        sel = set(self._selection)
        self._edit("mirror", lambda s: _replace(s, edit_ops.mirror(s, self.lookup, sel)))
        return True

    @Slot(result=bool)
    def deleteSelection(self) -> bool:  # noqa: N802
        if not self._selection or self._live is not None:
            return False
        sel = set(self._selection)
        self._selection = set()
        self._edit("delete", lambda s: _replace(s, edit_ops.delete(s, sel)))
        self.selectionChanged.emit()
        return True

    @Slot(float, float, bool, result=bool)
    def beginMove(self, x: float, y: float, follow: bool) -> bool:  # noqa: N802
        """Start moving the selection with the pointer at (x, y)."""
        if not self._selection:
            return False
        live = self._begin_live("drag" if follow else "move")
        live.data.update(
            kind="move", base=self.schematic(), start=(snap(x), snap(y)), follow=follow
        )
        return True

    @Slot(float, float)
    def moveTo(self, x: float, y: float) -> None:  # noqa: N802
        live = self._live
        if live is None or live.data.get("kind") != "move":
            return
        sx, sy = live.data["start"]  # type: ignore[misc]
        delta = (snap(x) - sx, snap(y) - sy)
        if delta == live.data.get("delta"):
            return
        live.data["delta"] = delta
        base: Schematic = live.data["base"]  # type: ignore[assignment]
        moved = edit_ops.move(base, self.lookup, self._selection, *delta, bool(live.data["follow"]))
        self._live_set(moved)

    @Slot()
    def endMove(self) -> None:  # noqa: N802
        live = self._live
        if live is not None and live.data.get("kind") == "move":
            self.commitLive()

    def _get_moving(self) -> bool:
        return self._live is not None and self._live.data.get("kind") == "move"

    moving = Property(bool, _get_moving, notify=liveChanged)

    @Slot(result=bool)
    def copySelection(self) -> bool:  # noqa: N802
        if not self._selection:
            return False
        self._clipboard = edit_ops.copy_selection(self._schematic, self._selection)
        self.clipboardChanged.emit()
        return True

    clipboardChanged = Signal()

    def _get_can_paste(self) -> bool:
        return self._clipboard is not None

    canPaste = Property(bool, _get_can_paste, notify=clipboardChanged)  # noqa: N815

    @Slot(float, float, result=bool)
    def beginPaste(self, x: float, y: float) -> bool:  # noqa: N802
        """Float a copy of the clipboard at the pointer; moveTo moves it, endMove drops
        it, cancelLive (Esc) takes it away."""
        clip = self._clipboard
        if clip is None:
            return False
        self.cancelLive()
        c = edit_ops.centre(clip, self.lookup, edit_ops.all_items(clip)) or (0, 0)
        dx, dy = snap(x) - c[0], snap(y) - c[1]
        pasted, selection = edit_ops.paste(self._schematic, clip, dx, dy)
        live = self._begin_live("paste")
        self._selection = selection
        live.data.update(kind="move", base=pasted, start=(snap(x), snap(y)), follow=False)
        self._live_set(copy.deepcopy(pasted))
        self.selectionChanged.emit()
        return True

    # ---- attributes, label names and sheet texts (S9)
    #
    # The attribute dialog shows a part's effective values (its own, else the symbol's
    # SYMATTR default). Saving stores only values that differ from the symbol, so a
    # part keeps following its symbol for the rest. Every change is one undo step.

    @Slot(int, result="QVariantMap")
    def partAttributes(self, index: int) -> dict[str, object]:  # noqa: N802
        """{index, name, symbol, prefix, simple, description, fields: [{key, value,
        default}]} for the attribute dialog; {} for a bad index."""
        if not 0 <= index < len(self._schematic.instances):
            return {}
        inst = self._schematic.instances[index]
        symbol = self.lookup(inst.symbol)
        prefix = symbol.prefix if symbol is not None else ""
        letter = prefix[:1].upper()
        keys = SIMPLE_KEYS.get(letter, ATTRIBUTE_KEYS)
        fields = []
        for key in keys:
            default = symbol.attr(key) if symbol is not None else ""
            own = next((v for k, v in inst.attrs.items() if k.casefold() == key.casefold()), None)
            fields.append(
                {"key": key, "value": default if own is None else own, "default": default}
            )
        return {
            "index": index,
            "name": inst.name,
            "symbol": inst.symbol,
            "prefix": prefix,
            "simple": letter in SIMPLE_KEYS,
            "description": self.symbolDescription(inst.symbol),
            "fields": fields,
        }

    @Slot(int, str, "QVariantMap", result=str)
    def checkAttributes(self, index: int, name: str, values: dict) -> str:  # noqa: N802
        """ "" when ``name`` and ``values`` make a valid part, else the first problem."""
        made = self._attributed(index, name, values)
        return made if isinstance(made, str) else ""

    @Slot(int, str, "QVariantMap", result=str)
    def setAttributes(self, index: int, name: str, values: dict) -> str:  # noqa: N802
        """Apply the attribute dialog (one undo step); returns "" or the problem."""
        made = self._attributed(index, name, values)
        if isinstance(made, str):
            return made
        old = self._schematic.instances[index].name

        def change(s: Schematic) -> None:
            s.instances[index] = made

        self._edit(f"edit {old}", change)
        return ""

    def _attributed(self, index: int, name: str, values: dict) -> Instance | str:
        if not 0 <= index < len(self._schematic.instances):
            return "no such part"
        inst = self._schematic.instances[index]
        name = name.strip()
        if not name or any(c.isspace() for c in name):
            return "the name must be one word"
        if any(
            i != index and other.name.casefold() == name.casefold()
            for i, other in enumerate(self._schematic.instances)
        ):
            return f"another part is already called {name}"
        symbol = self.lookup(inst.symbol)
        attrs = {k: v for k, v in inst.attrs.items() if k.casefold() not in _ATTRIBUTE_FOLDED}
        for key in ATTRIBUTE_KEYS:
            match = next(
                (v for k, v in values.items() if str(k).casefold() == key.casefold()), None
            )
            if match is None:
                own = next(
                    (v for k, v in inst.attrs.items() if k.casefold() == key.casefold()), None
                )
                if own is not None:
                    attrs[key] = own
                continue
            value = str(match).strip()
            default = symbol.attr(key) if symbol is not None else ""
            if value != default:
                attrs[key] = value
        made = Instance(inst.symbol, name, inst.x, inst.y, inst.rotation, inst.mirror, attrs)
        if symbol is not None and symbol.prefix:
            nets = [f"n{k + 1}" for k in range(len(symbol.pins))]
            part = netlist_part(PartInstance(name, symbol, nets, attrs))
            errors = [d.message for d in part.diagnostics if d.severity == ERROR]
            if errors:
                return errors[0].removeprefix(f"{name}: ")
        return made

    @Slot(int, str, result=bool)
    def renameLabel(self, index: int, name: str) -> bool:  # noqa: N802
        """Give net label ``index`` a new one-word name (one undo step)."""
        name = name.strip()
        if (
            not 0 <= index < len(self._schematic.labels)
            or not name
            or any(c.isspace() for c in name)
        ):
            return False

        def change(s: Schematic) -> None:
            s.labels[index].name = name

        self._edit(f"rename label {name}", change)
        return True

    @Slot(float, float, str, bool, result=bool)
    def addText(self, x: float, y: float, text: str, directive: bool) -> bool:  # noqa: N802
        """Place a SPICE directive (``directive``) or a comment (one undo step)."""
        text = text.strip()
        if not text:
            return False
        point = (int(round(x)), int(round(y)))
        what = "directive" if directive else "comment"
        self._edit(f"add {what}", lambda s: s.texts.append(TextItem(*point, text, directive)))
        return True

    @Slot(int, str, bool, result=bool)
    def setText(self, index: int, text: str, directive: bool) -> bool:  # noqa: N802
        """Change sheet text ``index`` (one undo step)."""
        text = text.strip()
        if not 0 <= index < len(self._schematic.texts) or not text:
            return False

        def change(s: Schematic) -> None:
            s.texts[index].text = text
            s.texts[index].directive = directive

        self._edit("edit text", change)
        return True

    @Slot(int, result="QVariantMap")
    def textAt(self, index: int) -> dict[str, object]:  # noqa: N802
        if not 0 <= index < len(self._schematic.texts):
            return {}
        t = self._schematic.texts[index]
        return {"index": index, "text": t.text, "directive": t.directive}

    @Slot(int, result=str)
    def labelName(self, index: int) -> str:  # noqa: N802
        labels = self._schematic.labels
        return labels[index].name if 0 <= index < len(labels) else ""

    # ---- check and run (S10)
    #
    # check() netlists the sheet (S3) and lists the problems; run() does the same and,
    # without errors, hands the netlist text to the existing Simulator (rule 8: there is
    # no second simulation path). The Simulator is attached once by Main.qml. The
    # sheet that ran is kept (``ran``) so probes (S11) know its nets.

    problemsChanged = Signal()
    ranChanged = Signal()

    @Slot(QObject)
    def attachSimulator(self, simulator: QObject) -> None:  # noqa: N802
        self._simulator = simulator
        simulator.resultReady.connect(self._on_sim_result)  # type: ignore[attr-defined]
        self._push_include_folders()

    def _get_problems(self) -> list[dict[str, object]]:
        return list(self._problems)

    problems = Property("QVariantList", _get_problems, notify=problemsChanged)

    def _base_dir(self) -> Path | None:
        sim = self._simulator
        path = str(sim.property("filePath") or "") if sim is not None else ""
        return Path(path).parent if path else None

    def _netlist(self) -> NetlistedSchematic:
        result = netlist_schematic(
            self._schematic, self.lookup, self._base_dir(), self.include_folders()
        )
        self._problems = [
            {"severity": d.severity, "message": d.message} for d in result.diagnostics
        ]
        self.problemsChanged.emit()
        return result

    @Slot(result=bool)
    def check(self) -> bool:
        """Netlist the sheet and list its problems; True when there are no errors."""
        self.cancelLive()
        return self._netlist().ok

    @Slot(result=bool)
    def run(self) -> bool:
        """Netlist the sheet and start the Simulator on it; False if it did not start
        (the reason is in ``problems`` or the Simulator's ``statusText``)."""
        self.cancelLive()
        result = self._netlist()
        sim = self._simulator
        if sim is None:
            return False
        if not result.ok:
            errors = sum(1 for p in self._problems if p["severity"] == ERROR)
            sim.set_status_text(f"Not started: the schematic has {errors} error(s)")  # type: ignore[attr-defined]
            return False
        started = bool(
            sim.run(result.text, publish=False, extra_cards=RUN_CARDS)  # type: ignore[attr-defined]
        )
        if started:
            self._ran = _Ran(self.schematic(), result.nets, self._undo.index())
            self._awaiting = True
            self.ranChanged.emit()
        return started

    # ---- probing (S11)
    #
    # After a schematic run the canvas probes: a wire gives V(net), a two-terminal
    # part's body its current, a pin the current into that pin, a drag from net to net
    # V(a,b), Alt+click a part its power. Probes need the result of *this* sheet:
    # ``probeReady`` is false once the sheet is edited, or when another run (the
    # netlist editor's) replaced the result. Probed traces stay probed for the next
    # schematic run; all other traces start hidden, as in LTspice.

    probeChanged = Signal()
    probed = Signal(str)  # the name of the trace that was added or shown

    @Slot(QObject)
    def attachWaveforms(self, model: QObject) -> None:  # noqa: N802
        self._waveforms = model

    def _on_sim_result(self) -> None:
        sim = self._simulator
        if not self._awaiting or sim is None:
            self.probeChanged.emit()
            return
        self._awaiting = False
        self._probe_result = sim.result  # type: ignore[attr-defined]
        model = self._waveforms
        if model is not None and self._probe_result is not None and self._probe_result.axis:
            model.show_only([])  # type: ignore[attr-defined]
            for p in list(self._probed):
                self._show(p, remember=False)
        self.probeChanged.emit()

    def _get_probe_ready(self) -> bool:
        sim = self._simulator
        ran = self._ran
        result = self._probe_result
        return (
            sim is not None
            and ran is not None
            and result is not None
            and sim.result is result  # type: ignore[attr-defined]
            and ran.schematic == self._schematic
        )

    def _get_probe_message(self) -> str:
        return self._probe_message

    probeReady = Property(bool, _get_probe_ready, notify=probeChanged)  # noqa: N815
    probeMessage = Property(str, _get_probe_message, notify=probeChanged)  # noqa: N815

    def _say(self, text: str) -> str:
        self._probe_message = text
        self.probeChanged.emit()
        return text

    def _pin_owner(self, x: float, y: float, tol: float) -> tuple[int, int] | None:
        assert self._ran is not None
        best: tuple[float, int, int] | None = None
        for i, inst in enumerate(self._ran.schematic.instances):
            symbol = self.lookup(inst.symbol)
            if symbol is None:
                continue
            for k, (px, py) in enumerate(pin_points(inst, symbol)):
                d = math.hypot(px - x, py - y)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, i, k)
        return None if best is None else (best[1], best[2])

    def _net_at(self, x: float, y: float, tol: float) -> str:
        """The net under (x, y) in the sheet that ran: a pin, a wire or a flag."""
        ran = self._ran
        if ran is None:
            return ""
        owner = self._pin_owner(x, y, tol)
        if owner is not None:
            nets = ran.nets.pins[owner[0]]
            return nets[owner[1]] if owner[1] < len(nets) else ""
        hit = hit_test(ran.schematic, self.lookup, x, y, tol)
        if hit is None:
            return ""
        if hit.kind == WIRE:
            return ran.nets.wires[hit.index]
        if hit.kind == GROUND:
            return "0"
        if hit.kind in (LABEL, PORT):
            flag = items_of(ran.schematic, hit.kind)[hit.index]
            point = (flag.x, flag.y)
            for i, w in enumerate(ran.schematic.wires):
                if point in w.ends or on_segment(point, w):
                    return ran.nets.wires[i]
            for i, inst in enumerate(ran.schematic.instances):
                symbol = self.lookup(inst.symbol)
                if symbol is not None and point in pin_points(inst, symbol):
                    return ran.nets.pins[i][pin_points(inst, symbol).index(point)]
            return flag.name
        return ""

    def _target(self, x: float, y: float, tol: float, alt: bool) -> tuple[str, Probe | None]:
        """(badge, probe) for a click at (x, y): badge is V, I, P or ""."""
        ran = self._ran
        if ran is None or not self._get_probe_ready():
            return "", None
        s = ran.schematic
        hit = hit_test(s, self.lookup, x, y, tol)
        if alt:
            if hit is not None and hit.kind == INSTANCE:
                inst = s.instances[hit.index]
                symbol = self.lookup(inst.symbol)
                if symbol is not None:
                    return "P", probe_ops.part_power(inst, symbol, ran.nets.pins[hit.index])
            return "", None
        owner = self._pin_owner(x, y, tol)
        if owner is not None:
            inst = s.instances[owner[0]]
            symbol = self.lookup(inst.symbol)
            if symbol is not None and symbol.prefix:
                return "I", probe_ops.pin_current(inst, symbol, owner[1])
        if hit is None or hit.kind == TEXT:
            return "", None
        if hit.kind == INSTANCE:
            inst = s.instances[hit.index]
            symbol = self.lookup(inst.symbol)
            if symbol is None or not symbol.prefix:
                return "", None
            return "I", probe_ops.part_current(inst, symbol)
        return "V", probe_ops.net_voltage(self._net_at(x, y, tol))

    @Slot(float, float, float, bool, result=str)
    def probeKind(self, x: float, y: float, tol: float, alt: bool) -> str:  # noqa: N802
        """What a click here would probe: "V", "I", "P" or "" (for the pointer badge)."""
        return self._target(x, y, tol, alt)[0]

    @Slot(float, float, float, result=str)
    def probeNet(self, x: float, y: float, tol: float) -> str:  # noqa: N802
        """The net under (x, y), for dragging V(a,b); "" when there is none."""
        return self._net_at(x, y, tol) if self._get_probe_ready() else ""

    @Slot(float, float, float, bool, result=str)
    def probeClick(self, x: float, y: float, tol: float, alt: bool) -> str:  # noqa: N802
        """Probe what is at (x, y). Returns the trace name, or a message why not."""
        _, found = self._target(x, y, tol, alt)
        if found is None:
            return self._say("")
        return self._show(found)

    @Slot(float, float, float, float, float, result=str)
    def probeBetween(self, x1: float, y1: float, x2: float, y2: float, tol: float) -> str:  # noqa: N802
        """A drag from one net to another: V(a,b)."""
        if not self._get_probe_ready():
            return self._say("")
        a, b = self._net_at(x1, y1, tol), self._net_at(x2, y2, tol)
        return self._show(probe_ops.voltage_between(a, b))

    def _show(self, found: Probe, remember: bool = True) -> str:
        if not found.ok:
            return self._say(found.error)
        result = self._probe_result
        if result is None:
            return self._say("")
        empty = [
            f
            for _, factors in found.terms
            for f in factors
            if f in result.vectors and np.asarray(result.vectors[f]).size == 0
        ]
        if empty:
            return self._say(
                f"{found.name}: ngspice does not save {empty[0]} in a .{result.analysis} analysis"
            )
        data = probe_ops.evaluate(found, result.vectors)
        if isinstance(data, str):
            return self._say(f"{found.name}: the result has no {data}")
        if not result.axis:  # .op: one value each
            value = complex(np.ravel(data)[0])
            shown = value.real if value.imag == 0 else abs(value)
            return self._say(f"{found.name} = {si(shown, found.unit)}")
        model = self._waveforms
        if model is not None:
            vector = found.vector
            if vector is not None and vector in model.property("traceNames"):
                model.setVisible(vector, True)  # type: ignore[attr-defined]
            else:
                model.add_trace(found.name, found.unit, data)  # type: ignore[attr-defined]
        if remember and all(p.name != found.name for p in self._probed):
            self._probed.append(found)
        self._say(found.name)
        self.probed.emit(found.name)
        return found.name

    # ---- settings

    def _read_list(self, key: str) -> list[str]:
        try:
            value = json.loads(str(self._settings.value(key, "")))
        except ValueError:
            return []
        return [str(v) for v in value] if isinstance(value, list) else []

    def _write(self, key: str, value: str) -> None:
        self._settings.setValue(key, value)
        self._settings.sync()


# ---------------------------------------------------------------- editor models

KINDS = (INSTANCE, WIRE, LABEL, GROUND, PORT, TEXT)
# The attribute dialog's fields; simple parts (by device letter) show only some.
ATTRIBUTE_KEYS = ("Value", "Value2", "SpiceModel", "ModelFile", "SpiceLine", "SpiceLine2")
SIMPLE_KEYS: dict[str, tuple[str, ...]] = {
    "R": ("Value",),
    "C": ("Value",),
    "L": ("Value",),
    "V": ("Value", "Value2"),
    "I": ("Value", "Value2"),
}
_ATTRIBUTE_FOLDED = frozenset(k.casefold() for k in ATTRIBUTE_KEYS)
INSTANCE_ROLES = [
    "index",
    "instName",
    "symbolName",
    "posX",
    "posY",
    "rotation",
    "mirror",
    "matrix",
    "value",
    "texts",
    "missing",
    "selected",
]
WIRE_ROLES = ["index", "x1", "y1", "x2", "y2", "selected"]
FLAG_ROLES = ["kind", "index", "posX", "posY", "name", "direction", "selected"]


class SyncModel(RowModel):
    """A RowModel that can take a whole new list of rows and change only what differs,
    so QML keeps the delegates of rows that stay the same."""

    def sync(self, rows: list[dict[str, object]]) -> None:
        old = self.rows()
        start = 0
        while start < len(old) and start < len(rows) and old[start] == rows[start]:
            start += 1
        end_old, end_new = len(old), len(rows)
        while end_old > start and end_new > start and old[end_old - 1] == rows[end_new - 1]:
            end_old -= 1
            end_new -= 1
        # Rows start..end_old (old) became start..end_new (new): update the common part
        # in place, then insert or remove the rest.
        common = min(end_old, end_new) - start
        for k in range(common):
            self.update(start + k, **rows[start + k])
        if end_new > end_old:
            first, last = start + common, end_new - 1
            self.beginInsertRows(QModelIndex(), first, last)
            old[first:first] = rows[first : last + 1]
            self.endInsertRows()
            self.countChanged.emit()
        elif end_old > end_new:
            first, last = start + common, end_old - 1
            self.beginRemoveRows(QModelIndex(), first, last)
            del old[first : last + 1]
            self.endRemoveRows()
            self.countChanged.emit()


def _flag_row(
    kind: str, index: int, x: int, y: int, name: str, direction: str, selected: bool
) -> dict[str, object]:
    return {
        "kind": kind,
        "index": index,
        "posX": x,
        "posY": y,
        "name": name,
        "direction": direction,
        "selected": selected,
    }


# Cards added to every schematic run, just before .end.
RUN_CARDS: tuple[str, ...] = (".options savecurrents",)  # part currents for probes


@dataclass
class _Ran:
    """The sheet as it was when it last ran, with its nets (for probing)."""

    schematic: Schematic
    nets: NetMap
    undo_index: int


@dataclass
class _Live:
    text: str
    before: Schematic
    data: dict[str, object] = field(default_factory=dict)


class _Snapshot(QUndoCommand):
    """One edit: the sheet before and after.

    The QUndoStack (a child of the controller) owns the commands, so a command keeps
    only a weak reference to the controller (as in DiagramController).
    """

    def __init__(
        self, ctl: SchematicController, text: str, before: Schematic, after: Schematic
    ) -> None:
        super().__init__(text)
        self._ctl = weakref.ref(ctl)
        self.before = before
        self.after = after

    def redo(self) -> None:
        ctl = self._ctl()
        if ctl is not None:
            ctl._apply_snapshot(self.after)

    def undo(self) -> None:
        ctl = self._ctl()
        if ctl is not None:
            ctl._apply_snapshot(self.before)


def _replace(target: Schematic, source: Schematic) -> None:
    """Make ``target`` (the copy an _edit change works on) equal to ``source``."""
    for name in ("title", "instances", "wires", "labels", "grounds", "ports", "texts"):
        setattr(target, name, getattr(source, name))


def _chosen(rows: list) -> list[tuple[str, str]]:
    """(node, direction) pairs from QML rows of {node, direction}."""
    out: list[tuple[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append((str(row.get("node", "")), str(row.get("direction", "bidir"))))
    return out


def _next_name(base: str, instances: list[Instance]) -> str:
    """``base`` + the lowest number no instance uses yet (names compare case-insensitively)."""
    used = {i.name.casefold() for i in instances}
    n = 1
    while f"{base}{n}".casefold() in used:
        n += 1
    return f"{base}{n}"


def _clean(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        p = str(p).strip()
        if p and p not in out:
            out.append(p)
    return out


def _local(folder: str) -> str:
    """A native path from a path or file:// URL ("" stays "")."""
    folder = folder.strip()
    if folder.startswith("file:"):
        folder = QUrl(folder).toLocalFile()
    return str(Path(folder)) if folder else ""


def _entry(
    kind: str,
    name: str,
    type_: str,
    pins: list[str],
    file: Path,
    line: int,
    section: str | None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "name": name,
        "type": type_,
        "pins": pins,
        "file": str(file),
        "fileName": file.name,
        "line": line,
        "section": section or "",
    }
