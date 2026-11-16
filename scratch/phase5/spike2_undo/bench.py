"""S2 spike measurements on IEEE 123 -> results.json (scratch only).

    python scratch/phase5/spike2_undo/bench.py

Core (no Qt): a seeded sequence of 1000 commands as deltas, deepcopy snapshots and
pickle snapshots: apply time per command, undo-all, redo-all, memory retained by the
1000 commands (tracemalloc, in a separate pass so it does not slow the timing).
Qt: a 200-event drag through QUndoStack (delta, pickle), macro vs compound delete of
the bus with the most connected elements, and 1000 actions with drags of up to 200
events through one QUndoStack.
"""

from __future__ import annotations

import copy
import gc
import json
import os
import pickle
import platform
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from commands import Command, CompoundCommand, MoveGlyph, SnapshotCommand, delete_bus  # noqa: E402
from randomseq import steps  # noqa: E402
from state import DesignState, check_consistency, load_ieee123, order  # noqa: E402

SEED = 2026
N = 1000


def wrap(cmd: Command, mode: str) -> Command:
    return cmd if mode == "delta" else SnapshotCommand(cmd, mode)


def same(a: DesignState, b: DesignState) -> bool:
    return a.grid == b.grid and a.diagram == b.diagram and order(a) == order(b)


def ms(ns: float) -> float:
    return round(ns / 1e6, 4)


def model_info(state: DesignState) -> dict[str, Any]:
    pair = (state.grid, state.diagram)
    t0 = time.perf_counter_ns()
    blob = pickle.dumps(pair, protocol=pickle.HIGHEST_PROTOCOL)
    t1 = time.perf_counter_ns()
    pickle.loads(blob)
    t2 = time.perf_counter_ns()
    copy.deepcopy(pair)
    t3 = time.perf_counter_ns()
    gc.collect()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    c = copy.deepcopy(pair)
    size = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    del c
    t4 = time.perf_counter_ns()
    state.grid == copy.deepcopy(state.grid)  # noqa: B015
    t5 = time.perf_counter_ns()
    return {
        "counts": state.grid.counts(),
        "bus_glyphs": len(state.diagram.bus_glyphs),
        "element_glyphs": len(state.diagram.element_glyphs),
        "pickle_bytes": len(blob),
        "objects_bytes_tracemalloc": size,
        "pickle_dumps_ms": ms(t1 - t0),
        "pickle_loads_ms": ms(t2 - t1),
        "deepcopy_ms": ms(t3 - t2),
        "deepcopy_plus_eq_ms": ms(t5 - t4),
    }


def core_run(base: DesignState, mode: str, gc_off: bool = False) -> dict[str, Any]:
    """``gc_off``: the timing pass runs with the cyclic garbage collector disabled, to
    show how much of the deepcopy cost is GC passes over the retained snapshots."""
    if gc_off:
        gc.disable()
    state = copy.deepcopy(base)
    start = copy.deepcopy(state)
    done: list[Command] = []
    per: list[int] = []
    kinds: dict[str, list[int]] = {}
    for step in steps(state, SEED, N):
        (cmd,) = step.commands
        cmd = wrap(cmd, mode)
        t0 = time.perf_counter_ns()
        cmd.apply(state)
        dt = time.perf_counter_ns() - t0
        per.append(dt)
        kinds.setdefault(step.kind, []).append(dt)
        done.append(cmd)
    end = copy.deepcopy(state)
    t0 = time.perf_counter_ns()
    for cmd in reversed(done):
        cmd.revert(state)
    t_undo = time.perf_counter_ns() - t0
    undo_ok = same(state, start)
    t0 = time.perf_counter_ns()
    for cmd in done:
        cmd.apply(state)
    t_redo = time.perf_counter_ns() - t0
    redo_ok = same(state, end)
    if gc_off:
        gc.enable()
        done.clear()
        gc.collect()
        return {
            "mode": f"{mode} (gc off)",
            "apply_total_ms": ms(sum(per)),
            "apply_median_ms": ms(statistics.median(per)),
            "undo_all_ms": ms(t_undo),
            "redo_all_ms": ms(t_redo),
            "undo_all_equals_start": undo_ok,
            "redo_all_equals_end": redo_ok,
        }

    # memory: a separate pass, retained = what freeing the 1000 commands releases
    state = copy.deepcopy(base)
    gc.collect()
    tracemalloc.start()
    done = []
    for step in steps(state, SEED, N):
        cmd = wrap(step.commands[0], mode)
        cmd.apply(state)
        done.append(cmd)
    gc.collect()
    m1 = tracemalloc.get_traced_memory()[0]
    done.clear()
    gc.collect()
    m2 = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    return {
        "mode": mode,
        "commands": N,
        "kinds": {k: len(v) for k, v in kinds.items()},
        "apply_total_ms": ms(sum(per)),
        "apply_median_ms": ms(statistics.median(per)),
        "apply_p95_ms": ms(sorted(per)[int(0.95 * len(per))]),
        "apply_max_ms": ms(max(per)),
        "apply_median_by_kind_ms": {k: ms(statistics.median(v)) for k, v in kinds.items()},
        "undo_all_ms": ms(t_undo),
        "redo_all_ms": ms(t_redo),
        "undo_all_equals_start": undo_ok,
        "redo_all_equals_end": redo_ok,
        "retained_by_commands_bytes": m1 - m2,
        "retained_per_command_bytes": round((m1 - m2) / N),
        "end_counts": end.grid.counts(),
    }


def qt_runs(base: DesignState) -> dict[str, Any]:
    from PySide6.QtGui import QGuiApplication, QUndoStack
    from qt_commands import UndoCommand, push_macro

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    out: dict[str, Any] = {}

    # 200-event drag
    for mode in ("delta", "pickle"):
        state = copy.deepcopy(base)
        start = copy.deepcopy(state)
        stack = QUndoStack()
        name = next(iter(state.diagram.bus_glyphs))
        x0, y0 = state.diagram.bus_glyphs[name].x, state.diagram.bus_glyphs[name].y
        per = []
        for k in range(200):
            move = MoveGlyph(f"bus.{name}", x0 + 0.1 * (k + 1), y0, gesture=1)
            cmd = UndoCommand(state, wrap(move, mode))
            t0 = time.perf_counter_ns()
            stack.push(cmd)
            per.append(time.perf_counter_ns() - t0)
        count = stack.count()
        t0 = time.perf_counter_ns()
        stack.undo()
        t_undo = time.perf_counter_ns() - t0
        out[f"drag200_{mode}"] = {
            "undo_steps": count,
            "push_total_ms": ms(sum(per)),
            "push_median_ms": ms(statistics.median(per)),
            "push_max_ms": ms(max(per)),
            "undo_ms": ms(t_undo),
            "undo_equals_start": same(state, start),
        }
        stack.clear()

    # macro vs compound: the bus with the most connected elements
    probe = copy.deepcopy(base)
    sizes = {b: len(delete_bus(probe, b).children) for b in probe.grid.buses}
    bus = max(sizes, key=lambda b: sizes[b])
    res: dict[str, Any] = {"bus": bus, "children": sizes[bus]}
    for how in ("compound", "macro"):
        t_push, t_undo, t_redo, ok = [], [], [], True
        for _ in range(20):
            state = copy.deepcopy(base)
            start = copy.deepcopy(state)
            stack = QUndoStack()
            comp: CompoundCommand = delete_bus(state, bus)
            t0 = time.perf_counter_ns()
            if how == "macro":
                push_macro(stack, state, comp)
            else:
                stack.push(UndoCommand(state, comp))
            t1 = time.perf_counter_ns()
            after = copy.deepcopy(state)
            t2 = time.perf_counter_ns()
            stack.undo()
            t3 = time.perf_counter_ns()
            ok &= same(state, start)
            t4 = time.perf_counter_ns()
            stack.redo()
            t5 = time.perf_counter_ns()
            ok &= same(state, after) and stack.count() == 1
            t_push.append(t1 - t0)
            t_undo.append(t3 - t2)
            t_redo.append(t5 - t4)
            stack.clear()
        res[how] = {
            "push_median_ms": ms(statistics.median(t_push)),
            "undo_median_ms": ms(statistics.median(t_undo)),
            "redo_median_ms": ms(statistics.median(t_redo)),
            "exact_and_one_step": ok,
        }
    out["delete_bus"] = res

    # 1000 actions, drags of 1-200 events, delta commands
    state = copy.deepcopy(base)
    start = copy.deepcopy(state)
    stack = QUndoStack()
    pushes = 0
    t_push = 0
    for step in steps(state, 11, N, max_drag_events=200):
        for c in step.commands:
            cmd = UndoCommand(state, c)
            t0 = time.perf_counter_ns()
            stack.push(cmd)
            t_push += time.perf_counter_ns() - t0
            pushes += 1
    end = copy.deepcopy(state)
    t0 = time.perf_counter_ns()
    stack.setIndex(0)
    t_undo = time.perf_counter_ns() - t0
    ok_start = same(state, start)
    t0 = time.perf_counter_ns()
    stack.setIndex(stack.count())
    t_redo = time.perf_counter_ns() - t0
    out["qundostack_1000_delta"] = {
        "actions": N,
        "pushes": pushes,
        "undo_steps": stack.count(),
        "push_total_ms": ms(t_push),
        "push_mean_ms": ms(t_push / pushes),
        "undo_all_ms": ms(t_undo),
        "redo_all_ms": ms(t_redo),
        "undo_all_equals_start": ok_start,
        "redo_all_equals_end": same(state, end),
        "consistent": check_consistency(state) == [],
    }
    # memory: a separate pass; retained = what stack.clear() releases (Python objects)
    state = copy.deepcopy(base)
    stack = QUndoStack()
    gc.collect()
    tracemalloc.start()
    for step in steps(state, 11, N, max_drag_events=200):
        for c in step.commands:
            stack.push(UndoCommand(state, c))
    gc.collect()
    m1 = tracemalloc.get_traced_memory()[0]
    stack.clear()
    gc.collect()
    m2 = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    out["qundostack_1000_delta"]["retained_by_stack_bytes"] = m1 - m2
    return out


def main() -> None:
    import PySide6

    base, t_load = load_ieee123()
    assert check_consistency(base) == []
    results: dict[str, Any] = {
        "about": {
            "made_by": "scratch/phase5/spike2_undo/bench.py",
            "python": platform.python_version(),
            "pyside6": PySide6.__version__,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "seed": SEED,
        },
        "load_s": round(t_load, 3),
        "model": model_info(base),
        "core": [core_run(base, m) for m in ("delta", "deepcopy", "pickle")]
        + [core_run(base, "deepcopy", gc_off=True)],
        "qt": qt_runs(base),
    }
    path = HERE / "results.json"
    path.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
