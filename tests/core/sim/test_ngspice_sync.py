"""C1: the adapter's sync mode (external sources, breakpoints, accepted points)."""

from __future__ import annotations

import ctypes
import locale
import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import pytest

from core.sim import ngspice_adapter
from core.sim.backend import SimulationError
from core.sim.ngspice_adapter import NgspiceAdapter, SyncHandlers, tran_card
from tests.fresh import in_fresh_process

R, C = 1e3, 1e-6
TAU = R * C
T_STEP = 1e-3

RC_DECK = """sync rc
Vin in 0 dc 0 external
R1 in out 1k
C1 out 0 1u
.options interp=0
{tran}
"""

# Sharp edges make ngspice reject and redo steps (spike 2).
EDGY_DECK = """sync edges
Vin in 0 dc 0 external
R1 in out 1k
C1 out 0 1u
D2 out c dclamp
Vc c 0 0.2
Vp p 0 pulse(0 5 0.2499m 1n 1n 0.2m 0.5m)
Rp p d 10
D1 d k dmod
Ck k 0 10n
Rk k 0 1k
.model dclamp d(is=1e-15 n=0.3)
.model dmod d(is=1e-14 n=1 cjo=1p)
.options interp=0
{tran}
"""


@dataclass
class TickLog:
    ts: float
    t_stop: float
    tick_times: list[float] = field(default_factory=list)
    tick_out: list[float] = field(default_factory=list)
    data_times: list[float] = field(default_factory=list)
    time_vector: list[float] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    redo_count: int = 0
    halted_points: int = -1
    moved_while_halted: bool = True
    stop_seconds: float = math.nan
    stop_error: str = ""


class _Ticker:
    """A tick controller: a 1 V step from T_STEP on, zero-order hold."""

    def __init__(self, log: TickLog) -> None:
        self.log = log
        self.k = 0
        self.held = 0.0
        self.sim: NgspiceAdapter | None = None

    def data(self, t: float, values: dict[str, float]) -> None:
        self.log.data_times.append(t)
        tick = self.k * self.log.ts
        if abs(t - tick) > 1e-6 * self.log.ts or tick > self.log.t_stop * (1 + 1e-9):
            return
        self.log.tick_times.append(t)
        self.log.tick_out.append(values["out"])
        self.held = 1.0 if tick >= T_STEP * (1 - 1e-9) else 0.0
        self.k += 1
        assert self.sim is not None
        self.sim.set_breakpoint(self.k * self.log.ts)

    def source(self, name: str, _t: float) -> float:
        self.log.sources.add(name)
        return self.held


def _ticked(deck: str, ts: float, t_stop: float, mode: str = "run") -> TickLog:
    log = TickLog(ts, t_stop)
    ticker = _Ticker(log)
    handlers = SyncHandlers(data=ticker.data, source=ticker.source)
    sim = NgspiceAdapter(sync=True, handlers=handlers)
    ticker.sim = sim
    sim.load(deck.format(tran=tran_card(ts, t_stop)))
    sim.set_breakpoint(ts)
    if mode == "halt":
        sim.run_ticks(t_stop, ts, background=True)
        while len(log.data_times) < 20:
            time.sleep(0.001)
        assert sim.halt()
        log.halted_points = len(log.data_times)
        time.sleep(0.1)
        log.moved_while_halted = len(log.data_times) != log.halted_points
        sim.resume()
        sim.wait()
    elif mode == "stop":
        sim.run_ticks(t_stop, ts, background=True)
        while len(log.data_times) < 20:
            time.sleep(0.001)
        started = time.monotonic()
        threading.Thread(target=sim.stop).start()
        try:
            sim.wait()
        except SimulationError as err:
            log.stop_error = str(err)
        log.stop_seconds = time.monotonic() - started
        return log
    else:
        sim.run_ticks(t_stop, ts)
    result = sim.results()
    assert result is not None
    log.time_vector = list(result.vectors["time"])
    log.errors = handlers.errors
    log.redo_count = sim.redo_count
    return log


def _check_rc(log: TickLog) -> None:
    n = round(log.t_stop / log.ts) + 1
    ticks = np.asarray(log.tick_times)
    assert len(ticks) == n
    assert np.max(np.abs(ticks - np.arange(n) * log.ts)) <= 1e-9 * log.ts
    out = np.asarray(log.tick_out)
    after = ticks >= T_STEP * (1 - 1e-9)
    analytic = 1 - np.exp(-(ticks[after] - T_STEP) / TAU)
    assert np.max(np.abs(out[after] - analytic)) < 1e-4
    assert np.all(np.abs(out[~after]) < 1e-12)


@pytest.mark.ngspice
def test_external_source_follows_the_ticks() -> None:
    log = in_fresh_process(_ticked, RC_DECK, 1e-4, 5e-3)
    assert log.errors == []
    assert log.sources == {"vin"}
    _check_rc(log)
    # The run ends just after t_stop (stop when time > t_stop + Ts/2).
    assert 5e-3 < log.time_vector[-1] < 5e-3 + 1e-4


@pytest.mark.ngspice
def test_only_accepted_points_reach_the_handler() -> None:
    log = in_fresh_process(_ticked, EDGY_DECK, 1e-4, 2e-3)
    assert log.errors == []
    assert log.redo_count > 0  # the diodes and the pulse make ngspice redo steps
    # Every point the handler saw is in the final time vector, and none is missing.
    assert log.data_times == log.time_vector


@pytest.mark.ngspice
def test_halt_and_resume() -> None:
    log = in_fresh_process(_ticked, RC_DECK, 1e-4, 5e-3, "halt")
    assert log.halted_points >= 20
    assert not log.moved_while_halted
    _check_rc(log)


@pytest.mark.ngspice
def test_stop_ends_a_background_run() -> None:
    log = in_fresh_process(_ticked, RC_DECK, 1e-5, 10.0, "stop")
    assert log.stop_error == "simulation stopped"
    assert log.stop_seconds < 1.0


def _two_runs() -> tuple[TickLog, TickLog]:
    return _ticked(RC_DECK, 1e-4, 3e-3), _ticked(RC_DECK, 1e-4, 3e-3)


@pytest.mark.ngspice
def test_a_second_run_in_the_same_process() -> None:
    first, second = in_fresh_process(_two_runs)
    assert first.tick_times == second.tick_times
    np.testing.assert_allclose(first.tick_out, second.tick_out, atol=1e-12)


@pytest.mark.ngspice
def test_one_mode_per_process() -> None:
    # tests/conftest.py already set ngspice up in plain mode in this process.
    assert NgspiceAdapter().available()
    sync = NgspiceAdapter(sync=True)
    assert not sync.available()
    assert "plain mode" in sync.last_error


@pytest.mark.ngspice
def test_load_sets_the_c_numeric_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(locale, "setlocale", lambda cat, value=None: calls.append((cat, value)))
    sim = NgspiceAdapter()
    sim.load("locale\nV1 a 0 1\nR1 a 0 1k\n.op\n")
    assert (locale.LC_NUMERIC, "C") in calls


# ---- callbacks without ngspice


def _points(*values: tuple[str, float]) -> ctypes._Pointer:  # type: ignore[type-arg]
    vecs = [ngspice_adapter._VecValues(n.encode(), v, 0.0, n == "time", False) for n, v in values]
    ptrs = (ctypes.POINTER(ngspice_adapter._VecValues) * len(vecs))(
        *[ctypes.pointer(v) for v in vecs]
    )
    all_ = ngspice_adapter._VecValuesAll(len(vecs), 0, ptrs)
    all_._keep = (vecs, ptrs)  # type: ignore[attr-defined]
    return ctypes.pointer(all_)


def test_a_rejected_step_is_never_passed_on() -> None:
    seen: list[tuple[float, dict[str, float]]] = []
    sim = NgspiceAdapter(sync=True, handlers=SyncHandlers(data=lambda t, v: seen.append((t, v))))
    sim._get_sync(0, 0)
    sim._get_sync(1, 1)  # location 1, redostep 1: rejected
    sim._send_data(_points(("time", 1e-3), ("OUT", 0.5)))
    assert seen == []
    assert sim.redo_count == 1
    sim._get_sync(0, 1)  # accepted
    sim._send_data(_points(("time", 1e-3), ("OUT", 0.5)))
    assert seen == [(1e-3, {"time": 1e-3, "out": 0.5})]


def test_source_callback_returns_the_handler_value() -> None:
    handlers = SyncHandlers(source=lambda name, t: 2.5 if name == "vin" else 0.0)
    sim = NgspiceAdapter(sync=True, handlers=handlers)
    value = ctypes.c_double(0.0)
    sim._get_src(ctypes.pointer(value), 0.0, b"VIN")
    assert value.value == 2.5


def test_callback_errors_are_kept_not_raised() -> None:
    def boom(_t: float, _v: dict[str, float]) -> None:
        raise ValueError("bad")

    handlers = SyncHandlers(data=boom)
    sim = NgspiceAdapter(sync=True, handlers=handlers)
    assert sim._send_data(_points(("time", 0.0))) == 0
    assert handlers.errors == ["ValueError: bad"]
