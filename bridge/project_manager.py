"""ProjectManager: the QML-facing object for project files (F7).

QML uses it as ``import QtSpice`` / ``ProjectManager.save()``. Main.qml attaches the
other three bridge objects once (``attach(Simulator, DiagramController,
WaveformModel)``); a project is the state they hold:

- the netlist editor text (``Simulator.netlistText``) and the analysis panel card;
- the user block code, the block diagram and its end time / time step
  (``DiagramController.export_state()``);
- the last netlist result and the last diagram result (saved as .npz files);
- the schematic, once ``attachSchematic(SchematicController)`` is called (S5); until
  then a loaded schematic is only kept so that saving does not drop it.

``modified`` compares that state with what was last saved or opened, so undoing
back to the saved state clears it again. ``save()`` returns False when the project
has no file yet; QML then asks for a name and calls ``saveAs(url)``.

Block code is only read when a project opens (never run), but it runs as soon as the
diagram does, so code the user did not write here deserves a look first. A project
counts as created by this app if this app last saved that exact block code to that
file path (a SHA-256 of the code, kept in QSettings with the path). Otherwise
``open`` stops and sends ``trustRequested``; QML asks the user and calls
``confirmTrust(bool)``. Declining opens the netlist, analysis settings and results but
no block code (blocks that need it are left off the canvas), as an untitled project,
so saving cannot overwrite the original file without its code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Property, QObject, QSettings, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement, QmlSingleton

from bridge.diagram_controller import DiagramController, DiagramState
from bridge.simulator import Simulator
from core.project.io import (
    SUFFIX,
    AnalysisSettings,
    BlockSource,
    DiagramEntry,
    LoadResult,
    NetlistEntry,
    Project,
    ProjectError,
    SimulationSettings,
    load_project,
    save_project,
)
from core.schematic.model import Schematic

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

MAX_RECENT = 8
NETLIST_RESULT = "netlist"
DIAGRAM_RESULT = "diagram"
_RECENT_KEY = "projects/recent"
_TRUSTED_KEY = "projects/trusted"


def _local_path(url: QUrl | str) -> Path:
    qurl = url if isinstance(url, QUrl) else QUrl(url)
    if qurl.isLocalFile():
        return Path(qurl.toLocalFile())
    return Path(str(url))


def code_digest(sources: list[BlockSource]) -> str:
    """Fingerprint of a project's block code (document names and code, in order)."""
    data = json.dumps([[s.document, s.code] for s in sources], ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def has_code(sources: list[BlockSource]) -> bool:
    return any(s.code.strip() for s in sources)


@QmlElement
@QmlSingleton
class ProjectManager(QObject):
    """New, Open, Save, Save As, recent files and the modified flag."""

    filePathChanged = Signal()
    modifiedChanged = Signal()
    recentFilesChanged = Signal()
    errorStringChanged = Signal()
    statusTextChanged = Signal()
    trustRequested = Signal(str, int)  # file name, number of documents with code
    projectLoaded = Signal()

    def __init__(self, parent: QObject | None = None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings()
        self._simulator: Simulator | None = None
        self._diagram: DiagramController | None = None
        self._waveforms: QObject | None = None
        self._file_path = ""
        self._modified = False
        self._error_string = ""
        self._status_text = ""
        self._netlist_name = "main"
        self._diagram_name = "main"
        self._extra_netlists: list[NetlistEntry] = []
        self._extra_diagrams: list[DiagramEntry] = []
        # Kept as loaded while no SchematicController is attached.
        self._schematic: Schematic | None = None
        self._schematic_ctl: QObject | None = None
        self._symbols: list[Path] = []  # circuit symbols (H3), absolute .asy paths
        self._saved_json = ""
        self._saved_results: tuple[object, object] = (None, None)
        self._pending: tuple[Path, Project, list[str]] | None = None

    # ---- properties

    def _get_file_path(self) -> str:
        return self._file_path

    def _get_file_name(self) -> str:
        return Path(self._file_path).name if self._file_path else "Untitled"

    def _get_modified(self) -> bool:
        return self._modified

    def _get_recent_files(self) -> list[str]:
        return self._read_list(_RECENT_KEY)

    def _get_error_string(self) -> str:
        return self._error_string

    def _get_status_text(self) -> str:
        return self._status_text

    filePath = Property(str, _get_file_path, notify=filePathChanged)  # noqa: N815
    fileName = Property(str, _get_file_name, notify=filePathChanged)  # noqa: N815
    modified = Property(bool, _get_modified, notify=modifiedChanged)
    recentFiles = Property("QStringList", _get_recent_files, notify=recentFilesChanged)  # noqa: N815
    errorString = Property(str, _get_error_string, notify=errorStringChanged)  # noqa: N815
    statusText = Property(str, _get_status_text, notify=statusTextChanged)  # noqa: N815

    # ---- wiring

    @Slot(QObject, QObject, QObject)
    def attach(self, simulator: QObject, diagram: QObject, waveforms: QObject) -> None:
        """Use these bridge objects' state as the project. Called once by Main.qml."""
        assert isinstance(simulator, Simulator) and isinstance(diagram, DiagramController)
        self._simulator, self._diagram, self._waveforms = simulator, diagram, waveforms
        for signal in (
            simulator.netlistTextChanged,
            simulator.analysisCardChanged,
            simulator.compatModeChanged,
            simulator.resultReady,
            diagram.sourcesChanged,
            diagram.runSettingsChanged,
            diagram.resultReady,
            diagram.diagramEdited,
        ):
            signal.connect(self._update_modified)
        self._mark_saved()

    @Slot(QObject)
    def attachSchematic(self, controller: QObject) -> None:  # noqa: N802 - QML API
        """Make the schematic editor's sheet part of the project."""
        self._schematic_ctl = controller
        controller.set_project_file(self._file_path)  # type: ignore[attr-defined]
        controller.set_project_symbols(self._symbols)  # type: ignore[attr-defined]
        controller.load_schematic(self._schematic)  # type: ignore[attr-defined]
        controller.schematicEdited.connect(self._update_modified)  # type: ignore[attr-defined]
        controller.projectSymbolsChanged.connect(self._update_modified)  # type: ignore[attr-defined]
        if self._simulator is not None and self._diagram is not None:
            self._mark_saved()

    # ---- state

    def current_project(self, path: Path | None = None) -> Project:
        """The project as the attached bridge objects hold it now. Symbol paths are
        written relative to the folder of ``path`` (default: the current file)."""
        sim, diagram = self._parts()
        state = diagram.export_state()
        results = {}
        if sim.result is not None:
            results[NETLIST_RESULT] = sim.result
        if diagram.result is not None:
            results[DIAGRAM_RESULT] = diagram.result
        return Project(
            netlists=[NetlistEntry(self._netlist_name, sim.property("netlistText"))]
            + self._extra_netlists,
            diagrams=[DiagramEntry(self._diagram_name, state.diagram)] + self._extra_diagrams,
            block_sources=[BlockSource(d, c) for d, c in state.sources],
            analysis=AnalysisSettings(sim.property("analysisCard"), state.t_end, state.dt),
            simulation=SimulationSettings(sim.property("compatMode")),
            results=results,
            schematic=(
                self._schematic_ctl.schematic_or_none()  # type: ignore[attr-defined]
                if self._schematic_ctl is not None
                else self._schematic
            ),
            symbols=self._symbol_entries(path),
        )

    def _project_symbols(self) -> list[Path]:
        ctl = self._schematic_ctl
        return ctl.project_symbols() if ctl is not None else list(self._symbols)  # type: ignore[attr-defined]

    def _symbol_entries(self, path: Path | None) -> list[str]:
        """The circuit symbols as stored in the .qsp: relative to its folder when inside
        it (with /), else absolute."""
        target = path if path is not None else (Path(self._file_path) if self._file_path else None)
        folder = target.parent.resolve() if target is not None else None
        out: list[str] = []
        for symbol in self._project_symbols():
            symbol = symbol.resolve()
            if folder is not None and symbol.is_relative_to(folder):
                out.append(symbol.relative_to(folder).as_posix())
            else:
                out.append(str(symbol))
        return out

    # ---- slots

    @Slot(result=bool)
    def newProject(self) -> bool:  # noqa: N802 - QML API
        """Start an empty, untitled project. QML asks about unsaved changes first."""
        if not self._can_replace():
            return False
        self._netlist_name = self._diagram_name = "main"
        self._apply(Project(), "", trusted=True)
        self._set_status("New project")
        self.projectLoaded.emit()
        return True

    @Slot("QVariant", result=bool)
    def open(self, url: QUrl | str) -> bool:
        """Open a .qsp file. Returns False (``errorString`` set) if it cannot be opened.

        When the project holds block code this app did not save, nothing changes yet:
        ``trustRequested`` is sent and ``confirmTrust`` finishes the open.
        """
        if not self._can_replace():
            return False
        path = _local_path(url).resolve()
        loaded: LoadResult = load_project(path)
        if not loaded.ok or loaded.project is None:
            self._set_error(f"Cannot open {path.name}: " + "; ".join(loaded.errors))
            if not path.exists():
                self._remove_recent(str(path))
            return False
        project = loaded.project
        if has_code(project.block_sources) and not self._is_trusted(path, project):
            self._pending = (path, project, loaded.warnings)
            count = sum(1 for s in project.block_sources if s.code.strip())
            self.trustRequested.emit(path.name, count)
            return True
        self._finish_open(path, project, loaded.warnings, trusted=True)
        return True

    @Slot(bool)
    def confirmTrust(self, trust: bool) -> None:  # noqa: N802 - QML API
        """Finish the open that sent ``trustRequested``."""
        if self._pending is None:
            return
        path, project, warnings = self._pending
        self._pending = None
        self._finish_open(path, project, warnings, trusted=trust)

    @Slot(result=bool)
    def save(self) -> bool:
        """Save to the current file. False if there is none yet (use ``saveAs``) or the
        save failed (``errorString`` set)."""
        if not self._file_path:
            return False
        return self._save_to(Path(self._file_path))

    @Slot("QVariant", result=bool)
    def saveAs(self, url: QUrl | str) -> bool:  # noqa: N802 - QML API
        path = _local_path(url)
        if path.suffix.lower() != SUFFIX:
            path = path.with_name(path.name + SUFFIX)
        return self._save_to(path.resolve())

    @Slot()
    def clearRecentFiles(self) -> None:  # noqa: N802 - QML API
        self._write_list(_RECENT_KEY, [])
        self.recentFilesChanged.emit()

    @Slot("QVariant", result=bool)
    def isProjectFile(self, url: QUrl | str) -> bool:  # noqa: N802 - QML API
        return _local_path(url).suffix.lower() == SUFFIX

    @Slot("QVariant", result=str)
    def toLocalFile(self, url: QUrl | str) -> str:  # noqa: N802 - QML API
        return str(_local_path(url))

    # ---- opening

    def _finish_open(
        self, path: Path, project: Project, warnings: list[str], trusted: bool
    ) -> None:
        self._netlist_name = project.netlists[0].name if project.netlists else "main"
        self._diagram_name = project.diagrams[0].name if project.diagrams else "main"
        warnings = warnings + self._apply(project, str(path), trusted)
        if trusted:
            if has_code(project.block_sources):  # the user said yes: don't ask again
                self._trust(path, project.block_sources)
            self._add_recent(str(path))
            text = f"Opened {path.name}"
        else:
            self._set_file_path("")
            text = (
                f"Opened {path.name} without its block code, as an untitled project "
                "(saving will not overwrite the original file)"
            )
        if warnings:
            text += ". " + "; ".join(warnings)
        self._set_error("")
        self._set_status(text)
        self.projectLoaded.emit()

    def _apply(self, project: Project, path: str, trusted: bool) -> list[str]:
        """Put ``project`` into the bridge objects. Returns what was skipped."""
        sim, diagram = self._parts()
        self._extra_netlists = list(project.netlists[1:])
        self._extra_diagrams = list(project.diagrams[1:])
        self._schematic = project.schematic
        folder = Path(path).parent if path else Path.cwd()
        self._symbols = [(folder / s).resolve() for s in project.symbols]
        skipped = [f"circuit symbol {s} is missing" for s in self._symbols if not s.is_file()]
        if self._schematic_ctl is not None:
            self._schematic_ctl.set_project_symbols(self._symbols)  # type: ignore[attr-defined]
            self._schematic_ctl.load_schematic(project.schematic)  # type: ignore[attr-defined]
        sources = [(s.document, s.code) for s in project.block_sources] if trusted else []
        first = project.diagrams[0].diagram if project.diagrams else {"blocks": [], "wires": []}
        warnings = diagram.load_state(
            DiagramState(
                sources, first, project.analysis.diagram_t_end, project.analysis.diagram_dt
            )
        )
        sim.set_file_path(path)
        sim.setProperty("analysisCard", project.analysis.card)
        sim.setProperty("compatMode", project.simulation.compat_mode)
        sim.setProperty("netlistText", project.netlists[0].text if project.netlists else "")
        netlist_result = project.results.get(NETLIST_RESULT)
        diagram_result = project.results.get(DIAGRAM_RESULT)
        sim.restore_result(netlist_result)
        diagram.restore_result(diagram_result)
        if netlist_result is None and diagram_result is not None and self._waveforms is not None:
            self._waveforms.set_result(diagram_result)  # type: ignore[attr-defined]
        self._set_file_path(path)
        self._mark_saved()
        return warnings + skipped

    def _can_replace(self) -> bool:
        sim, diagram = self._parts()
        if sim.property("running") or diagram.property("running"):
            self._set_error("Stop the running simulation first")
            return False
        self._pending = None
        return True

    # ---- saving

    def _save_to(self, path: Path) -> bool:
        project = self.current_project(path)
        try:
            save_project(path, project)
        except (ProjectError, OSError) as exc:
            self._set_error(f"Cannot save {path.name}: {exc}")
            return False
        if has_code(project.block_sources):
            self._trust(path, project.block_sources)
        self._set_file_path(str(path))
        self._add_recent(str(path))
        self._mark_saved()
        self._set_error("")
        self._set_status(f"Saved {path.name}")
        return True

    # ---- modified flag

    def _snapshot(self) -> tuple[str, tuple[object, object]]:
        project = self.current_project()
        data = project.to_dict()
        del data["results"]
        sim, diagram = self._parts()
        return json.dumps(data, sort_keys=True), (sim.result, diagram.result)

    def _mark_saved(self) -> None:
        self._saved_json, self._saved_results = self._snapshot()
        self._set_modified(False)

    @Slot()
    def _update_modified(self) -> None:
        if self._simulator is None or self._diagram is None:
            return
        text, results = self._snapshot()
        same_results = all(a is b for a, b in zip(results, self._saved_results, strict=True))
        self._set_modified(text != self._saved_json or not same_results)

    # ---- settings

    def _is_trusted(self, path: Path, project: Project) -> bool:
        return self._read_dict(_TRUSTED_KEY).get(str(path)) == code_digest(project.block_sources)

    def _trust(self, path: Path, sources: list[BlockSource]) -> None:
        trusted = self._read_dict(_TRUSTED_KEY)
        trusted[str(path)] = code_digest(sources)
        self._write_dict(_TRUSTED_KEY, trusted)

    def _add_recent(self, path: str) -> None:
        recent = [p for p in self._read_list(_RECENT_KEY) if p != path]
        self._write_list(_RECENT_KEY, [path, *recent][:MAX_RECENT])
        self.recentFilesChanged.emit()

    def _remove_recent(self, path: str) -> None:
        recent = self._read_list(_RECENT_KEY)
        if path in recent:
            self._write_list(_RECENT_KEY, [p for p in recent if p != path])
            self.recentFilesChanged.emit()

    # Lists and dicts are stored as JSON text: QSettings' own list handling differs
    # between the INI and registry backends (a one-item list comes back as a string).
    def _read_list(self, key: str) -> list[str]:
        value = self._read_json(key)
        return [str(v) for v in value] if isinstance(value, list) else []

    def _read_dict(self, key: str) -> dict[str, str]:
        value = self._read_json(key)
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

    def _read_json(self, key: str) -> object:
        try:
            return json.loads(str(self._settings.value(key, "")))
        except ValueError:
            return None

    def _write_list(self, key: str, value: list[str]) -> None:
        self._settings.setValue(key, json.dumps(value))
        self._settings.sync()

    def _write_dict(self, key: str, value: dict[str, str]) -> None:
        self._settings.setValue(key, json.dumps(value))
        self._settings.sync()

    # ---- helpers

    def _parts(self) -> tuple[Simulator, DiagramController]:
        if self._simulator is None or self._diagram is None:
            raise RuntimeError("ProjectManager.attach() has not been called")
        return self._simulator, self._diagram

    def _set_file_path(self, path: str) -> None:
        if path != self._file_path:
            self._file_path = path
            self.filePathChanged.emit()
        if self._schematic_ctl is not None:
            self._schematic_ctl.set_project_file(path)  # type: ignore[attr-defined]

    def _set_modified(self, modified: bool) -> None:
        if modified != self._modified:
            self._modified = modified
            self.modifiedChanged.emit()

    def _set_error(self, text: str) -> None:
        if text != self._error_string:
            self._error_string = text
            self.errorStringChanged.emit()

    def _set_status(self, text: str) -> None:
        if text != self._status_text:
            self._status_text = text
            self.statusTextChanged.emit()
