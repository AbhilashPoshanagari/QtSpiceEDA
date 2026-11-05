from __future__ import annotations

import math
import threading

import numpy as np
import pytest

from core.sim import ngspice_adapter
from core.sim.backend import SimulationError
from core.sim.ngspice_adapter import NgspiceAdapter, display_name

# 1 kOhm / 1 uF low-pass driven by a 1 V step (1 ns rise). Time constant RC = 1 ms.
# ngspice rejects a zero TSTEP, so the card uses 1u rather than ".tran 0 5m".
RC_DECK = """RC step response
V1 in 0 PULSE(0 1 0 1n 1n 1 2)
R1 in out 1k
C1 out 0 1u
.tran 1u 5m
.end
"""


@pytest.fixture
def adapter() -> NgspiceAdapter:
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    return sim


@pytest.mark.ngspice
def test_rc_step_at_one_time_constant(adapter: NgspiceAdapter) -> None:
    adapter.load(RC_DECK)
    adapter.run()
    result = adapter.results()

    assert result is not None
    assert result.analysis == "tran"
    assert result.axis == "time"
    assert result.units["V(out)"] == "V"
    v_1ms = np.interp(1e-3, result.vectors["time"], result.vectors["V(out)"])
    assert v_1ms == pytest.approx(1 - math.exp(-1), rel=0.005)  # 0.632 V +/- 0.5 %


@pytest.mark.ngspice
def test_console_lines_are_collected(adapter: NgspiceAdapter) -> None:
    lines: list[str] = []
    adapter.on_message = lines.append
    adapter.load(RC_DECK)
    adapter.run()
    assert any("rc step response" in line.lower() for line in adapter.console)
    assert lines == adapter.console


@pytest.mark.ngspice
def test_bad_deck_raises_simulation_error(adapter: NgspiceAdapter) -> None:
    with pytest.raises(SimulationError):
        adapter.load("bad\nV1 in 0 1\nR1 in out 1k\nC1 out 0 1u\n.tran 0 5m\n.end")


@pytest.mark.ngspice
def test_stop_cancels_long_run(adapter: NgspiceAdapter) -> None:
    adapter.load("long\nV1 in 0 SIN(0 1 1k)\nR1 in out 1k\nC1 out 0 1u\n.tran 1u 5\n.end")
    timer = threading.Timer(0.3, adapter.stop)
    timer.start()
    with pytest.raises(SimulationError, match="stopped"):
        adapter.run()
    timer.cancel()


def test_missing_library_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.sim.ngspice_adapter.library_candidates", lambda: ["definitely-not-ngspice.dll"]
    )
    monkeypatch.setattr(ngspice_adapter, "_SESSION", ngspice_adapter._Session())
    sim = NgspiceAdapter()
    assert sim.available() is False
    assert sim.last_error
    with pytest.raises(SimulationError):
        sim.load(RC_DECK)


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        ("out", "V", "V(out)"),
        ("v1#branch", "A", "I(v1)"),
        ("time", "s", "time"),
        # S11: device currents from ".options savecurrents"
        ("@r1[i]", "A", "I(r1)"),
        ("@c1[i]", "A", "I(c1)"),
        ("@i1[current]", "A", "I(i1)"),
        ("@d1[id]", "A", "I(d1)"),
        ("@q1[ic]", "A", "Ic(q1)"),
        ("@q1[ie]", "A", "Ie(q1)"),
        ("@m1[id]", "A", "Id(m1)"),
        ("@m1[ib]", "A", "Ib(m1)"),
        ("@q1[gm]", "S", "@q1[gm]"),
    ],
)
def test_display_name(raw: str, unit: str, expected: str) -> None:
    assert display_name(raw, unit) == expected


@pytest.mark.ngspice
def test_several_adapters_share_one_ngspice() -> None:
    first, second = NgspiceAdapter(), NgspiceAdapter()
    assert first.available() and second.available()
    first.load(RC_DECK)
    lines_before = len(first.console)
    second.load(RC_DECK)  # second now owns the callbacks
    with pytest.raises(SimulationError, match="no deck"):
        first.run()
    second.run()
    assert second.results() is not None
    assert len(first.console) == lines_before  # output of second's run went to second only
    assert any("transient" in line.lower() for line in second.console)


@pytest.mark.ngspice
def test_stop_before_run_is_not_lost(adapter: NgspiceAdapter) -> None:
    adapter.load(RC_DECK)
    adapter.stop()
    with pytest.raises(SimulationError, match="stopped"):
        adapter.run()
    adapter.load(RC_DECK)  # a new load clears the request
    adapter.run()
    assert adapter.results() is not None


# ---- L3: compatibility mode (Spike A)

# PSpice if() in a B-source: "no such function" unless ngbehavior includes ps.
PSPICE_IF_DECK = """PSpice if() needs compatibility mode
V1 in 0 1
B1 out 0 V=if(V(in)>0.5, 2, 0)
R1 out 0 1k
.op
.end
"""


@pytest.mark.parametrize(
    ("mode", "command"),
    [("ltpsa", "set ngbehavior=ltpsa"), ("", "unset ngbehavior"), ("psa", "set ngbehavior=psa")],
)
def test_compat_command(mode: str, command: str) -> None:
    assert ngspice_adapter.compat_command(mode) == command


@pytest.mark.parametrize("mode", ["lt psa", "ltpsa;quit", "LTPSA", "x" * 17, None])
def test_compat_command_rejects_bad_modes(mode: object) -> None:
    with pytest.raises(SimulationError, match="compatibility mode"):
        ngspice_adapter.compat_command(mode)  # type: ignore[arg-type]


def test_default_compat_mode() -> None:
    assert NgspiceAdapter().compat_mode == "ltpsa"


@pytest.mark.ngspice
def test_compat_mode_on_runs_pspice_if(adapter: NgspiceAdapter) -> None:
    adapter.load(PSPICE_IF_DECK)
    adapter.run()
    result = adapter.results()
    assert result is not None
    assert result.vectors["V(out)"][0] == pytest.approx(2.0)
    assert any("Compatibility modes selected" in line for line in adapter.console)


@pytest.mark.ngspice
def test_compat_mode_off_rejects_pspice_if_then_on_again(adapter: NgspiceAdapter) -> None:
    adapter.compat_mode = ""
    with pytest.raises(SimulationError):
        adapter.load(PSPICE_IF_DECK)
        adapter.run()
    # Applied per load: switching it back on works without restarting ngspice.
    adapter.compat_mode = "ltpsa"
    adapter.load(PSPICE_IF_DECK)
    adapter.run()
    result = adapter.results()
    assert result is not None
    assert result.vectors["V(out)"][0] == pytest.approx(2.0)
