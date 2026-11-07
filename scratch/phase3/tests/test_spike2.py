"""Spike 2: rejected steps never reach the controller.

Each spike runs in a fresh spawned process (ngspice is global per
process). Timings are printed, never asserted."""

from __future__ import annotations

import pytest
import spike2_redo
from fresh import in_fresh_process

pytestmark = pytest.mark.ngspice


def test_spike2_senddata_is_exactly_the_accepted_points() -> None:
    r = in_fresh_process(spike2_redo.run, 100e-6, 5e-3, 0)
    assert r.finished, r.errors
    assert r.rejects > 0  # the sharp edges really make ngspice redo steps
    assert r.sync_counts["loc=1 redo=1"] == r.rejects
    assert r.reject_time_reset  # on a redo, time is back at the last accepted point
    assert r.trial_times_rejected > 0  # GetVSRCData does see rejected trial points
    assert r.sendata_times == r.accepted
    assert r.gated_bad == []
    assert len(r.gated_ticks) == 51


def test_spike2_rejected_step_on_a_tick_never_reaches_the_gated_controller() -> None:
    r = in_fresh_process(spike2_redo.run, 100e-6, 2e-3, 0, (7, 11))
    assert r.finished, r.errors
    assert len(r.forced) == 2
    assert len(r.naive_bad) == 2  # a GetVSRCData-driven controller ran on rejected steps
    assert r.gated_bad == []  # the SendData-driven one never did
    # Finding: after a forced redo ngspice steps over the tick breakpoint, so the
    # gated tick runs late (never return nonzero from GetSyncData).
    assert len(r.gated_late) == 2
