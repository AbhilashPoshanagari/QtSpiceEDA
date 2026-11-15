"""S6 spike: edit -> result loop, cold child per run vs warm child. -> results.json

`.venv/Scripts/python.exe scratch/phase5/spike6_runloop/bench.py` (needs `.[grid]`,
about 1 min). OpenDSS runs only in child processes.

The spawned child re-imports this file as __mp_main__, so its top level imports only
the standard library and the light spike modules; numpy/core.grid/dss are imported
in functions (and in the child, where their import is a timed stage).
"""

from __future__ import annotations

import json
import pickle
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

from child import Request, cold_main  # noqa: E402
from feeders import FEEDERS, config, edited, extracted  # noqa: E402
from warm import WarmGridChild  # noqa: E402

from core.blocks.process import ChildProcess  # noqa: E402

N = 10
CHILD_STAGES = (
    "import_core",
    "import_dss",
    "context",
    "unpickle_model",
    "write_dss",
    "write_file",
    "compile",
    "validate",
    "solve",
    "pickle_result",
)


def _stages(stamps: dict[str, float], sent: float, received: float) -> dict[str, float]:
    """Seconds per stage. ``to_child`` = parent hands over -> child target entered
    (cold: spawn + interpreter start; warm: pipe). ``back`` = child pickled the result
    -> parent has the Reply (cold: also the child's exit and join)."""
    out = {"to_child": stamps["enter"] - sent}
    prev = stamps["enter"]
    for name in CHILD_STAGES:
        if name in stamps:
            out[name] = stamps[name] - prev
            prev = stamps[name]
    out["back"] = received - prev
    return out


def _overlay_input(result: Any) -> tuple[dict[str, float], dict[str, float | None]]:
    """What D8 needs from a GridResult: min pu per bus, loading % per branch."""
    bus_pu: dict[str, float] = {}
    for name, pu in zip(result.node_names, result.node_voltage_pu, strict=True):
        bus = name.split(".")[0]
        v = float(pu)
        if bus not in bus_pu or v < bus_pu[bus]:
            bus_pu[bus] = v
    return bus_pu, {k: b.loading_pct for k, b in result.branches.items()}


def _one(send: Any, model: Any, i: int, out_dir: str) -> dict[str, Any]:
    """Edit, pickle, hand over with ``send(request) -> Reply``, unpickle, overlay input."""
    t0 = time.perf_counter()
    m = edited(model, i)
    t_edit = time.perf_counter()
    b = pickle.dumps(m, protocol=pickle.HIGHEST_PROTOCOL)
    t_pickle = time.perf_counter()
    reply = send(Request(i, b, config(), out_dir))
    t_recv = time.perf_counter()
    assert reply.ok, reply.error
    result = pickle.loads(reply.result_bytes)
    t_unpickle = time.perf_counter()
    _overlay_input(result)
    t_overlay = time.perf_counter()
    stages = {"edit": t_edit - t0, "pickle_model": t_pickle - t_edit}
    stages |= _stages(reply.stamps, t_pickle, t_recv)
    stages |= {"unpickle_result": t_unpickle - t_recv, "overlay_input": t_overlay - t_unpickle}
    return {
        "stages": stages,
        "total": t_overlay - t0,
        "result": result,
        "pid": reply.pid,
        "model_bytes": len(b),
        "result_bytes": len(reply.result_bytes),
        "script_bytes": reply.script_bytes,
    }


def _cold_send(request: Request) -> Any:
    outcome = ChildProcess(cold_main, (request,), timeout=60, name="grid runner").wait()
    assert outcome.ok, outcome.error
    return outcome.message


def _warm_send(child: WarmGridChild):  # type: ignore[no-untyped-def]
    def send(request: Request) -> Any:
        outcome = child.run(request)
        assert outcome.ok, outcome.error
        return outcome.message

    return send


def _summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    keys = list(runs[0]["stages"])
    ms = lambda xs: round(1000 * statistics.median(xs), 3)  # noqa: E731
    return {
        "runs": len(runs),
        "median_ms": {k: ms([r["stages"][k] for r in runs]) for k in keys},
        "total_median_ms": ms([r["total"] for r in runs]),
        "total_min_ms": round(1000 * min(r["total"] for r in runs), 3),
        "total_max_ms": round(1000 * max(r["total"] for r in runs), 3),
        "pids": len({r["pid"] for r in runs}),
    }


def _same(a: Any, b: Any) -> bool:
    import numpy as np

    return (
        a.node_names == b.node_names
        and np.array_equal(a.node_voltage, b.node_voltage)
        and a.total_losses_kw == b.total_losses_kw
        and a.metadata["regulator_taps"] == b.metadata["regulator_taps"]
    )


def _max_pu_diff(a: Any, b: Any) -> float:
    import numpy as np

    pa = dict(zip(a.node_names, np.nan_to_num(a.node_voltage_pu), strict=True))
    pb = dict(zip(b.node_names, np.nan_to_num(b.node_voltage_pu), strict=True))
    return max(abs(pa[n] - pb[n]) for n in pa)


def _working_set_mb(pid: int) -> float | None:
    """Working set of a process in MB (Windows tasklist; None elsewhere)."""
    import subprocess

    try:
        line = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        kb = line.rsplit('","', 1)[1].strip('"').split()[0]
        return round(int(kb.replace(",", "").replace(".", "")) / 1024, 1)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def bench_feeder(feeder: str, out_dir: str) -> dict[str, Any]:
    model, original = extracted(feeder)
    out: dict[str, Any] = {"nodes": len(original.node_names)}

    cold = [_one(_cold_send, model, i, out_dir) for i in range(N)]
    out["cold"] = _summary(cold)
    out["sizes_bytes"] = {
        "model_pickle": cold[0]["model_bytes"],
        "result_pickle": cold[0]["result_bytes"],
        "script": cold[0]["script_bytes"],
    }
    # Edit 0 is the unedited model: its re-solve against the original file's solve.
    out["edit0_vs_original_max_pu"] = _max_pu_diff(cold[0]["result"], original)
    out["edits_change_result"] = not _same(cold[0]["result"], cold[1]["result"])

    for reuse in (False, True):
        key = "warm_reuse_engine" if reuse else "warm_new_engine"
        child = WarmGridChild(reuse_engine=reuse)
        t0 = time.perf_counter()
        child.start()
        _one(_warm_send(child), model, 0, out_dir)
        first_total = time.perf_counter() - t0
        runs = [_one(_warm_send(child), model, i, out_dir) for i in range(N)]
        idle_mb = _working_set_mb(child.process.pid)  # type: ignore[union-attr]
        t_stop = time.perf_counter()
        code = child.stop()
        stop_s = time.perf_counter() - t_stop
        out[key] = _summary(runs) | {
            "first_run_ms": round(1000 * first_total, 3),
            "ready_after_spawn_ms": round(
                1000 * (child.ready_stamps["import_dss"] - child.spawned_at), 3
            ),
            "idle_working_set_mb": idle_mb,
            "stop_ms": round(1000 * stop_s, 3),
            "stop_exit_code": code,
            "same_as_cold_bitwise": all(
                _same(r["result"], c["result"]) for r, c in zip(runs, cold, strict=True)
            ),
        }

    # load_model (command by command, no file) instead of file + compile, same child.
    child = WarmGridChild(reuse_engine=True)
    lm: list[float] = []
    for i in range(N):
        req = Request(i, pickle.dumps(edited(model, i)), config(), out_dir, also_load_model=True)
        outcome = child.run(req)
        assert outcome.ok, outcome.error
        lm.append(outcome.message.extra["load_model_s"])
    child.stop()
    out["load_model_median_ms"] = round(1000 * statistics.median(lm), 3)
    return out


def bench_recovery(out_dir: str) -> dict[str, Any]:
    """IEEE 123: Stop during a run, a crash, a kill while idle; time to the next result."""
    import threading

    from core.blocks.process import CANCELLED, ERROR, TIMEOUT

    model, _ = extracted("ieee123")
    b = pickle.dumps(model)
    out: dict[str, Any] = {}
    child = WarmGridChild(reuse_engine=False)
    assert child.run(Request(0, b, config(), out_dir)).ok
    pid0 = child.process.pid  # type: ignore[union-attr]

    def timed_next(tag: str) -> None:
        t0 = time.perf_counter()
        outcome = child.run(Request(1, b, config(), out_dir))
        assert outcome.ok, outcome.error
        out[f"{tag}_next_run_ms"] = round(1000 * (time.perf_counter() - t0), 1)

    # Stop: cancel from another thread while the child is inside a run.
    timer = threading.Timer(0.2, child.cancel)
    timer.start()
    t0 = time.perf_counter()
    outcome = child.run(Request(9, b, config(), out_dir, hang_s=5.0))
    out["cancel_status"] = outcome.status
    out["cancel_returns_after_ms"] = round(1000 * (time.perf_counter() - t0 - 0.2), 1)
    assert outcome.status == CANCELLED
    timed_next("cancel")

    # Crash during a run (os._exit(3) in the child).
    outcome = child.run(Request(9, b, config(), out_dir, crash=True))
    out["crash_status"], out["crash_error"] = outcome.status, outcome.error
    assert outcome.status == ERROR
    timed_next("crash")

    # Killed while idle (e.g. by the OS); noticed before the next request is sent.
    assert child.process is not None
    child.process.kill()
    child.process.join(5)
    timed_next("killed_idle")

    # Timeout: the child does not answer within the run's timeout.
    t0 = time.perf_counter()
    outcome = child.run(Request(9, b, config(), out_dir, hang_s=5.0), timeout=0.5)
    out["timeout_status"] = outcome.status
    out["timeout_returns_ms"] = round(1000 * (time.perf_counter() - t0), 1)
    assert outcome.status == TIMEOUT
    timed_next("timeout")

    out["restarts"] = child.restarts
    out["pid_changed"] = child.process is not None and child.process.pid != pid0
    out["stop_exit_code"] = child.stop()
    return out


def main() -> None:
    import dss

    results: dict[str, Any] = {
        "versions": {
            "python": sys.version.split()[0],
            "dss_python": dss.__version__,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "runs_per_case": N,
    }
    with tempfile.TemporaryDirectory(prefix="s6_") as tmp:
        for feeder in FEEDERS:
            t0 = time.perf_counter()
            results[feeder] = bench_feeder(feeder, tmp)
            print(f"{feeder}: {time.perf_counter() - t0:.1f} s", file=sys.stderr)
        results["recovery_ieee123"] = bench_recovery(tmp)
    (HERE / "results.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
