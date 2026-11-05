"""SimulationWorker: runs a SimulatorBackend (ngspice) on a QThread.

Usage: move the worker to a QThread and invoke ``run(deck)`` through a queued
connection. ``stop()`` may be called directly from any thread.

ngspice prints many console lines from its own thread; they are sent in batches
(at most every ``MESSAGE_INTERVAL`` seconds) so the GUI thread is not flooded, and
progress is only emitted when the percentage changes.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from core.sim.backend import SimulationError, SimulatorBackend

MESSAGE_INTERVAL = 0.05


class SimulationWorker(QObject):
    """Signals: ``progress(int)``, ``message(str)`` (one or more lines joined by
    newlines), ``finished(SimulationResult)`` and ``failed(str)``. Exactly one of
    ``finished`` / ``failed`` is emitted per ``run``."""

    progress = Signal(int)
    message = Signal(str)
    finished = Signal(object)  # SimulationResult
    failed = Signal(str)

    def __init__(self, backend: SimulatorBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._last_flush = 0.0
        self._last_percent = -1
        self._stop = threading.Event()

    @property
    def backend(self) -> SimulatorBackend:
        return self._backend

    @Slot(str)
    def run(self, deck: str) -> None:
        backend = self._backend
        backend.on_message = self._on_message
        backend.on_progress = self._on_progress
        self._last_percent = -1
        try:
            backend.load(deck)
            if self._stop.is_set():
                raise SimulationError("simulation stopped")
            backend.run()
            result = backend.results()
            if result is None:
                raise SimulationError("the simulator returned no result")
        except SimulationError as exc:
            self._flush(force=True)
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # a crash in the backend must not kill the thread
            self._flush(force=True)
            self.failed.emit(f"internal error: {exc}")
            return
        finally:
            backend.on_message = None
            backend.on_progress = None
        self._flush(force=True)
        self.finished.emit(result)

    def arm(self) -> None:
        """Clear a previous stop request. Call before queueing the next ``run``; ``run``
        itself does not clear it, so a stop that arrives before the queued run starts
        is not lost."""
        self._stop.clear()

    def stop(self) -> None:
        """Ask the running simulation to stop. Thread-safe; not a queued slot because
        the worker thread is busy inside ``run``."""
        self._stop.set()
        self._backend.stop()

    # --- backend callbacks (any thread) --------------------------------------

    def _on_message(self, line: str) -> None:
        with self._lock:
            self._pending.append(line)
        self._flush()

    def _on_progress(self, percent: int) -> None:
        self._flush()  # progress keeps arriving, so held-back lines get out promptly
        if percent != self._last_percent:
            self._last_percent = percent
            self.progress.emit(percent)

    def _flush(self, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._pending or (not force and now - self._last_flush < MESSAGE_INTERVAL):
                return
            text = "\n".join(self._pending)
            self._pending.clear()
            self._last_flush = now
        self.message.emit(text)
