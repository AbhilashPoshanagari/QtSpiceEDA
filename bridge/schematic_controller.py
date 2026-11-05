"""SchematicController: the QML face of the schematic editor.

So far (L8) only its library side: symbol and library search paths, the symbol list,
searching models and subcircuits, and "Use with symbol..." which pairs a library entry
with a symbol and fills SpiceModel/ModelFile. The result is kept as ``pendingPart``
for the part picker of the schematic editor (S tasks) to place.

Search paths are per user, not per project, and live in QSettings (as JSON text, like
ProjectManager's lists). Every folder is read-only: nothing here writes to it.
Changing a path rescans what it affects and emits ``symbolsChanged`` or
``libraryChanged``, so the browser's lists refresh.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Property, QObject, QSettings, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement, QmlSingleton

from core.netlist.libindex import LIBRARY_SUFFIXES, LibraryIndex
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

    def __init__(self, parent: QObject | None = None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings()
        self._symbol_paths = self._read_list(SYMBOL_PATHS_KEY)
        self._library_paths = self._read_list(LIBRARY_PATHS_KEY)
        self._ltspice = str(self._settings.value(LTSPICE_FOLDER_KEY, "") or "")
        self._symbols = SymbolLibrary(self._symbol_paths, self._ltspice or None)
        self._index: LibraryIndex | None = None
        self._pending: dict[str, object] = {}

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
