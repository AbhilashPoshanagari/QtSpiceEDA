"""Simulator: the QML-facing object for netlist checking (and, from Phase 3, running).

QML uses it as ``import QtSpice`` / ``Simulator.check(text)``.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement, QmlSingleton
from PySide6.QtQuick import QQuickTextDocument

from bridge.highlighter import NetlistHighlighter
from core.netlist.builder import build
from core.netlist.model import ERROR, Diagnostic
from core.netlist.parser import parse
from core.netlist.validate import validate

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

NETLIST_SUFFIXES = (".cir", ".net", ".sp")


def _local_path(url: QUrl | str) -> Path:
    qurl = url if isinstance(url, QUrl) else QUrl(url)
    if qurl.isLocalFile():
        return Path(qurl.toLocalFile())
    return Path(str(url))


@QmlElement
@QmlSingleton
class Simulator(QObject):
    """Checks netlists. ``diagnostics`` is a list of {line, col, severity, message}.

    ``deck`` is the clean SPICE deck of the last check, or "" if it had errors.
    ``filePath`` is the netlist file last opened; relative ``.include`` paths are
    resolved against its folder.
    """

    diagnosticsChanged = Signal()
    deckChanged = Signal()
    filePathChanged = Signal()
    errorStringChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._diagnostics: list[dict[str, object]] = []
        self._deck = ""
        self._file_path = ""
        self._error_string = ""
        self._highlighters: list[NetlistHighlighter] = []

    # ---- properties

    def _get_diagnostics(self) -> list[dict[str, object]]:
        return self._diagnostics

    def _get_deck(self) -> str:
        return self._deck

    def _get_file_path(self) -> str:
        return self._file_path

    def _get_error_string(self) -> str:
        return self._error_string

    diagnostics = Property("QVariantList", _get_diagnostics, notify=diagnosticsChanged)
    deck = Property(str, _get_deck, notify=deckChanged)
    filePath = Property(str, _get_file_path, notify=filePathChanged)  # noqa: N815
    errorString = Property(str, _get_error_string, notify=errorStringChanged)  # noqa: N815

    def _set_error_string(self, text: str) -> None:
        if text != self._error_string:
            self._error_string = text
            self.errorStringChanged.emit()

    # ---- slots

    @Slot(str, result=bool)
    def check(self, text: str) -> bool:
        """Parse and validate ``text``. Returns True if there are no errors."""
        base_dir = Path(self._file_path).parent if self._file_path else None
        result = parse(text, base_dir)
        found: list[Diagnostic] = sorted(
            result.diagnostics + validate(result.circuit), key=lambda d: (d.line, d.col)
        )
        ok = not any(d.severity == ERROR for d in found)
        self._diagnostics = [asdict(d) for d in found]
        self.diagnosticsChanged.emit()
        deck = build(result.circuit) if ok else ""
        if deck != self._deck:
            self._deck = deck
            self.deckChanged.emit()
        return ok

    @Slot("QVariant", result=str)
    def openFile(self, url: QUrl | str) -> str:  # noqa: N802 - QML API
        """Read a netlist file and remember its folder. Returns "" and sets errorString
        if it cannot be read."""
        path = _local_path(url)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._set_error_string(f"Cannot open {path.name}: {exc.strerror or exc}")
            return ""
        self._set_error_string("")
        resolved = str(path.resolve())
        if resolved != self._file_path:
            self._file_path = resolved
            self.filePathChanged.emit()
        return text

    @Slot("QVariant", result=bool)
    def isNetlistFile(self, url: QUrl | str) -> bool:  # noqa: N802 - QML API
        return _local_path(url).suffix.lower() in NETLIST_SUFFIXES

    @Slot(QObject)
    def attachHighlighter(self, document: QObject) -> None:  # noqa: N802 - QML API
        """Highlight a QML TextEdit/TextArea's ``textDocument`` as a SPICE netlist."""
        if isinstance(document, QQuickTextDocument):
            text_document = document.textDocument()
            self._highlighters.append(NetlistHighlighter(text_document))
