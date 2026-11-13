"""Spike 6: timing runs complete for every feeder. Times are printed, never asserted."""

from __future__ import annotations

import pytest
import spike6_timing

pytestmark = pytest.mark.opendss


@pytest.mark.parametrize("feeder", spike6_timing.FEEDERS)
def test_in_process_timing(feeder: str) -> None:
    r = spike6_timing.in_process(feeder, n=3)
    assert r.nodes > 0
    print(f"{feeder}: compile {r.compile_ms:.1f} ms, solve {r.solve_ms:.1f} ms")


def test_child_timing() -> None:
    c = spike6_timing.in_child("ieee123", n=1)
    assert c.total_s >= c.startup_s > 0
    print(f"ieee123 child: start-up {c.startup_s:.3f} s, total {c.total_s:.3f} s")


def test_first_import() -> None:
    imp, ctx = spike6_timing.first_import()
    assert imp > 0 and ctx > 0
    print(f"import dss {imp * 1000:.0f} ms, first context {ctx * 1000:.1f} ms")
