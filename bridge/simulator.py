"""Simulator: the QML-facing object for checking and running netlists.

QML uses it as ``import QtSpice`` / ``Simulator.check(text)`` / ``Simulator.run(text)``.
ngspice runs on a QThread (SimulationWorker); results go to the attached WaveformModel.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Property,
    QCoreApplication,
    QObject,
    Qt,
    QThread,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtQml import QmlElement, QmlSingleton
from PySide6.QtQuick import QQuickTextDocument

from bridge.highlighter import NetlistHighlighter
from bridge.sim_worker import SimulationWorker
from core.netlist.builder import build
from core.netlist.model import ERROR, Analysis, Circuit, Diagnostic
from core.netlist.parser import parse
from core.netlist.validate import validate
from core.sim.backend import DEFAULT_COMPAT_MODE, SimulatorBackend, valid_compat_mode
from core.sim.ngspice_adapter import NgspiceAdapter
from core.sim.result import SimulationResult

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

NETLIST_SUFFIXES = (".cir", ".net", ".sp")
MAX_CONSOLE_LINES = 2000
_QUEUED = Qt.ConnectionType.QueuedConnection


def _local_path(url: QUrl | str) -> Path:
    qurl = url if isinstance(url, QUrl) else QUrl(url)
    if qurl.isLocalFile():
        return Path(qurl.toLocalFile())
    return Path(str(url))


def _card(analysis: Analysis) -> str:
    return " ".join([f".{analysis.kind}", *analysis.args])


@QmlElement
@QmlSingleton
class Simulator(QObject):
    """Checks and runs netlists.

    ``diagnostics`` is a list of {line, col, severity, message}. ``deck`` is the clean
    SPICE deck of the last check, or "" if it had errors. ``filePath`` is the netlist
    file last opened; relative ``.include`` paths are resolved against its folder.

    ``run(text)`` checks first and refuses to start if there are errors. One analysis
    runs at a time: ``analysisCard`` (set by the AnalysisPanel) replaces the netlist's
    analysis cards in the deck that is sent, through ``build``; when it is empty the
    netlist's first card (``netlistAnalysis``) is used, and ``statusText`` says how many
    other netlist cards were not run. While ``analysisCard`` is set,
    a netlist without analysis cards is not an error. ``sentDeck`` is the deck that
    was sent and ``consoleText`` the ngspice output of the last run.

    ``netlistText`` mirrors the netlist editor's text (the editor writes it on every
    edit, and follows it when a project is loaded), so a project can be saved
    without asking QML for it.
    """

    diagnosticsChanged = Signal()
    deckChanged = Signal()
    filePathChanged = Signal()
    errorStringChanged = Signal()
    runningChanged = Signal()
    progressChanged = Signal()
    consoleTextChanged = Signal()
    sentDeckChanged = Signal()
    statusTextChanged = Signal()
    analysisCardChanged = Signal()
    netlistAnalysisChanged = Signal()
    netlistTextChanged = Signal()
    compatModeChanged = Signal()
    resultReady = Signal()
    _start = Signal(str)

    def __init__(
        self, parent: QObject | None = None, backend: SimulatorBackend | None = None
    ) -> None:
        super().__init__(parent)
        self._diagnostics: list[dict[str, object]] = []
        self._deck = ""
        self._file_path = ""
        self._error_string = ""
        self._highlighters: list[NetlistHighlighter] = []
        self._backend = backend
        self._worker: SimulationWorker | None = None
        self._thread: QThread | None = None
        self._running = False
        self._progress = 0
        self._console: list[str] = []
        self._console_text = ""
        self._sent_deck = ""
        self._status_text = ""
        self._analysis_card = ""
        self._netlist_analysis = ""
        self._netlist_text = ""
        self._compat_mode = DEFAULT_COMPAT_MODE
        self._skipped_note = ""
        self._result: SimulationResult | None = None
        self._last_text: str | None = None
        self._waveforms: QObject | None = None
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # ---- properties

    def _get_diagnostics(self) -> list[dict[str, object]]:
        return self._diagnostics

    def _get_deck(self) -> str:
        return self._deck

    def _get_file_path(self) -> str:
        return self._file_path

    def _get_error_string(self) -> str:
        return self._error_string

    def _get_running(self) -> bool:
        return self._running

    def _get_progress(self) -> int:
        return self._progress

    def _get_console_text(self) -> str:
        return self._console_text

    def _get_sent_deck(self) -> str:
        return self._sent_deck

    def _get_status_text(self) -> str:
        return self._status_text

    def _get_analysis_card(self) -> str:
        return self._analysis_card

    def _set_analysis_card(self, card: str) -> None:
        card = " ".join(card.split())
        if card != self._analysis_card:
            had_card = bool(self._analysis_card)
            self._analysis_card = card
            self.analysisCardChanged.emit()
            # A panel card makes a netlist without analysis cards valid (and back):
            # re-check so the editor's diagnostics follow.
            if had_card != bool(card) and self._last_text is not None:
                self._check(self._last_text)

    def _get_netlist_analysis(self) -> str:
        return self._netlist_analysis

    def _get_available(self) -> bool:
        return self._get_backend().available()

    def _get_netlist_text(self) -> str:
        return self._netlist_text

    def _get_compat_mode(self) -> str:
        return self._compat_mode

    def _set_compat_mode(self, mode: str) -> None:
        mode = mode.strip().lower()
        if not valid_compat_mode(mode):
            self._set_status(f"Ignored invalid compatibility mode {mode!r}")
            return
        if mode != self._compat_mode:
            self._compat_mode = mode
            self.compatModeChanged.emit()

    def _set_netlist_text(self, text: str) -> None:
        if text != self._netlist_text:
            self._netlist_text = text
            self.netlistTextChanged.emit()

    diagnostics = Property("QVariantList", _get_diagnostics, notify=diagnosticsChanged)
    deck = Property(str, _get_deck, notify=deckChanged)
    filePath = Property(str, _get_file_path, notify=filePathChanged)  # noqa: N815
    errorString = Property(str, _get_error_string, notify=errorStringChanged)  # noqa: N815
    running = Property(bool, _get_running, notify=runningChanged)
    progress = Property(int, _get_progress, notify=progressChanged)
    consoleText = Property(str, _get_console_text, notify=consoleTextChanged)  # noqa: N815
    sentDeck = Property(str, _get_sent_deck, notify=sentDeckChanged)  # noqa: N815
    statusText = Property(str, _get_status_text, notify=statusTextChanged)  # noqa: N815
    analysisCard = Property(  # noqa: N815
        str, _get_analysis_card, _set_analysis_card, notify=analysisCardChanged
    )
    netlistAnalysis = Property(  # noqa: N815
        str, _get_netlist_analysis, notify=netlistAnalysisChanged
    )
    available = Property(bool, _get_available, constant=True)
    netlistText = Property(  # noqa: N815
        str, _get_netlist_text, _set_netlist_text, notify=netlistTextChanged
    )
    # ngspice compatibility mode ("ltpsa" by default, "" = off); stored in the project.
    compatMode = Property(  # noqa: N815
        str, _get_compat_mode, _set_compat_mode, notify=compatModeChanged
    )

    @property
    def result(self) -> SimulationResult | None:
        """Result of the last successful run."""
        return self._result

    def restore_result(self, result: SimulationResult | None) -> None:
        """Make ``result`` the last result (a project was loaded) and show it."""
        self._result = result
        if result is None:
            if self._waveforms is not None:
                getattr(self._waveforms, "set_result", lambda _r: None)(None)
        else:
            self._push_result()

    def set_file_path(self, path: str) -> None:
        """Set the file that relative ``.include`` paths are resolved against."""
        resolved = str(Path(path).resolve()) if path else ""
        if resolved != self._file_path:
            self._file_path = resolved
            self.filePathChanged.emit()

    def _set_error_string(self, text: str) -> None:
        if text != self._error_string:
            self._error_string = text
            self.errorStringChanged.emit()

    def _set_running(self, running: bool) -> None:
        if running != self._running:
            self._running = running
            self.runningChanged.emit()

    @Slot(int)
    def _set_progress(self, percent: int) -> None:
        if percent != self._progress:
            self._progress = percent
            self.progressChanged.emit()

    def _set_status(self, text: str) -> None:
        if text != self._status_text:
            self._status_text = text
            self.statusTextChanged.emit()

    def _set_sent_deck(self, deck: str) -> None:
        if deck != self._sent_deck:
            self._sent_deck = deck
            self.sentDeckChanged.emit()

    # ---- slots

    @Slot(str, result=bool)
    def check(self, text: str) -> bool:
        """Parse and validate ``text``. Returns True if there are no errors."""
        return self._check(text)[0]

    def _check(self, text: str) -> tuple[bool, Circuit]:
        self._last_text = text
        base_dir = Path(self._file_path).parent if self._file_path else None
        result = parse(text, base_dir)
        found: list[Diagnostic] = sorted(
            result.diagnostics + validate(result.circuit, require_analysis=not self._analysis_card),
            key=lambda d: (d.line, d.col),
        )
        ok = not any(d.severity == ERROR for d in found)
        self._diagnostics = [d.to_dict() for d in found]
        self.diagnosticsChanged.emit()
        deck = build(result.circuit) if ok else ""
        if deck != self._deck:
            self._deck = deck
            self.deckChanged.emit()
        analyses = result.circuit.analyses
        first = _card(analyses[0]) if analyses else ""
        if first != self._netlist_analysis:
            self._netlist_analysis = first
            self.netlistAnalysisChanged.emit()
        return ok, result.circuit

    @Slot(str, result=bool)
    def run(self, text: str) -> bool:
        """Check ``text`` and start a simulation in the background.

        Returns False (and sets ``statusText``) if it did not start: the netlist has
        errors, there is no analysis, the analysis card is invalid, ngspice is missing
        or a run is already going.
        """
        if self._running:
            self._set_status("A simulation is already running")
            return False
        ok, circuit = self._check(text)
        if not ok:
            errors = sum(1 for d in self._diagnostics if d["severity"] == ERROR)
            self._set_status(f"Not started: the netlist has {errors} error(s)")
            return False
        card = self._analysis_card or self._netlist_analysis
        if not card:
            self._set_status("Not started: no analysis (.op, .dc, .ac or .tran)")
            return False
        deck = build(circuit, card)
        card_errors = [d for d in parse(deck).diagnostics if d.severity == ERROR]
        if card_errors:
            self._set_status(f"Not started: {card}: {card_errors[0].message}")
            return False
        backend = self._get_backend()
        if hasattr(backend, "compat_mode"):
            backend.compat_mode = self._compat_mode  # type: ignore[attr-defined]
        if not backend.available():
            reason = getattr(backend, "last_error", "") or "library not found"
            self._set_status(f"Not started: ngspice is not available ({reason})")
            return False

        self._ensure_thread()
        self._console.clear()
        self._console_text = ""
        self.consoleTextChanged.emit()
        self._set_sent_deck(deck)
        self._set_progress(0)
        self._skipped_note = "" if self._analysis_card else _skipped_note(circuit)
        self._set_status(f"Running {card.split()[0]} ...{self._skipped_note}")
        self._set_running(True)
        assert self._worker is not None
        self._worker.arm()
        self._start.emit(deck)
        return True

    @Slot(str, result=str)
    def checkAnalysisCard(self, card: str) -> str:  # noqa: N802 - QML API
        """Error message for an analysis card such as ".ac dec 10 1 1k", or "" if valid."""
        result = parse(f"card check\n{card}\n.end\n")
        for d in result.diagnostics:
            if d.severity == ERROR and d.line == 2:
                return d.message
        if not result.circuit.analyses:
            return "not an analysis card (.op, .dc, .ac or .tran)"
        return ""

    @Slot()
    def stop(self) -> None:
        """Cancel the running simulation (no-op if none is running)."""
        if self._running and self._worker is not None:
            self._set_status("Stopping ...")
            self._worker.stop()

    @Slot(QObject)
    def attachWaveforms(self, model: QObject) -> None:  # noqa: N802 - QML API
        """Send every new result to ``model`` (a WaveformModel)."""
        self._waveforms = model
        if self._result is not None:
            self._push_result()

    @Slot()
    def shutdown(self) -> None:
        """Stop any run and end the worker thread. Called when the app quits."""
        if self._thread is None:
            return
        if self._worker is not None:
            self._worker.stop()
        self._thread.quit()
        self._thread.wait(10_000)
        self._thread = None
        self._worker = None

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

    # ---- worker plumbing

    def _get_backend(self) -> SimulatorBackend:
        if self._backend is None:
            self._backend = NgspiceAdapter()
        return self._backend

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = QThread()
        self._thread.setObjectName("SimulationWorker")
        self._worker = SimulationWorker(self._get_backend())
        self._worker.moveToThread(self._thread)
        self._thread.finished.connect(self._worker.deleteLater)
        self._start.connect(self._worker.run, _QUEUED)
        self._worker.message.connect(self._on_message, _QUEUED)
        self._worker.progress.connect(self._set_progress, _QUEUED)
        self._worker.finished.connect(self._on_finished, _QUEUED)
        self._worker.failed.connect(self._on_failed, _QUEUED)
        self._thread.start()

    @Slot(str)
    def _on_message(self, text: str) -> None:
        self._append_console(text.split("\n"))

    def _append_console(self, lines: list[str]) -> None:
        self._console.extend(lines)
        if len(self._console) > MAX_CONSOLE_LINES:
            del self._console[: len(self._console) - MAX_CONSOLE_LINES]
        self._console_text = "\n".join(self._console)
        self.consoleTextChanged.emit()

    @Slot(object)
    def _on_finished(self, result: SimulationResult) -> None:
        self._result = result
        if result.axis:
            points = len(result.vectors[result.axis])
            self._set_status(f"Done: .{result.analysis}, {points} points{self._skipped_note}")
        else:
            self._set_status(f"Done: .{result.analysis}{self._skipped_note}")
            self._append_console(["", "Operating point:", *_op_lines(result)])
        self._set_progress(100)
        self._set_running(False)
        self._push_result()
        self.resultReady.emit()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        stopped = "stopped" in message.lower()
        self._set_status("Stopped" if stopped else f"Failed: {message}")
        if not stopped:
            self._append_console([f"Error: {message}"])
        self._set_running(False)

    def _push_result(self) -> None:
        if self._waveforms is not None and self._result is not None:
            set_result = getattr(self._waveforms, "set_result", None)
            if set_result is not None:
                set_result(self._result)


def _skipped_note(circuit: Circuit) -> str:
    """Status suffix when the netlist has analysis cards after the one that runs."""
    others = len(circuit.analyses) - 1
    if others < 1:
        return ""
    cards = "card was" if others == 1 else "cards were"
    return f" ({others} more analysis {cards} not run: choose one in the Analysis panel)"


def _op_lines(result: SimulationResult) -> list[str]:
    lines = []
    for name in result.trace_names():
        data = result.vectors[name]
        value = float(data.real[0]) if len(data) else float("nan")
        unit = result.units.get(name, "")
        lines.append(f"  {name} = {value:.6g} {unit}".rstrip())
    return lines
