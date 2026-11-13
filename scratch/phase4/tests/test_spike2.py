"""Spike 2: contexts do not affect each other; OpenDSS runs in a spawned child and a
child killed mid-solve does not hang the parent. Timings are printed, never asserted."""

from __future__ import annotations

import os

import common
import pytest
import spike2_contexts

pytestmark = pytest.mark.opendss


def test_two_contexts_are_independent() -> None:
    rep = spike2_contexts.isolation()
    assert rep.max_dev_13 < 1e-8
    assert rep.max_dev_34 < 1e-8
    assert rep.pu_671_after < rep.pu_671_before - 1e-3  # the change took effect in 13
    assert rep.max_dev_34_after_13_changed == 0.0  # ... and not in 34
    print(f"second Solve of IEEE 34 alone moves it by {rep.resolve_drift_34:.2e} pu")
    assert rep.cwd_unchanged


def test_default_compile_changes_process_cwd() -> None:
    cwd = os.getcwd()
    rep = spike2_contexts.isolation()
    assert os.path.normpath(rep.cwd_after_default_compile) == os.path.join(
        "tests", "fixtures", "grid", "ieee13"
    )
    assert os.getcwd() == cwd


def test_child_matches_reference() -> None:
    rep = spike2_contexts.run_in_child("ieee13")
    assert rep.exitcode == 0
    assert common.compare_to_reference(rep.result, common.load_reference("ieee13")) < 1e-8
    print(f"child start-up {rep.to_ready_s:.3f} s, total {rep.total_s:.3f} s")


def test_terminated_child_does_not_hang_parent() -> None:
    rep = spike2_contexts.terminate_mid_solve(run_for=0.3)
    assert not rep.hung
    assert rep.exitcode not in (0, None)
    assert spike2_contexts.run_in_child("ieee13").result.converged
