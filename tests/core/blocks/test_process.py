from __future__ import annotations

import os
import threading
import time
from multiprocessing.connection import Connection

import pytest

from core.blocks.process import CANCELLED, ERROR, OK, TIMEOUT, ChildProcess


def _send(conn: Connection, value: object) -> None:
    conn.send(("got", value))
    conn.close()


def _sleep(conn: Connection) -> None:
    time.sleep(60)


def _exit(conn: Connection, code: int) -> None:
    os._exit(code)


def test_child_sends_one_message() -> None:
    outcome = ChildProcess(_send, ([1, 2.5, "x"],), timeout=30).wait()
    assert outcome.status == OK and outcome.ok
    assert outcome.message == ("got", [1, 2.5, "x"])


def test_child_that_exits_without_a_message_is_an_error() -> None:
    outcome = ChildProcess(_exit, (3,), timeout=30, name="test runner").wait()
    assert outcome.status == ERROR
    assert outcome.error == "test runner process exited with code 3"


def test_timeout_stops_the_child() -> None:
    child = ChildProcess(_sleep, (), timeout=1.0)
    t0 = time.monotonic()
    outcome = child.wait()
    assert outcome.status == TIMEOUT
    assert time.monotonic() - t0 < 10
    assert child.process is not None and not child.process.is_alive()


def test_cancel_from_another_thread() -> None:
    child = ChildProcess(_sleep, (), timeout=60)
    child.start()
    threading.Timer(0.5, child.cancel).start()
    outcome = child.wait()
    assert outcome.status == CANCELLED and child.cancelled
    assert child.process is not None and not child.process.is_alive()


def test_runs_only_once() -> None:
    child = ChildProcess(_send, (1,), timeout=30)
    child.wait()
    with pytest.raises(RuntimeError):
        child.start()
    assert child.wait().ok  # the outcome is kept


class _LatePipe:
    """The parent's end of the pipe as the race sees it: the first poll() times out
    although the child sends its message and exits right after (D6 found this)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._first = True

    def poll(self, timeout: float = 0.0) -> bool:
        if self._first:
            self._first = False
            return False
        return self._conn.poll(timeout)

    def recv(self) -> object:
        return self._conn.recv()

    def close(self) -> None:
        self._conn.close()


def test_message_sent_just_before_the_child_exits_is_kept() -> None:
    child = ChildProcess(_send, ("late",), timeout=30)
    child.start()
    assert child.process is not None
    child.process.join(timeout=30)  # it has sent and exited
    assert child.process.exitcode == 0
    child._conn = _LatePipe(child._conn)  # type: ignore[arg-type,assignment]
    outcome = child.wait()
    assert outcome.status == OK, outcome.error
    assert outcome.message == ("got", "late")
