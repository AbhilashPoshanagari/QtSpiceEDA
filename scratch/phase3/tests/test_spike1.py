"""Spike 1: external source driven at a fixed tick.

Each spike runs in a fresh spawned process (ngspice is global per
process). Timings are printed, never asserted."""

from __future__ import annotations

import pytest
import spike1_external
from fresh import in_fresh_process

pytestmark = pytest.mark.ngspice


def test_spike1_step_reaches_rc_at_1ms_and_matches_exponential() -> None:
    r = in_fresh_process(spike1_external.run, 100e-6, 0.02)
    assert r.finished, r.errors
    assert r.ticks == 201  # k = 0 .. 200
    assert r.missed_ticks == 0
    assert r.max_land_err < 1e-12
    assert r.first_high_tick == pytest.approx(1e-3, abs=1e-12)
    assert r.max_err < 1e-4  # V(out) vs 1 - exp(-(t - 1 ms) / RC)
    print(f"Ts=100us 20ms: {r.wall_s:.3f}s, {r.us_per_tick:.1f} us/tick")


def test_spike1_runs_at_ts_10us() -> None:
    r = in_fresh_process(spike1_external.run, 10e-6, 0.01)
    assert r.finished, r.errors
    assert r.ticks == 1001
    assert r.missed_ticks == 0
    assert r.max_err < 1e-4
    print(f"Ts=10us 10ms: {r.wall_s:.3f}s, {r.us_per_tick:.1f} us/tick")


def test_spike1_preset_breakpoints_give_the_same_answer() -> None:
    rolling = in_fresh_process(spike1_external.run, 100e-6, 0.01, "rolling")
    preset = in_fresh_process(spike1_external.run, 100e-6, 0.01, "preset")
    assert preset.finished and rolling.finished
    assert preset.points == rolling.points
    assert preset.max_err == pytest.approx(rolling.max_err, rel=1e-9)


def test_spike1_without_breakpoints_the_source_edge_is_inaccurate() -> None:
    # Landing on the ticks by shortening delta is not enough: ngspice must know the
    # tick is a breakpoint so it restarts integration there.
    clamped = in_fresh_process(spike1_external.run, 100e-6, 0.01, "none", True)
    rolling = in_fresh_process(spike1_external.run, 100e-6, 0.01, "rolling")
    assert clamped.missed_ticks == 0
    assert clamped.max_err > 100 * rolling.max_err
