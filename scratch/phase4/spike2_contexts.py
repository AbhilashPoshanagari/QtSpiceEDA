"""Spike 2: independent DSS contexts, and OpenDSS in a spawned child process.

1. Two engines from DSS.NewContext(): IEEE 13 in one, IEEE 34 in the other, compiled
   and solved interleaved; then a load in the IEEE 13 context is changed and re-solved.
   Each must still match its own reference, and the IEEE 34 one must not move.
   Also shows that `compile` changes the process working directory unless
   AllowChangeDir is off.
2. IEEE 13 in a child process started the way core/blocks/runner.py BlockRunner does
   (spawn context, one-way Pipe, daemon Process). The parent never imports `dss`.
3. A child terminated in the middle of IEEE 123 solves (it compiles and solves in a
   loop); the parent must not hang, and a new child must work afterwards.

Run:  python scratch/phase4/spike2_contexts.py
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from pathlib import Path

import common

# ---- 1. contexts


@dataclass
class IsolationReport:
    max_dev_13: float  # max |pu - reference| over all nodes, IEEE 13 context
    max_dev_34: float
    max_dev_34_after_13_changed: float  # against a lone IEEE 34 context solved twice
    resolve_drift_34: float  # a second Solve of IEEE 34 on its own, against the reference
    pu_671_before: float
    pu_671_after: float  # IEEE 13 context after load 671 doubled
    cwd_unchanged: bool  # AllowChangeDir=False
    cwd_after_default_compile: str  # where the process ended up with AllowChangeDir on


def isolation() -> IsolationReport:
    cwd = os.getcwd()
    a, b = common.new_context(), common.new_context()
    c13, c34 = common.CASES["ieee13"], common.CASES["ieee34"]
    common.compile_case(a, c13)
    common.compile_case(b, c34)
    common.solve_case(a, c13)
    common.solve_case(b, c34)
    r13 = common.read_solution(a, "ieee13")
    r34 = common.read_solution(b, "ieee34")
    dev13 = common.compare_to_reference(r13, common.load_reference("ieee13"))
    dev34 = common.compare_to_reference(r34, common.load_reference("ieee34"))
    cwd_ok = os.getcwd() == cwd

    # Change IEEE 13 only, re-solve it, then re-solve and re-read IEEE 34.
    common.command(a, "Load.671.kW=2310")
    a.ActiveCircuit.Solution.Solve()
    r13b = common.read_solution(a, "ieee13")
    b.ActiveCircuit.Solution.Solve()
    r34b = common.read_solution(b, "ieee34")
    # A second Solve moves IEEE 34 a little by itself (default tolerance 1e-4), so the
    # control is a lone IEEE 34 context given the same compile, solve, solve.
    lone = common.new_context()
    common.load_and_solve("ieee34", lone)
    lone.ActiveCircuit.Solution.Solve()
    r34_lone = common.read_solution(lone, "ieee34")
    dev34b = common.compare_to_reference(r34b, common.as_reference(r34_lone))
    drift34 = common.compare_to_reference(r34_lone, common.load_reference("ieee34"))

    # Default AllowChangeDir: compile moves the whole process (global state).
    c = common.new_context(allow_change_dir=True)
    common.compile_case(c, c13)
    moved = os.getcwd()
    os.chdir(cwd)
    return IsolationReport(
        max_dev_13=dev13,
        max_dev_34=dev34,
        max_dev_34_after_13_changed=dev34b,
        resolve_drift_34=drift34,
        pu_671_before=r13.pu("671.1"),
        pu_671_after=r13b.pu("671.1"),
        cwd_unchanged=cwd_ok,
        cwd_after_default_compile=str(Path(moved).relative_to(common.ROOT)),
    )


# ---- 2. child process (BlockRunner pattern)


@dataclass
class ChildReport:
    result: common.Solved
    to_ready_s: float  # Process.start() -> child has imported dss and made a context
    ready_to_result_s: float  # compile + solve + read + send
    total_s: float  # Process.start() -> result received
    exitcode: int | None


def _child_main(conn, feeder: str) -> None:
    try:
        ctx = common.new_context()
        conn.send(("ready", None))
        conn.send(("ok", common.load_and_solve(feeder, ctx)))
    except Exception as exc:  # report, never hang the parent
        conn.send(("error", repr(exc)))
    finally:
        conn.close()


def run_in_child(feeder: str = "ieee13", timeout: float = 120.0) -> ChildReport:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(child, feeder), daemon=True)
    t0 = time.perf_counter()
    proc.start()
    child.close()
    try:
        msgs, times = [], []
        while len(msgs) < 2:
            if not parent.poll(timeout):
                raise TimeoutError("child did not answer")
            msgs.append(parent.recv())
            times.append(time.perf_counter() - t0)
            if msgs[-1][0] == "error":
                raise RuntimeError(f"child failed: {msgs[-1][1]}")
    finally:
        proc.join(10)
        if proc.is_alive():
            proc.terminate()
        parent.close()
    return ChildReport(msgs[1][1], times[0], times[1] - times[0], times[1], proc.exitcode)


# ---- 3. terminate mid-solve


def _solve_forever(conn, feeder: str) -> None:
    ctx = common.new_context()
    case = common.CASES[feeder]
    n = 0
    while True:
        common.compile_case(ctx, case)
        if n == 0:
            conn.send(("solving", None))
        common.solve_case(ctx, case)
        n += 1


@dataclass
class TerminateReport:
    terminate_to_exit_s: float
    exitcode: int | None
    hung: bool


def terminate_mid_solve(feeder: str = "ieee123", run_for: float = 0.5) -> TerminateReport:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_solve_forever, args=(child, feeder), daemon=True)
    proc.start()
    child.close()
    if not parent.poll(120):
        proc.kill()
        raise TimeoutError("child never started solving")
    parent.recv()
    time.sleep(run_for)  # the child is busy in compile/solve calls all this time
    t0 = time.perf_counter()
    proc.terminate()
    proc.join(10)
    hung = proc.is_alive()
    if hung:
        proc.kill()
        proc.join(5)
    parent.close()
    return TerminateReport(time.perf_counter() - t0, proc.exitcode, hung)


def main() -> None:
    iso = isolation()
    print("1. two contexts")
    print(f"   IEEE 13 max dev from reference: {iso.max_dev_13:.2e} pu")
    print(f"   IEEE 34 max dev from reference: {iso.max_dev_34:.2e} pu")
    print(
        f"   load 671 doubled in the IEEE 13 context: 671.1 "
        f"{iso.pu_671_before:.5f} -> {iso.pu_671_after:.5f} pu"
    )
    print(
        f"   IEEE 34 re-solved after that vs a lone IEEE 34 re-solved: "
        f"{iso.max_dev_34_after_13_changed:.2e} pu"
    )
    print(f"   (a second Solve alone moves IEEE 34 by {iso.resolve_drift_34:.2e} pu)")
    print(f"   cwd unchanged with AllowChangeDir=False: {iso.cwd_unchanged}")
    print(
        f"   cwd after a compile with the default AllowChangeDir: {iso.cwd_after_default_compile}"
    )

    print("2. child process (spawn + Pipe + daemon)")
    for i in range(2):
        rep = run_in_child("ieee13")
        dev = common.compare_to_reference(rep.result, common.load_reference("ieee13"))
        print(
            f"   child {i + 1}: start-up {rep.to_ready_s:.3f} s, compile+solve+send "
            f"{rep.ready_to_result_s * 1000:.1f} ms, total {rep.total_s:.3f} s, "
            f"exit {rep.exitcode}, max dev {dev:.1e} pu"
        )

    print("3. child terminated during IEEE 123 solves")
    term = terminate_mid_solve()
    print(
        f"   exit code {term.exitcode}, terminate -> exit {term.terminate_to_exit_s * 1000:.0f} "
        f"ms, parent hung: {term.hung}"
    )
    after = run_in_child("ieee13")
    print(f"   next child afterwards: converged={after.result.converged}")


if __name__ == "__main__":
    main()
