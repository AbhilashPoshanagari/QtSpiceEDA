"""Spike 2: what ngspice reports when it rejects a step, and how ticks stay clean.

Spike 1's RC plus sharp edges ngspice must cut its step for:
  - a 1 ns PULSE through a diode into a capacitor (ngspice knows these edges),
  - a B-source tanh edge 0.1 us before a tick (ngspice does NOT know it, so a step
    that lands on the tick jumps across the edge and gets rejected),
  - a sharp diode clamp on the RC output, and the external 1 V step at 1 ms.

Every callback is logged in order:
  V  GetVSRCData(time)                        - trial points, rejected ones too
  S  GetSyncData(time, delta, olddelta, redostep, location)
  D  SendData(time)                           - accepted points
The run's final ``time`` vector is the ground truth for "accepted".

Two controllers run side by side:
  naive : ticks on the first GetVSRCData call whose time reaches k*Ts
  gated : ticks on SendData at k*Ts (accepted points only) - the one we keep
A tick is "bad" when it ran inside a step that ngspice then rejected.

``force_redo_ticks`` makes GetSyncData return 1 (ask for a redo) on the first step
that lands on those ticks - the only way found to get a rejected step exactly on a
tick (ngspice's own rejections happen right after its breakpoints, not on ticks).

Run:  python scratch/phase3/spike2_redo.py
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ngspice_sync import Handlers, SyncSession, tran_card

TS = 100e-6
T_STOP = 5e-3

DECK = """spike2 sharp edges
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
* edges ngspice has no breakpoint for, 0.1 us before a tick
Be e 0 V=2.5*(2+tanh((time-0.6999m)/1n)+tanh((time-1.0999m)/1n))
Re e f 10
D3 f g dmod
Cg g 0 10n
Rg g 0 1k
.model dclamp d(is=1e-15 n=0.3)
.model dmod d(is=1e-14 n=1 cjo=1p)
.options interp=0
{tran}
.end
"""


@dataclass
class Spike2Result:
    log: list[tuple] = field(default_factory=list)  # first keep_log entries
    accepted: list[float] = field(default_factory=list)  # final time vector
    sendata_times: list[float] = field(default_factory=list)
    sync_counts: dict[str, int] = field(default_factory=dict)  # "loc=.. redo=.." -> n
    rejects: int = 0  # GetSyncData calls with redostep = 1
    reject_time_reset: bool = False  # on every redo, time == last accepted (to 1 ulp)
    trial_times_rejected: int = 0  # distinct GetVSRCData times never accepted
    naive_ticks: list[float] = field(default_factory=list)
    naive_bad: list[float] = field(default_factory=list)
    forced: list[float] = field(default_factory=list)  # times GetSyncData returned 1
    gated_late: list[float] = field(default_factory=list)  # ticks run off k*Ts
    gated_ticks: list[float] = field(default_factory=list)
    gated_bad: list[float] = field(default_factory=list)
    finished: bool = False
    errors: list[str] = field(default_factory=list)


def _ran_in_rejected_step(log: list[tuple], i: int) -> bool:
    """True if the next D-or-redo event after log[i] is a redo (step rejected).

    A redo is either ngspice's own (GetSyncData redostep = 1) or one we asked for (F).
    """
    for e in log[i + 1 :]:
        if e[0] == "D":
            return False
        if e[0] == "F" or (e[0] == "S" and e[4] == 1):
            return True
    return False


def run(
    ts: float = TS,
    t_stop: float = T_STOP,
    keep_log: int = 4000,
    force_redo_ticks: tuple[int, ...] = (),
) -> Spike2Result:
    res = Spike2Result()
    log: list[tuple] = []
    ref: list[SyncSession] = []
    state = {"k_gated": 0, "k_naive": 0, "held": 0.0}
    naive_at: list[int] = []
    tol = 1e-3 * ts

    def due(k: int, t: float) -> bool:
        return k * ts - tol <= t and k * ts <= t_stop + tol

    def vsrc(t: float, _name: str) -> float:
        log.append(("V", t))
        if due(state["k_naive"], t):
            res.naive_ticks.append(t)
            naive_at.append(len(log) - 1)
            state["k_naive"] += 1
        return state["held"]

    def sync(t: float, delta: float, old: float, redo: int, loc: int):
        log.append(("S", t, delta, old, redo, loc))
        k = round(t / ts)
        if (
            loc == 1
            and redo == 0
            and k in force_redo_ticks
            and abs(t - k * ts) < tol
            and k not in {round(f / ts) for f in res.forced}
        ):
            res.forced.append(t)
            log.append(("F", t))
            return 1, None
        return 0, None

    def data(t: float, _values: dict[str, float], _index: int) -> None:
        log.append(("D", t))
        res.sendata_times.append(t)
        if due(state["k_gated"], t):
            res.gated_ticks.append(t)
            if abs(t - state["k_gated"] * ts) > tol:
                res.gated_late.append(t)
            state["held"] = 1.0 if t >= 1e-3 - tol else 0.0
            state["k_gated"] += 1
            nxt = state["k_gated"] * ts
            if nxt <= t_stop + tol:
                ref[0].set_breakpoint(nxt)

    h = Handlers(vsrc=vsrc, sync=sync, data=data)
    s = SyncSession(h)
    ref.append(s)
    s.load(DECK.format(tran=tran_card(ts, t_stop)))
    s.set_breakpoint(ts)
    s.run_ticks(t_stop, ts)

    accepted = s.vector("time")
    res.accepted = accepted.tolist()
    acc_set = set(res.accepted)
    res.trial_times_rejected = len({e[1] for e in log if e[0] == "V"} - acc_set)
    res.naive_bad = [
        res.naive_ticks[j] for j, i in enumerate(naive_at) if _ran_in_rejected_step(log, i)
    ]
    res.gated_bad = [t for t in res.gated_ticks if t not in acc_set]

    counts: Counter[str] = Counter()
    last_accepted = 0.0
    reset_ok = True
    for e in log:
        if e[0] == "D":
            last_accepted = e[1]
        elif e[0] == "S":
            counts[f"loc={e[5]} redo={e[4]}"] += 1
            if e[4] == 1 and abs(e[1] - last_accepted) > 1e-12 * max(abs(e[1]), 1e-30):
                reset_ok = False
    res.sync_counts = dict(counts)
    res.rejects = sum(n for k, n in counts.items() if k.endswith("redo=1"))
    res.reject_time_reset = reset_ok
    res.log = log[:keep_log]
    res.finished = s.finished()
    res.errors = h.errors[:5]
    return res


def excerpt_around_bad_naive(res: Spike2Result, before: int = 6, after: int = 8) -> list[tuple]:
    """The log around the first naive tick that ran inside a rejected step."""
    if not res.naive_bad:
        return []
    t_bad = res.naive_bad[0]
    for i, e in enumerate(res.log):
        if e[0] == "V" and e[1] == t_bad:
            return res.log[max(0, i - before) : i + after]
    return []


def main() -> None:
    from fresh import in_fresh_process

    cases = ((100e-6, 5e-3, ()), (10e-6, 5e-3, ()), (100e-6, 2e-3, (7, 11)))
    for ts, t_stop, force in cases:
        r = in_fresh_process(run, ts, t_stop, 200_000, force)
        print(f"== Ts = {ts * 1e6:.0f} us, {t_stop * 1e3:.0f} ms, forced redo at ticks {force}")
        print(f"accepted points          : {len(r.accepted)}")
        print(f"SendData times == accepted time vector: {r.sendata_times == r.accepted}")
        print(f"GetSyncData calls        : {r.sync_counts}")
        print(
            f"rejected steps (redo=1)  : {r.rejects}; time reset to last accepted on "
            f"every redo: {r.reject_time_reset}"
        )
        print(f"GetVSRCData trial times never accepted: {r.trial_times_rejected}")
        print(
            f"naive ticks {len(r.naive_ticks)}, inside rejected steps: {len(r.naive_bad)} "
            f"{[f'{t:.6g}' for t in r.naive_bad[:4]]}"
        )
        print(
            f"gated ticks {len(r.gated_ticks)}, not accepted: {len(r.gated_bad)}, "
            f"late (off k*Ts): {[f'{t:.9g}' for t in r.gated_late]}"
        )
        print(f"finished: {r.finished} {r.errors}")
        ex = excerpt_around_bad_naive(r)
        if ex:
            print("log around the first naive tick inside a rejected step:")
        for e in ex:
            if e[0] == "F":
                print(f"  F t={e[1]:.12e}   <- GetSyncData returned 1 (redo)")
            elif e[0] == "S":
                print(f"  S t={e[1]:.12e} delta={e[2]:.3e} old={e[3]:.3e} redo={e[4]} loc={e[5]}")
            else:
                print(f"  {e[0]} t={e[1]:.12e}")


if __name__ == "__main__":
    main()
