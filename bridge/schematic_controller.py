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

Search paths are per user, not per project, and live in QSettings (as JSON text, like
ProjectManager's lists). Every folder is read-only: nothing here writes to it.
Changing a path rescans what it affects and emits ``symbolsChanged`` or
``libraryChanged``, so the browser's lists refresh.
"""

from __future__ import annotations

import copy
import json
import math
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Property, QModelIndex, QObject, QSettings, QUrl, Signal, Slot
from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtQml import QmlElement, QmlSingleton

from bridge.diagram_controller import RowModel
from core.netlist.libindex import LIBRARY_SUFFIXES, LibraryIndex
from core.schematic.connectivity import connect
from core.schematic.geometry import (
    GROUND,
    INSTANCE,
    LABEL,
    PORT,
    WIRE,
    bounds,
    hit_test,
    instance_texts,
    items_in_rect,
    items_of,
    orientation_matrix,
    part_value,
    pin_near,
    snap,
    symbol_box,
    symbol_svg,
)
from core.schematic.model import Instance, Schematic
from core.symbols.asy import Symbol
from core.symbols.library import SymbolLibrary

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

SYMBOL_PATHS_KEY = "library/symbolPaths"
LIBRARY_PATHS_KEY = "library/libraryPaths"
LTSPICE_FOLDER_KEY = "library/ltspiceFolder"

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
        self._symbols = SymbolLibrary(self._symbol_paths, self._ltspice or None)
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
        self._symbols.set_paths(self._symbol_paths, self._ltspice or None)
        self._symbols.refresh()
        self.symbolsChanged.emit()

    # ---- library index

    def index(self) -> LibraryIndex:
        if self._index is None:
            index = LibraryIndex.scan(self._library_paths)
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
        self._selection: set[tuple[str, int]] = set()
        self._symbol_cache: dict[str, Symbol | None] = {}
        self._geometry_cache: dict[str, dict[str, object]] = {}
        self._instances = SyncModel(INSTANCE_ROLES, self)
        self._wires = SyncModel(WIRE_ROLES, self)
        self._flags = SyncModel(FLAG_ROLES, self)
        self._junctions: list[dict[str, int]] = []
        self._texts: list[dict[str, object]] = []
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
        self.symbolsChanged.connect(self._symbols_rescanned)

    # ---- Python side

    def schematic(self) -> Schematic:
        """A copy of the sheet as it is now."""
        return copy.deepcopy(self._schematic)

    def schematic_or_none(self) -> Schematic | None:
        """The sheet for saving: None when it is empty (projects without one stay so)."""
        s = self._schematic
        empty = not (
            s.title or s.instances or s.wires or s.labels or s.grounds or s.ports or s.texts
        )
        return None if empty else self.schematic()

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

    def _get_selected_wire_path(self) -> str:
        return self._selected_wire_path

    instances = Property(QObject, _get_instances, constant=True)
    wires = Property(QObject, _get_wires, constant=True)
    flags = Property(QObject, _get_flags, constant=True)
    junctions = Property("QVariantList", _get_junctions, notify=sheetChanged)
    texts = Property("QVariantList", _get_texts, notify=sheetChanged)
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
            {"posX": t.x, "posY": t.y, "text": t.text, "directive": t.directive} for t in s.texts
        ]
        if not live:
            nets = connect(s, self.lookup)
            self._junctions = [{"x": x, "y": y} for x, y in nets.junctions]
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

KINDS = (INSTANCE, WIRE, LABEL, GROUND, PORT)
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
