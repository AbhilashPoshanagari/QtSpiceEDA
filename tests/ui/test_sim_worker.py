from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from bridge.sim_worker import SimulationWorker
from core.sim.backend import SimulationError, SimulatorBackend
from core.sim.ngspice_adapter import NgspiceAdapter
from core.sim.result import SimulationResult
from tests.ui.fakes import FakeBackend

RC_DECK = "rc\nV1 in 0 PULSE(0 1 0 1n 1n 1 2)\nR1 in out 1k\nC1 out 0 1u\n.tran 1u 5m\n.end\n"


class _Starter(QObject):
    start = Signal(str)


class _MainThreadSink(QObject):
    """Receives the worker's signals in the main thread through queued connections,
    as the Simulator does, and emits ``done`` once ``finished`` has been handled.

    Wait on ``done``, not on ``worker.finished``: pytest-qt's waitSignal callback is a
    plain callable, so for a worker on another thread it runs in that thread and ends
    the wait before the main thread has handled the queued ``message`` events.
    """

    done = Signal()

    def __init__(self, worker: SimulationWorker) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.result: SimulationResult | None = None
        queued = Qt.ConnectionType.QueuedConnection
        worker.message.connect(self.add, queued)
        worker.finished.connect(self.finish, queued)

    @Slot(str)
    def add(self, text: str) -> None:
        self.lines.append(text)

    @Slot(object)
    def finish(self, result: SimulationResult) -> None:
        self.result = result
        self.done.emit()


def _on_thread(worker: SimulationWorker) -> tuple[QThread, _Starter]:
    thread = QThread()
    worker.moveToThread(thread)
    starter = _Starter()
    starter.start.connect(worker.run, Qt.ConnectionType.QueuedConnection)
    thread.start()
    return thread, starter


def _collect(worker: SimulationWorker) -> dict[str, list]:
    got: dict[str, list] = {"progress": [], "message": [], "finished": [], "failed": []}
    for name, items in got.items():
        getattr(worker, name).connect(items.append)
    return got


def test_finished_with_result(qapp: object) -> None:
    backend = FakeBackend(lines=["hello", "world"], progress=[10, 10, 50, 100])
    worker = SimulationWorker(backend)
    got = _collect(worker)
    worker.run("deck")
    assert backend.loaded == "deck"
    assert len(got["finished"]) == 1 and got["failed"] == []
    assert isinstance(got["finished"][0], SimulationResult)
    assert "\n".join(got["message"]).split("\n") == ["hello", "world"]
    assert got["progress"] == [10, 50, 100]  # repeats are dropped
    assert backend.on_message is None and backend.on_progress is None


@pytest.mark.parametrize("where", ["load", "run"])
def test_failed_on_simulation_error(qapp: object, where: str) -> None:
    worker = SimulationWorker(FakeBackend(fail_in=where))
    got = _collect(worker)
    worker.run("deck")
    assert got["finished"] == []
    assert got["failed"] == [f"{where} failed"]


def test_unexpected_exception_is_reported(qapp: object) -> None:
    class Broken(FakeBackend):
        def run(self) -> None:
            raise ZeroDivisionError("boom")

    worker = SimulationWorker(Broken())
    got = _collect(worker)
    worker.run("deck")
    assert got["failed"] == ["internal error: boom"]


def test_messages_are_batched(qapp: object) -> None:
    worker = SimulationWorker(FakeBackend(lines=[f"line {i}" for i in range(1000)]))
    got = _collect(worker)
    worker.run("deck")
    assert len(got["message"]) < 20
    assert "\n".join(got["message"]).count("\n") == 999


def test_stop_cancels_on_worker_thread(qtbot) -> None:
    backend = FakeBackend(block=True)
    worker = SimulationWorker(backend)
    thread, starter = _on_thread(worker)
    try:
        with qtbot.waitSignal(worker.failed, timeout=3000) as blocker:
            starter.start.emit("deck")
            assert backend.started.wait(2)
            worker.stop()
        assert blocker.args == ["simulation stopped"]
    finally:
        thread.quit()
        thread.wait(3000)


@pytest.mark.ngspice
def test_real_ngspice_on_a_thread(qtbot) -> None:
    worker = SimulationWorker(NgspiceAdapter())
    sink = _MainThreadSink(worker)
    thread, starter = _on_thread(worker)
    try:
        with qtbot.waitSignal(sink.done, timeout=10_000):
            starter.start.emit(RC_DECK)
        result = sink.result
        assert result is not None
        v = np.interp(1e-3, result.vectors["time"], result.vectors["V(out)"])
        assert v == pytest.approx(1 - np.exp(-1), rel=1e-3)
        assert any("transient" in m.lower() for m in sink.lines), sink.lines
    finally:
        thread.quit()
        thread.wait(3000)


def test_fake_backend_is_a_backend() -> None:
    assert isinstance(FakeBackend(), SimulatorBackend)
    with pytest.raises(SimulationError):
        FakeBackend(fail_in="load").load("x")


def test_stop_before_queued_run_is_not_lost(qapp: object) -> None:
    backend = FakeBackend(block=True)
    worker = SimulationWorker(backend)
    got = _collect(worker)
    worker.arm()
    worker.stop()  # arrives before the queued run() starts
    worker.run("deck")
    assert got["failed"] == ["simulation stopped"]
    assert not backend.started.is_set()
    worker.arm()
    backend.block = False
    worker.run("deck")
    assert len(got["finished"]) == 1
