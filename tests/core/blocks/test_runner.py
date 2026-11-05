from __future__ import annotations

import threading
import time

import numpy as np

from core.blocks.diagram import Diagram
from core.blocks.library import builtin_specs
from core.blocks.registry import BlockRegistry
from core.blocks.runner import CANCELLED, ERROR, OK, TIMEOUT, BlockRunner, run_diagram

SQUARE = """
@block
def square(u):
    return u * u
"""

HANG = """
@block
def hang(u):
    while True:
        pass
"""

RAISES = """
@block
def boom(u):
    raise RuntimeError("kaboom")
"""


def diagram_with(code: str, name: str) -> dict:
    """Constant(3) -> <user block> -> Scope, as a dict."""
    reg = BlockRegistry(builtin_specs())
    assert reg.add_source(code, "doc").ok
    d = Diagram(reg.get)
    d.add_block("Constant", settings={"value": 3.0})
    d.add_block(name)
    d.add_block("Scope", settings={"label": "out"})
    d.add_wire("Constant1", "y", f"{name}1", "u")
    d.add_wire(f"{name}1", "y", "Scope1", "u")
    return d.to_dict()


def test_run_diagram_in_process() -> None:
    result = run_diagram(diagram_with(SQUARE, "square"), {"doc": SQUARE}, 1.0, 0.5)
    assert list(result.vectors["out"]) == [9.0, 9.0, 9.0]


def test_user_block_runs_in_child_process() -> None:
    outcome = BlockRunner(diagram_with(SQUARE, "square"), {"doc": SQUARE}, 1.0, 0.1).run()
    assert outcome.status == OK, outcome.traceback
    assert outcome.ok and outcome.result is not None
    np.testing.assert_array_equal(outcome.result.vectors["out"], np.full(11, 9.0))
    assert len(outcome.result.vectors["time"]) == 11


def test_exception_comes_back_as_traceback() -> None:
    outcome = BlockRunner(diagram_with(RAISES, "boom"), {"doc": RAISES}, 1.0, 0.1).run()
    assert outcome.status == ERROR
    assert outcome.result is None
    assert "block boom1: RuntimeError: kaboom" in outcome.message
    assert "Traceback" in outcome.traceback
    # The user's own source line is shown.
    assert 'raise RuntimeError("kaboom")' in outcome.traceback


def test_algebraic_loop_is_reported() -> None:
    reg = BlockRegistry(builtin_specs())
    d = Diagram(reg.get)
    d.add_block("Gain", block_id="g1")
    d.add_block("Gain", block_id="g2")
    d.add_wire("g1", "y", "g2", "u")
    d.add_wire("g2", "y", "g1", "u")
    outcome = BlockRunner(d.to_dict(), {}, 1.0, 0.1).run()
    assert outcome.status == ERROR
    assert outcome.message.startswith("core.blocks.engine.AlgebraicLoopError")
    assert "g1" in outcome.message and "g2" in outcome.message


def test_infinite_loop_is_stopped_by_timeout() -> None:
    runner = BlockRunner(diagram_with(HANG, "hang"), {"doc": HANG}, 1.0, 0.1, timeout=3.0)
    start = time.monotonic()
    outcome = runner.run()
    elapsed = time.monotonic() - start
    assert outcome.status == TIMEOUT
    assert "3 s timeout" in outcome.message
    assert 2.9 < elapsed < 10.0
    assert runner._process is not None and not runner._process.is_alive()


def test_cancel_from_another_thread() -> None:
    runner = BlockRunner(diagram_with(HANG, "hang"), {"doc": HANG}, 1.0, 0.1, timeout=60.0)
    runner.start()
    timer = threading.Timer(1.0, runner.cancel)
    timer.start()
    start = time.monotonic()
    outcome = runner.wait()
    timer.join()
    assert outcome.status == CANCELLED
    assert time.monotonic() - start < 10.0
    assert runner._process is not None and not runner._process.is_alive()
