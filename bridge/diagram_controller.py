"""DiagramController: the QML-facing object for function blocks (first version).

QML uses it as ``import QtSpice`` / ``DiagramController.apply(code)``. In this version
it owns the block library: the built-in blocks plus the blocks written in the function
editor. The canvas (placing, wiring and running blocks) comes in a later phase.

The function editor edits one *document* at a time (``document``). ``apply(code)``
reads the code with the registry (which never executes it). Without errors the
document's blocks replace its previous ones and ``library`` updates; with errors the
library keeps the last good version. ``diagnostics`` is a list of
{line, col, severity, message}; ``removedPorts`` lists the ports that disappeared in
the last successful apply, as "block: input a".
"""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement, QmlSingleton

from core.blocks.library import builtin_specs
from core.blocks.registry import BlockRegistry, SourceResult
from core.blocks.spec import BlockSpec
from core.netlist.model import ERROR

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1


def spec_to_dict(spec: BlockSpec, builtin: bool) -> dict[str, object]:
    """A library entry as QML sees it."""
    return {
        "name": spec.name,
        "shape": spec.shape,
        "color": spec.color,
        "inputs": list(spec.inputs),
        "outputs": list(spec.outputs),
        "settings": [{"name": k, "value": v} for k, v in spec.settings.items()],
        "stateful": spec.stateful,
        "doc": spec.doc,
        "builtin": builtin,
    }


@QmlElement
@QmlSingleton
class DiagramController(QObject):
    """Block library and function-editor checking."""

    libraryChanged = Signal()
    diagnosticsChanged = Signal()
    removedPortsChanged = Signal()
    statusTextChanged = Signal()
    documentChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._registry = BlockRegistry(builtin_specs())
        self._library: list[dict[str, object]] = []
        self._diagnostics: list[dict[str, object]] = []
        self._removed_ports: list[str] = []
        self._status_text = ""
        self._next_document = 1
        self._document = self._new_document_id()
        self._refresh_library()

    # ---- properties

    def _get_library(self) -> list[dict[str, object]]:
        return self._library

    def _get_diagnostics(self) -> list[dict[str, object]]:
        return self._diagnostics

    def _get_removed_ports(self) -> list[str]:
        return self._removed_ports

    def _get_status_text(self) -> str:
        return self._status_text

    def _get_document(self) -> str:
        return self._document

    library = Property("QVariantList", _get_library, notify=libraryChanged)
    diagnostics = Property("QVariantList", _get_diagnostics, notify=diagnosticsChanged)
    removedPorts = Property(  # noqa: N815
        "QStringList", _get_removed_ports, notify=removedPortsChanged
    )
    statusText = Property(str, _get_status_text, notify=statusTextChanged)  # noqa: N815
    document = Property(str, _get_document, notify=documentChanged)

    @property
    def registry(self) -> BlockRegistry:
        return self._registry

    # ---- slots

    @Slot(str, result=bool)
    def check(self, code: str) -> bool:
        """Report problems in ``code`` without changing the library."""
        result = self._registry.check_source(code, self._document)
        self._set_diagnostics(result)
        return result.ok

    @Slot(str, result=bool)
    def apply(self, code: str) -> bool:
        """Make ``code`` the current document's code if it has no errors."""
        result = self._registry.add_source(code, self._document)
        self._set_diagnostics(result)
        if not result.ok:
            errors = sum(1 for d in result.diagnostics if d.severity == ERROR)
            self._set_status(f"{errors} error(s): library not updated")
            return False
        self._set_removed_ports(result.removed_ports)
        self._refresh_library()
        self._set_status(self._describe(result))
        return True

    @Slot(result=str)
    def newDocument(self) -> str:  # noqa: N802
        """Start a new, empty document; blocks of the current one stay in the library."""
        self._set_document(self._new_document_id())
        self._clear_feedback()
        return self._document

    @Slot(str, result=str)
    def openBlock(self, name: str) -> str:  # noqa: N802
        """Make the document defining user block ``name`` current; return its code.

        Returns "" (and changes nothing) for built-in or unknown blocks.
        """
        document = self._registry.document_of(name)
        if document is None:
            return ""
        self._set_document(document)
        self._clear_feedback()
        return self._registry.source(document)

    @Slot(str, result=bool)
    def isBuiltin(self, name: str) -> bool:  # noqa: N802
        return self._registry.is_builtin(name)

    # ---- helpers

    def _new_document_id(self) -> str:
        document = f"function{self._next_document}"
        self._next_document += 1
        return document

    def _describe(self, result: SourceResult) -> str:
        parts: list[str] = []
        if result.specs:
            parts.append("Library: " + ", ".join(s.name for s in result.specs))
        if result.removed_blocks:
            parts.append("removed blocks: " + ", ".join(result.removed_blocks))
        if result.removed_ports:
            parts.append("removed ports: " + "; ".join(result.removed_ports))
        return " — ".join(parts) if parts else "No blocks defined"

    def _refresh_library(self) -> None:
        library = [
            spec_to_dict(s, self._registry.is_builtin(s.name)) for s in self._registry.specs()
        ]
        if library != self._library:
            self._library = library
            self.libraryChanged.emit()

    def _set_diagnostics(self, result: SourceResult) -> None:
        diagnostics = [asdict(d) for d in result.diagnostics]
        if diagnostics != self._diagnostics:
            self._diagnostics = diagnostics
            self.diagnosticsChanged.emit()

    def _set_removed_ports(self, ports: list[str]) -> None:
        if ports != self._removed_ports:
            self._removed_ports = ports
            self.removedPortsChanged.emit()

    def _set_status(self, text: str) -> None:
        if text != self._status_text:
            self._status_text = text
            self.statusTextChanged.emit()

    def _set_document(self, document: str) -> None:
        if document != self._document:
            self._document = document
            self.documentChanged.emit()

    def _clear_feedback(self) -> None:
        self._set_diagnostics(SourceResult())
        self._set_removed_ports([])
        self._set_status("")
