"""C4: combined runs in the BlockRunner child process."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from core.blocks.runner import CANCELLED, ERROR, OK, TIMEOUT, BlockRunner
from core.physical.cosim import ANALYSIS
from tests.fixtures.physical.components import speed_loop, speed_loop_reference

FIXTURES = ["tests.fixtures.physical.components"]
J, B, KP, W_SET = 0.01, 0.001, 1.0, 100.0


def speed_loop_dict(**kwargs: float) -> dict:
    d, _reg = speed_loop(J=J, b=B, kp=KP, w_set=W_SET, **kwargs)
    return d.to_dict()


@pytest.mark.ngspice
def test_combined_run_in_the_child() -> None:
    ts, t_end = 1e-3, 0.1
    runner = BlockRunner(speed_loop_dict(), {}, t_end, ts, physical_modules=FIXTURES)
    outcome = runner.run()
    assert outcome.status == OK, outcome.traceback
    result = outcome.result
    assert result is not None and result.analysis == ANALYSIS
    t = result.vectors["time"]
    ticks = np.arange(round(t_end / ts) + 1) * ts
    at = np.searchsorted(t, ticks - 1e-12)
    w_ref, tau_ref = speed_loop_reference(J, B, KP, W_SET, ts, len(ticks))
    # Done when: settles to the set speed and matches the reference within 1 %.
    assert np.max(np.abs(result.vectors["w"][at] - w_ref)) < 0.01 * W_SET
    assert np.max(np.abs(result.vectors["tau"][at] - tau_ref)) < 0.01 * KP * W_SET
    assert result.vectors["w"][-1] == pytest.approx(W_SET, rel=0.01)
    assert result.units["inertia.A.w"] == "rad/s"


@pytest.mark.ngspice
def test_stop_ends_a_combined_run_within_a_second() -> None:
    # Ts = 10 us for 100 s simulated: would take minutes.
    runner = BlockRunner(
        speed_loop_dict(), {}, 100.0, 1e-5, timeout=60.0, physical_modules=FIXTURES
    )
    runner.start()
    time.sleep(1.5)  # the child is up and ngspice is running
    stopped = threading.Event()
    started = time.monotonic()
    threading.Thread(target=lambda: (runner.cancel(), stopped.set())).start()
    outcome = runner.wait()
    elapsed = time.monotonic() - started
    assert outcome.status == CANCELLED
    assert elapsed < 1.0
    assert runner._process is not None and not runner._process.is_alive()


@pytest.mark.ngspice
def test_combined_run_timeout() -> None:
    runner = BlockRunner(speed_loop_dict(), {}, 100.0, 1e-5, timeout=2.0, physical_modules=FIXTURES)
    started = time.monotonic()
    outcome = runner.run()
    assert outcome.status == TIMEOUT
    assert "2 s timeout" in outcome.message
    assert time.monotonic() - started < 4.0


def test_loop_of_two_gains_is_rejected_in_a_physical_diagram() -> None:
    d, _reg = speed_loop()
    d.add_block("Gain", block_id="g1")
    d.add_block("Gain", block_id="g2")
    d.add_wire("g1", "y", "g2", "u")
    d.add_wire("g2", "y", "g1", "u")
    outcome = BlockRunner(d.to_dict(), {}, 0.01, 1e-3, physical_modules=FIXTURES).run()
    assert outcome.status == ERROR
    assert "AlgebraicLoopError: algebraic loop through g1, g2" in outcome.message


def test_network_errors_come_back_as_a_message() -> None:
    d, _reg = speed_loop()
    d.remove_block("ref")
    outcome = BlockRunner(d.to_dict(), {}, 0.01, 1e-3, physical_modules=FIXTURES).run()
    assert outcome.status == ERROR
    assert "NetworkError:" in outcome.message
    assert "no rotational reference" in outcome.message


def test_unknown_physical_blocks_without_their_modules() -> None:
    # The built-in library does not have the fixture components.
    outcome = BlockRunner(speed_loop_dict(), {}, 0.01, 1e-3).run()
    assert outcome.status == ERROR
    assert "unknown block type" in outcome.message


@pytest.mark.ngspice
def test_combined_run_sends_back_the_ngspice_console() -> None:
    runner = BlockRunner(speed_loop_dict(), {}, 0.01, 1e-3, physical_modules=FIXTURES)
    outcome = runner.run()
    assert outcome.status == OK, outcome.traceback
    assert outcome.console, "no ngspice console lines came back"
    assert any("ngspice" in line.lower() or "circuit" in line.lower() for line in outcome.console)


def test_failed_combined_run_still_has_a_console_list() -> None:
    # Ts below 10 us is refused before ngspice starts: the list is there, but empty.
    runner = BlockRunner(speed_loop_dict(), {}, 0.01, 1e-6, physical_modules=FIXTURES)
    outcome = runner.run()
    assert outcome.status == ERROR
    assert outcome.console == []


def test_signal_only_run_has_an_empty_console() -> None:
    diagram = {
        "blocks": [
            {"id": "c", "spec": "Constant", "x": 0, "y": 0, "settings": {}},
            {"id": "s", "spec": "Scope", "x": 0, "y": 0, "settings": {}},
        ],
        "wires": [{"src": "c.y", "dst": "s.u"}],
    }
    outcome = BlockRunner(diagram, {}, 0.01, 1e-3).run()
    assert outcome.status == OK, outcome.traceback
    assert outcome.console == []
