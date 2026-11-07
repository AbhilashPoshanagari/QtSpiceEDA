"""Spike 3: Spike 1 inside a spawned child process.

Each spike runs in a fresh spawned process (ngspice is global per
process). Timings are printed, never asserted."""

from __future__ import annotations

import pytest
import spike1_external
import spike3_child
from fresh import in_fresh_process

pytestmark = pytest.mark.ngspice


def test_spike3_child_matches_fresh_process_run() -> None:
    rep = spike3_child.run_in_child(100e-6, 0.02)
    ref = in_fresh_process(spike1_external.run, 100e-6, 0.02)
    assert rep.result.finished
    assert rep.result.ticks == ref.ticks == len(rep.tick_out)
    assert rep.result.max_err == ref.max_err
    print(f"{rep.platform}: child total {rep.child_wall_s:.2f}s")


def test_spike3_second_child_and_terminated_child() -> None:
    assert spike3_child.run_in_child(100e-6, 0.005).result.ticks == 51
    code = spike3_child.terminate_mid_run(10e-6, 1.0, 0.5)
    assert code != 0  # killed, and the parent did not hang
    assert spike3_child.run_in_child(100e-6, 0.005).result.ticks == 51
