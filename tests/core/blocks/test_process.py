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
