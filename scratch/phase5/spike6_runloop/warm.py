"""S6 spike, parent side of a warm grid child. Qt-free.

The same process pattern as core/blocks/process.ChildProcess (rule 5): a
``multiprocessing`` spawn context, a Pipe and a daemon Process; statuses and
ChildOutcome are the helper's own. Differences, because the child serves more than
one run:

- The Pipe is duplex (requests down, replies up). One request is in flight at a time.
- A failed run (ERROR, TIMEOUT, CANCELLED) always terminates the child: after a
  timeout or a Stop the child may be anywhere inside OpenDSS, so it is never reused.
  The next ``run`` spawns a new one (``restarts`` counts them). Nothing is retried
  automatically: the failed run reports its own outcome.
- A child that died while idle (killed, crashed) is noticed by the next ``run`` (dead
  process, or the request cannot be sent) and replaced before the request is sent.
- ``stop()`` sends STOP, joins, terminates if the child did not exit, closes the pipe.
  If the parent dies, the child gets EOF on its pipe and returns; daemon=True also
  kills it when the parent exits normally.

State lives in the WarmGridChild instance (owned by one controller), never in module
globals; the child's engine is a local of child.warm_main.
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import threading
import time
from multiprocessing.connection import Connection

from child import RUN, STOP, Reply, Request, warm_main

from core.blocks.process import CANCELLED, ERROR, OK, TIMEOUT, ChildOutcome

_POLL = 0.05


class WarmGridChild:
    def __init__(self, reuse_engine: bool = False, timeout: float = 30.0) -> None:
        self.reuse_engine = reuse_engine
        self.timeout = float(timeout)
        self.name = "grid runner"
        self.restarts = -1  # the first start is not a restart
        self.ready_stamps: dict[str, float] = {}
        self.spawned_at = 0.0
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._process: mp.process.BaseProcess | None = None
        self._conn: Connection | None = None
        self._ready = False

    # ---- lifecycle

    @property
    def process(self) -> mp.process.BaseProcess | None:
        return self._process

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start(self) -> None:
        """Spawn the child now (e.g. when the designer opens). Returns at once."""
        with self._lock:
            self._spawn()

    def _spawn(self) -> None:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe(duplex=True)
        proc = ctx.Process(target=warm_main, args=(child, self.reuse_engine), daemon=True)
        self.spawned_at = time.perf_counter()
        proc.start()
        child.close()
        self._process, self._conn, self._ready = proc, parent, False
        self.restarts += 1

    def stop(self, timeout: float = 2.0) -> int | None:
        """Shut the child down; returns its exit code (0 = clean). Idempotent."""
        with self._lock:
            proc, conn = self._process, self._conn
            self._process, self._conn, self._ready = None, None, False
        if proc is None:
            return None
        if conn is not None:
            with contextlib.suppress(EOFError, OSError):
                conn.send((STOP, None))
        proc.join(timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
        if conn is not None:
            conn.close()
        return proc.exitcode

    def cancel(self) -> None:
        """Stop the current run. Any thread; terminates the child at once."""
        self._cancelled.set()
        with self._lock:
            if self._process is not None and self._process.is_alive():
                self._process.terminate()

    def _discard(self) -> None:
        """Terminate and forget the child (after any failed run)."""
        with self._lock:
            proc, conn = self._process, self._conn
            self._process, self._conn, self._ready = None, None, False
        if proc is not None:
            if proc.is_alive():
                proc.terminate()
            proc.join(5)
        if conn is not None:
            conn.close()

    # ---- runs

    def run(self, request: Request, timeout: float | None = None) -> ChildOutcome:
        """One run. OK -> ``message`` is the child's Reply. The timeout covers a
        (re)start of the child when one is needed, as in ChildProcess.

        On Windows ``send`` of a large request returns only when the child has read it.
        If the child turns out dead while the request is sent, the request never
        reached it: it is sent once more to a new child (nothing ran twice).
        """
        self._cancelled.clear()
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        for attempt in (0, 1):
            with self._lock:
                if self._process is None or not self._process.is_alive():
                    self._close_dead()
                    self._spawn()
                conn = self._conn
            assert conn is not None
            if not self._ready:
                outcome = self._receive(conn, deadline, expect=None)
                if not outcome.ok:
                    self._discard()
                    return outcome
                _, _, self.ready_stamps = outcome.message
                self._ready = True
            try:
                conn.send((RUN, request))
                break
            except (EOFError, OSError):
                self._discard()
                if self._cancelled.is_set():
                    return ChildOutcome(CANCELLED, error="run cancelled")
                if attempt == 1:
                    return ChildOutcome(ERROR, error=f"cannot reach the {self.name}")
        outcome = self._receive(conn, deadline, expect=request.run_id)
        if not outcome.ok:
            self._discard()
        return outcome

    def _close_dead(self) -> None:
        if self._conn is not None:
            self._conn.close()
        if self._process is not None:
            self._process.join(1)
        self._process, self._conn, self._ready = None, None, False

    def _receive(self, conn: Connection, deadline: float, expect: int | None) -> ChildOutcome:
        proc = self._process
        while True:
            if self._cancelled.is_set():
                return ChildOutcome(CANCELLED, error="run cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ChildOutcome(TIMEOUT, error=f"run stopped after the {self.timeout:g} s")
            try:
                ready = conn.poll(min(_POLL, remaining))
            except (EOFError, OSError):
                ready = True
            if ready:
                try:
                    message = conn.recv()
                except (EOFError, OSError):
                    if self._cancelled.is_set():
                        return ChildOutcome(CANCELLED, error="run cancelled")
                    if proc is not None:
                        proc.join(1)
                    code = proc.exitcode if proc is not None else None
                    return ChildOutcome(ERROR, error=f"{self.name} process exited with code {code}")
                if expect is None or (isinstance(message, Reply) and message.run_id == expect):
                    return ChildOutcome(OK, message=message)
                continue  # a stale reply (cannot happen: failed runs kill the child)
            if proc is not None and not proc.is_alive():
                if self._cancelled.is_set():
                    return ChildOutcome(CANCELLED, error="run cancelled")
                return ChildOutcome(
                    ERROR, error=f"{self.name} process exited with code {proc.exitcode}"
                )
