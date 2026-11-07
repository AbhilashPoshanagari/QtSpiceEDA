"""Spike 3: Spike 1 inside a multiprocessing child, the way BlockRunner starts one.

core/blocks/runner.py uses mp.get_context("spawn"), a one-way Pipe and a daemon
Process; this spike does the same. The child loads ngspice itself, runs the ticked RC
and sends back the Spike1Result plus the tick waveform. The parent never loads ngspice.

Also checked: two children one after the other (each gets a fresh ngspice), and a
child that is terminated mid-run (the parent must not hang).

Run:  python scratch/phase3/spike3_child.py
"""

from __future__ import annotations

import multiprocessing as mp
import platform
import sys
import time
from dataclasses import dataclass

import spike1_external


@dataclass
class ChildReport:
    platform: str
    python: str
    result: spike1_external.Spike1Result
    tick_times: list[float]
    tick_out: list[float]
    child_wall_s: float  # start of Process() to result received


def _child_main(conn, ts: float, t_stop: float) -> None:
    try:
        ticks: dict[str, list[float]] = {}
        r = spike1_external.run(ts, t_stop, "rolling", False, ticks)
        conn.send(("ok", r, ticks))
    except Exception as exc:  # report, never hang the parent
        conn.send(("error", repr(exc), {}))
    finally:
        conn.close()


def run_in_child(ts: float = 100e-6, t_stop: float = 0.1, timeout: float = 300) -> ChildReport:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    started = time.perf_counter()
    proc = ctx.Process(target=_child_main, args=(child, ts, t_stop), daemon=True)
    proc.start()
    child.close()
    if not parent.poll(timeout):
        proc.terminate()
        raise TimeoutError("child did not answer")
    status, payload, ticks = parent.recv()
    wall = time.perf_counter() - started
    proc.join(10)
    if status != "ok":
        raise RuntimeError(f"child failed: {payload}")
    return ChildReport(
        platform=f"{platform.system()} {platform.release()} {platform.machine()}",
        python=sys.version.split()[0],
        result=payload,
        tick_times=ticks["t"],
        tick_out=ticks["v"],
        child_wall_s=wall,
    )


def terminate_mid_run(ts: float = 10e-6, t_stop: float = 1.0, after_s: float = 1.0) -> int:
    """Start a long child run, kill it, and return its exit code (parent must not hang)."""
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(child, ts, t_stop), daemon=True)
    proc.start()
    child.close()
    time.sleep(after_s)
    proc.terminate()
    proc.join(10)
    return proc.exitcode if proc.exitcode is not None else -999


def main() -> None:
    for ts in (100e-6, 10e-6):
        rep = run_in_child(ts, 1.0)
        r = rep.result
        print(
            f"{rep.platform} py{rep.python}  Ts={ts * 1e6:.0f}us: ticks={r.ticks} "
            f"ngspice wall={r.wall_s:.2f}s child total={rep.child_wall_s:.2f}s "
            f"max_err={r.max_err:.2e} finished={r.finished}"
        )
    again = run_in_child(100e-6, 0.1)
    print(f"second child: ticks={again.result.ticks} finished={again.result.finished}")
    print(f"terminated child exit code: {terminate_mid_run()}")


if __name__ == "__main__":
    main()
