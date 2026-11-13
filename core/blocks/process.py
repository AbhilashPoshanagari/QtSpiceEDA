"""ChildProcess: one run of a function in a separate process. Qt-free.

The process pattern shared by every runner that keeps work out of the UI process
(BlockRunner for block diagrams, GridRunner for OpenDSS): a ``multiprocessing`` spawn
context (same on every OS), a one-way Pipe from the child to the parent and a daemon
Process. The child calls ``target(conn, *args)`` and sends exactly one message
through ``conn``; ``wait()`` returns that message.

``wait()`` covers the whole run, including starting the process, with ``timeout``.
``cancel()`` may be called from any thread and terminates the process at once.
"""

from __future__ import annotations

import multiprocessing as mp
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

OK = "ok"
ERROR = "error"
TIMEOUT = "timeout"
CANCELLED = "cancelled"

_POLL = 0.05


@dataclass
class ChildOutcome:
    """How the child ended. ``message`` is what it sent (status OK only)."""

    status: str
    message: Any = None
    error: str = ""
    """One line for the user when the status is not OK."""

    @property
    def ok(self) -> bool:
        return self.status == OK


class ChildProcess:
    """Runs ``target(conn, *args)`` once in a spawned daemon process.

    ``target`` must be a module-level function and ``args`` plain picklable data.
    ``name`` is used in error messages ("<name> process exited with code 1").
    """

    def __init__(
        self,
        target: Callable[..., None],
        args: tuple[Any, ...],
        timeout: float,
        name: str = "runner",
    ) -> None:
        self._target = target
        self._args = args
        self.timeout = float(timeout)
        self.name = name
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._process: mp.process.BaseProcess | None = None
        self._conn: Connection | None = None
        self._started_at = 0.0
        self._outcome: ChildOutcome | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def process(self) -> mp.process.BaseProcess | None:
        """The child process once started (for tests and diagnostics)."""
        return self._process

    def start(self) -> None:
        """Start the child process (returns at once)."""
        with self._lock:
            if self._process is not None:
                raise RuntimeError(f"a {self.name} runs only once")
            ctx = mp.get_context("spawn")
            parent, child = ctx.Pipe(duplex=False)
            self._process = ctx.Process(target=self._target, args=(child, *self._args), daemon=True)
            self._started_at = time.monotonic()
            self._process.start()
            child.close()
            self._conn = parent

    def cancel(self) -> None:
        """Stop the run. Safe to call from any thread, before or after it ends."""
        self._cancelled.set()
        with self._lock:
            if self._process is not None and self._process.is_alive():
                self._process.terminate()

    def wait(self) -> ChildOutcome:
        """Block until the child sends its message, exits, times out or is cancelled."""
        if self._outcome is not None:
            return self._outcome
        if self._process is None:
            self.start()
        assert self._process is not None and self._conn is not None
        deadline = self._started_at + self.timeout
        outcome: ChildOutcome | None = None
        while outcome is None:
            if self._cancelled.is_set():
                outcome = ChildOutcome(CANCELLED, error="run cancelled")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = ChildOutcome(
                    TIMEOUT, error=f"run stopped after the {self.timeout:g} s timeout"
                )
                break
            try:
                ready = self._conn.poll(min(_POLL, remaining))
            except (EOFError, OSError):
                ready = True  # the pipe closed: recv() below reports it
            if ready:
                outcome = self._receive()
            elif not self._process.is_alive():
                outcome = ChildOutcome(
                    ERROR,
                    error=f"{self.name} process exited with code {self._process.exitcode}",
                )
            # A cancel kills the process, which closes the pipe: that is not an error.
            if outcome is not None and not outcome.ok and self._cancelled.is_set():
                outcome = ChildOutcome(CANCELLED, error="run cancelled")
        self._finish()
        self._outcome = outcome
        return outcome

    def _receive(self) -> ChildOutcome:
        assert self._conn is not None
        try:
            return ChildOutcome(OK, message=self._conn.recv())
        except (EOFError, OSError) as err:
            # The pipe closes when the child dies: report its exit code if it has one.
            assert self._process is not None
            self._process.join(timeout=1.0)
            code = self._process.exitcode
            if code is not None:
                return ChildOutcome(ERROR, error=f"{self.name} process exited with code {code}")
            return ChildOutcome(ERROR, error=f"lost contact with the {self.name}: {err}")

    def _finish(self) -> None:
        with self._lock:
            proc = self._process
            if proc is not None:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=5)
            if self._conn is not None:
                self._conn.close()
