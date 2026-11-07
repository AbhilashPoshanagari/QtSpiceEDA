"""Spike 1: an `external` voltage source driven from Python at a fixed tick Ts.

RC low-pass (R = 1 k, C = 1 u, tau = 1 ms). Vin is declared `dc 0 external`; its
value comes from GetVSRCData. A Python "controller" runs once per accepted tick
k*Ts, reads V(out) and sets Vin = 1 V from t = 1 ms on (zero-order hold).

Ticks are detected from SendData, which ngspice calls only for accepted time points
(see spike 2). Breakpoints are either all set before the run ("preset") or one at a
time from the controller ("rolling"); "none" + clamp lands on the ticks by shortening
delta in GetSyncData instead, for comparison. The run ends with ``run_ticks``.

Run:  python scratch/phase3/spike1_external.py
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

import numpy as np
from ngspice_sync import Handlers, SyncSession, tran_card

R, C = 1e3, 1e-6
TAU = R * C
T_STEP = 1e-3

DECK = """spike1 rc with external source
Vin in 0 dc 0 external
R1 in out {r}
C1 out 0 {c}
.options interp=0
{tran}
.end
"""

BASELINE_DECK = """spike1 rc baseline (built-in source, no callbacks)
Vin in 0 pwl(0 0 {t1} 0 {t2} 1)
R1 in out {r}
C1 out 0 {c}
.tran {ts} {tstop} 0 {ts}
.end
"""


@dataclass
class Spike1Result:
    ts: float
    t_stop: float
    bkpt: str  # "rolling" | "preset" | "none"
    clamp: bool  # GetSyncData shortens delta to land on the next tick
    wall_s: float
    ticks: int
    missed_ticks: int  # ticks with no accepted point within 1e-3*Ts
    max_land_err: float  # max |t_accepted - k*Ts| over the ticks
    points: int
    vsrc_calls: int
    sync_calls: int
    max_err: float  # max |V(out) - analytic| at the ticks after the step
    first_high_tick: float  # time of the first tick that set Vin = 1
    finished: bool
    errors: list[str]

    @property
    def us_per_tick(self) -> float:
        return 1e6 * self.wall_s / max(self.ticks, 1)


def clamp_delta(t: float, delta: float, k_next: int, ts: float) -> float | None:
    """GetSyncData location 0: shorten the step so it lands on the next tick.

    Only used to compare against SetBkpt; breakpoints turn out to be what matters.
    """
    tol = 1e-3 * ts
    target = k_next * ts
    if target <= t + tol:  # already on this tick; aim at the next one
        target += ts
    remaining = target - t
    if 0 < remaining <= delta * (1 + 1e-9):
        return remaining
    return None


class TickController:
    """The fixed-step Python side: runs once per accepted tick, holds its output."""

    def __init__(
        self, session_ref: list[SyncSession], ts: float, t_stop: float, bkpt: str, clamp: bool
    ) -> None:
        self.ts = ts
        self.t_stop = t_stop
        self.bkpt = bkpt
        self.clamp = clamp
        self.tol = 1e-3 * ts
        self.session_ref = session_ref
        self.k_next = 0
        self.held = 0.0
        self.tick_times: list[float] = []
        self.tick_out: list[float] = []
        self.land_err: list[float] = []
        self.missed = 0
        self.vsrc_calls = 0
        self.sync_calls = 0
        self.first_high = math.nan

    # GetVSRCData: may be called for trial points that are later rejected,
    # so it only ever returns the held value.
    def vsrc(self, _t: float, _name: str) -> float:
        self.vsrc_calls += 1
        return self.held

    # GetSyncData: location 0 is "about to take a step of delta from time t".
    def sync(self, t: float, delta: float, _old: float, _redo: int, loc: int):
        self.sync_calls += 1
        if loc != 0 or not self.clamp:
            return 0, None
        return 0, clamp_delta(t, delta, self.k_next, self.ts)

    # SendData: accepted points only.
    def data(self, t: float, values: dict[str, float], _index: int) -> None:
        tick_t = self.k_next * self.ts
        if t < tick_t - self.tol or tick_t > self.t_stop + self.tol:
            return
        if t > tick_t + self.tol:
            self.missed += 1  # stepped over the tick: controller runs late
        # One tick: read sensors -> run controller once -> set source (held).
        self.tick_times.append(t)
        self.land_err.append(abs(t - tick_t))
        self.tick_out.append(values.get("v(out)", values.get("out", math.nan)))
        u = 1.0 if tick_t >= T_STEP - self.tol else 0.0
        if u and math.isnan(self.first_high):
            self.first_high = t
        self.held = u
        self.k_next += 1
        if self.bkpt == "rolling":
            nxt = self.k_next * self.ts
            if nxt <= self.t_stop + self.tol:
                self.session_ref[0].set_breakpoint(nxt)


def run(
    ts: float,
    t_stop: float = 1.0,
    bkpt: str = "rolling",
    clamp: bool = False,
    ticks_out: dict[str, list[float]] | None = None,
) -> Spike1Result:
    """Run the ticked RC; ``ticks_out`` (if given) receives {"t": [...], "v": [...]}."""
    ref: list[SyncSession] = []
    ctl = TickController(ref, ts, t_stop, bkpt, clamp)
    h = Handlers(vsrc=ctl.vsrc, sync=ctl.sync, data=ctl.data)
    s = SyncSession(h)
    ref.append(s)
    s.load(DECK.format(r=R, c=C, tran=tran_card(ts, t_stop)))
    if bkpt == "preset":
        n = int(round(t_stop / ts))
        for k in range(1, n + 1):
            s.set_breakpoint(k * ts)
    elif bkpt == "rolling":
        s.set_breakpoint(ts)
    started = time.perf_counter()
    s.run_ticks(t_stop, ts)
    wall = time.perf_counter() - started

    if ticks_out is not None:
        ticks_out["t"] = list(ctl.tick_times)
        ticks_out["v"] = list(ctl.tick_out)
    tt = np.asarray(ctl.tick_times)
    vo = np.asarray(ctl.tick_out)
    # The source goes high at the first tick at/after 1 ms and holds from there.
    t0 = ctl.first_high
    after = tt > t0
    analytic = 1.0 - np.exp(-(tt[after] - t0) / TAU)
    max_err = float(np.max(np.abs(vo[after] - analytic))) if after.any() else math.nan
    finished = s.finished()
    errors = (
        h.errors[:5]
        + [ln for ln in h.console if ln.startswith("stderr") and "spinit" not in ln][:5]
    )
    return Spike1Result(
        ts=ts,
        t_stop=t_stop,
        bkpt=bkpt,
        clamp=clamp,
        wall_s=wall,
        ticks=len(tt),
        missed_ticks=ctl.missed,
        max_land_err=max(ctl.land_err, default=math.nan),
        points=len(s.vector("time")),
        vsrc_calls=ctl.vsrc_calls,
        sync_calls=ctl.sync_calls,
        max_err=max_err,
        first_high_tick=t0,
        finished=finished,
        errors=errors,
    )


def run_baseline(ts: float, t_stop: float = 1.0) -> float:
    """Wall time of the same RC with a built-in PWL source and no Python callbacks."""
    h = Handlers()
    s = SyncSession(h, want_data=False)
    s.load(BASELINE_DECK.format(r=R, c=C, ts=ts, tstop=t_stop, t1=T_STEP, t2=T_STEP + 1e-9))
    started = time.perf_counter()
    s.run()
    return time.perf_counter() - started


def as_dict(r: Spike1Result) -> dict:
    d = asdict(r)
    d["us_per_tick"] = r.us_per_tick
    return d


def main() -> None:
    import multiprocessing as mp
    import sys

    from fresh import in_fresh_process

    mp.freeze_support()
    steps = [float(a) * 1e-6 for a in sys.argv[1:]] or [100e-6, 50e-6, 20e-6, 10e-6]
    print(
        f"{'Ts':>7} {'bkpt':>8} {'clamp':>5} {'wall s':>7} {'us/tick':>8} {'ticks':>7} "
        f"{'missed':>6} {'points':>7} {'vsrc':>8} {'max err':>9} ok"
    )
    for ts in steps:
        for bkpt, clamp in (("rolling", False), ("preset", False), ("none", True)):
            r = in_fresh_process(run, ts, 1.0, bkpt, clamp)
            print(
                f"{ts * 1e6:5.0f}us {bkpt:>8} {clamp!s:>5} {r.wall_s:7.2f} "
                f"{r.us_per_tick:8.1f} {r.ticks:7d} {r.missed_ticks:6d} {r.points:7d} "
                f"{r.vsrc_calls:8d} {r.max_err:9.2e} {r.finished}",
                flush=True,
            )
        base = in_fresh_process(run_baseline, ts, 1.0)
        print(f"{ts * 1e6:5.0f}us baseline (PWL source, no callbacks) {base:7.2f} s", flush=True)


if __name__ == "__main__":
    main()
