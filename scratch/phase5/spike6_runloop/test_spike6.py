"""S6 spike checks: `pytest -q scratch/phase5/spike6_runloop` (not part of `pytest -q`).

The measurements are bench.py; these pin down what the README claims about the warm
child: same results as a cold child, shutdown, and recovery after Stop, a crash, a
kill and a timeout. IEEE 13 only (fast); bench.py does the same on IEEE 123.
"""

from __future__ import annotations

import pickle
import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

import child as child_module  # noqa: E402
import warm as warm_module  # noqa: E402
from child import Request, cold_main  # noqa: E402
from feeders import config, edited, extracted  # noqa: E402
from warm import WarmGridChild  # noqa: E402

from core.blocks.process import CANCELLED, ERROR, TIMEOUT, ChildProcess  # noqa: E402
from core.grid.engines.opendss.engine import OpenDSSEngine  # noqa: E402

pytestmark = pytest.mark.skipif(not OpenDSSEngine.available(), reason="needs dss-python")


@pytest.fixture(scope="module")
def model() -> Any:
    return extracted("ieee13")[0]


def _request(model: Any, i: int, tmp: Path, **hooks: Any) -> Request:
    return Request(i, pickle.dumps(edited(model, i)), config(), str(tmp), **hooks)


def _result(outcome: Any) -> Any:
    assert outcome.ok, outcome.error
    assert outcome.message.ok, outcome.message.error
    return pickle.loads(outcome.message.result_bytes)


def _same(a: Any, b: Any) -> bool:
    import numpy as np

    return (
        a.node_names == b.node_names
        and np.array_equal(a.node_voltage, b.node_voltage)
        and a.total_losses_kw == b.total_losses_kw
        and a.metadata["regulator_taps"] == b.metadata["regulator_taps"]
    )


@pytest.mark.parametrize("reuse", [False, True], ids=["new_engine", "reuse_engine"])
def test_warm_child_gives_the_cold_results_bit_for_bit(model, tmp_path, reuse) -> None:  # type: ignore[no-untyped-def]
    cold = [
        _result(ChildProcess(cold_main, (_request(model, i, tmp_path),), timeout=60).wait())
        for i in range(3)
    ]
    child = WarmGridChild(reuse_engine=reuse)
    try:
        outcomes = [child.run(_request(model, i, tmp_path)) for i in range(3)]
        assert len({o.message.pid for o in outcomes}) == 1  # one process served all
        for outcome, expected in zip(outcomes, cold, strict=True):
            assert _same(_result(outcome), expected)
        assert not _same(cold[0], cold[1])  # the edits really change the result
    finally:
        child.stop()


def test_stop_is_clean_and_idempotent(model, tmp_path) -> None:  # type: ignore[no-untyped-def]
    child = WarmGridChild()
    _result(child.run(_request(model, 0, tmp_path)))
    proc = child.process
    assert proc is not None and proc.daemon
    assert child.stop() == 0
    assert not proc.is_alive()
    assert child.stop() is None


def test_closing_the_pipe_ends_the_child(model, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """What happens when the parent dies: the child reads EOF and exits by itself."""
    child = WarmGridChild()
    _result(child.run(_request(model, 0, tmp_path)))
    proc, conn = child.process, child._conn
    assert proc is not None and conn is not None
    conn.close()
    proc.join(5)
    assert proc.exitcode == 0


def test_stop_during_a_run_then_a_new_child(model, tmp_path) -> None:  # type: ignore[no-untyped-def]
    child = WarmGridChild()
    try:
        _result(child.run(_request(model, 0, tmp_path)))
        first = child.process
        timer = threading.Timer(0.2, child.cancel)
        timer.start()
        outcome = child.run(_request(model, 1, tmp_path, hang_s=10.0))
        assert outcome.status == CANCELLED
        assert first is not None and not first.is_alive()
        after = child.run(_request(model, 2, tmp_path))
        _result(after)
        assert after.message.pid != first.pid and child.restarts == 1
    finally:
        child.stop()


def test_crash_during_a_run_then_a_new_child(model, tmp_path) -> None:  # type: ignore[no-untyped-def]
    child = WarmGridChild()
    try:
        _result(child.run(_request(model, 0, tmp_path)))
        outcome = child.run(_request(model, 1, tmp_path, crash=True))
        assert outcome.status == ERROR
        assert outcome.error == "grid runner process exited with code 3"
        _result(child.run(_request(model, 2, tmp_path)))
        assert child.restarts == 1
    finally:
        child.stop()


def test_killed_while_idle_is_replaced_before_the_next_run(model, tmp_path) -> None:  # type: ignore[no-untyped-def]
    child = WarmGridChild()
    try:
        _result(child.run(_request(model, 0, tmp_path)))
        assert child.process is not None
        child.process.kill()
        child.process.join(5)
        _result(child.run(_request(model, 1, tmp_path)))
        assert child.restarts == 1
    finally:
        child.stop()


def test_timeout_then_a_new_child(model, tmp_path) -> None:  # type: ignore[no-untyped-def]
    child = WarmGridChild()
    try:
        _result(child.run(_request(model, 0, tmp_path)))
        outcome = child.run(_request(model, 1, tmp_path, hang_s=10.0), timeout=0.5)
        assert outcome.status == TIMEOUT
        _result(child.run(_request(model, 2, tmp_path)))
        assert child.restarts == 1
    finally:
        child.stop()


def test_no_mutable_module_state() -> None:
    """Rule "Conventions": the warm child keeps its state in the instance (parent) and
    in warm_main's locals (child), never in module globals."""
    allowed = (str, int, float, type, types.FunctionType, types.ModuleType)
    for module in (child_module, warm_module):
        for name, value in vars(module).items():
            if name.startswith("__") or name == "annotations":
                continue
            assert isinstance(value, allowed) or callable(value), f"{module.__name__}.{name}"
