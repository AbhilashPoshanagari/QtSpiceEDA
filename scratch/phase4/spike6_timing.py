"""Spike 6: what the UI has to wait for.

For IEEE 13, 34 and 123:
  in-process   compile, snapshot solve, reading the node voltages, and reading every
               element back (spike 4), each in a new context; median of N runs
  child        spawn + Pipe + daemon child (spike 2): start-up (until the child has
               imported dss and made a context), compile + solve + read + send, total
Also the one-off cost of `import dss` + the first context, in a fresh process.

Times are printed, never asserted (they depend on the machine).

Run:  python scratch/phase4/spike6_timing.py [N]
"""

from __future__ import annotations

import multiprocessing as mp
import statistics
import sys
import time
from dataclasses import dataclass

import common
import spike2_contexts
import spike4_api

FEEDERS = ("ieee13", "ieee34", "ieee123")


@dataclass
class InProcess:
    feeder: str
    nodes: int
    compile_ms: float
    solve_ms: float
    read_ms: float
    extract_ms: float


@dataclass
class InChild:
    feeder: str
    startup_s: float
    work_ms: float
    total_s: float


def _ms(values: list[float]) -> float:
    return statistics.median(values) * 1000.0


def in_process(feeder: str, n: int = 10) -> InProcess:
    case = common.CASES[feeder]
    compile_s, solve_s, read_s, extract_s = [], [], [], []
    nodes = 0
    for _ in range(n):
        ctx = common.new_context()
        compile_s.append(common.compile_case(ctx, case))
        solve_s.append(common.solve_case(ctx, case))
        t0 = time.perf_counter()
        nodes = len(common.read_solution(ctx, feeder).node_names)
        read_s.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        spike4_api.read_all(ctx)
        extract_s.append(time.perf_counter() - t0)
    return InProcess(feeder, nodes, _ms(compile_s), _ms(solve_s), _ms(read_s), _ms(extract_s))


def in_child(feeder: str, n: int = 3) -> InChild:
    reps = [spike2_contexts.run_in_child(feeder) for _ in range(n)]
    return InChild(
        feeder,
        statistics.median(r.to_ready_s for r in reps),
        statistics.median(r.ready_to_result_s for r in reps) * 1000.0,
        statistics.median(r.total_s for r in reps),
    )


def _first_import(conn) -> None:
    t0 = time.perf_counter()
    import dss  # noqa: F401

    t1 = time.perf_counter()
    common.new_context()
    conn.send((t1 - t0, time.perf_counter() - t1))
    conn.close()


def first_import() -> tuple[float, float]:
    """(import dss, first NewContext) in seconds, in a fresh spawned process."""
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_first_import, args=(child,), daemon=True)
    proc.start()
    child.close()
    if not parent.poll(120):
        proc.terminate()
        raise TimeoutError("no answer")
    out = parent.recv()
    proc.join(10)
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    imp, ctx = first_import()
    print(f"fresh process: import dss {imp * 1000:.0f} ms, first NewContext {ctx * 1000:.1f} ms")
    print(f"in-process, median of {n} (new context each run):")
    for f in FEEDERS:
        r = in_process(f, n)
        print(
            f"  {f:8} {r.nodes:4} nodes: compile {r.compile_ms:6.1f} ms  solve "
            f"{r.solve_ms:6.1f} ms  read V {r.read_ms:5.2f} ms  read all elements "
            f"{r.extract_ms:6.1f} ms"
        )
    print("child process (spawn + Pipe + daemon), median of 3:")
    for f in FEEDERS:
        c = in_child(f)
        print(
            f"  {f:8} start-up {c.startup_s:.3f} s  compile+solve+read+send "
            f"{c.work_ms:6.1f} ms  total {c.total_s:.3f} s"
        )
    print("(IEEE 13 solves inside `compile`; its solve column is ~0)")


if __name__ == "__main__":
    main()
