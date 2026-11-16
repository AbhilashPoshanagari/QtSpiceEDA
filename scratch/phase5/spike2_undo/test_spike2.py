"""S2 spike checks: `pytest -q scratch/phase5/spike2_undo` (not part of `pytest -q`).

Exact undo: after any seeded sequence of 1000 commands, undo all gives a state equal
(== and the same dict key order) to the start, redo all equal to the end, and the
rule 21 consistency check holds after every apply and every revert.
The timing and memory numbers are bench.py.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

from commands import (  # noqa: E402
    Command,
    CompoundCommand,
    MoveGlyph,
    SetProperty,
    SnapshotCommand,
    add_load,
    delete_bus,
)
from randomseq import steps  # noqa: E402
from state import DesignState, check_consistency, load_ieee123, mini_state, order  # noqa: E402

MODES = ["delta", "deepcopy", "pickle"]


def wrap(cmd: Command, mode: str) -> Command:
    return cmd if mode == "delta" else SnapshotCommand(cmd, mode)


def assert_same(a: DesignState, b: DesignState) -> None:
    assert a.grid == b.grid
    assert a.diagram == b.diagram
    assert order(a) == order(b)


@pytest.fixture(scope="module")
def ieee123() -> DesignState:
    pytest.importorskip("dss", reason="dss-python not installed (pip install -e '.[grid]')")
    state, _ = load_ieee123()
    assert check_consistency(state) == []
    return state


def run_core(state: DesignState, mode: str, seed: int, n: int) -> None:
    start = copy.deepcopy(state)
    done: list[Command] = []
    for step in steps(state, seed, n):
        (cmd,) = step.commands
        cmd = wrap(cmd, mode)
        cmd.apply(state)
        done.append(cmd)
        assert check_consistency(state) == [], step.kind
    assert len(done) == n
    end = copy.deepcopy(state)
    assert state.grid != start.grid
    for cmd in reversed(done):
        cmd.revert(state)
        assert check_consistency(state) == []
    assert_same(state, start)
    for cmd in done:
        cmd.apply(state)
    assert_same(state, end)


# --- single commands (no dss needed) ---------------------------------------------------


def test_mini_is_consistent() -> None:
    assert check_consistency(mini_state()) == []


@pytest.mark.parametrize("mode", MODES)
def test_each_command_reverts_exactly(mode: str) -> None:
    state = mini_state()
    start = copy.deepcopy(state)
    cmds: list[Command] = [
        add_load(state, "b3", state.grid.loads["ld1"]),
        MoveGlyph("bus.b2", 40.0, 7.0, gesture=1),
        MoveGlyph("load.ld1", -3.0, 2.5, gesture=2),
        SetProperty("load.ld1", "kw", 123.4),
        SetProperty("transformer.tx1", "windings.1.tap", 1.025),
        SetProperty("line.sw1", "closed", False),  # a Switch has a "line." id
    ]
    for c in cmds:
        c = wrap(c, mode)
        before = copy.deepcopy(state)
        c.apply(state)
        assert check_consistency(state) == []
        assert state.grid != before.grid or state.diagram != before.diagram
        after = copy.deepcopy(state)
        c.revert(state)
        assert_same(state, before)
        c.apply(state)
        assert_same(state, after)
    assert "ld3" in state.grid.loads
    assert state.grid.loads["ld1"].kw == 123.4
    assert state.grid.transformers["tx1"].windings[1].tap == 1.025
    assert start.grid != state.grid


@pytest.mark.parametrize("mode", MODES)
def test_delete_bus_removes_everything_connected(mode: str) -> None:
    state = mini_state()
    start = copy.deepcopy(state)
    compound = delete_bus(state, "b2")
    ids = [c.element_id for c in compound.children]  # type: ignore[attr-defined]
    # tx1 has a winding at b2, so its regulator goes first; line l2 and l3 end at b2
    assert ids == ["regcontrol.reg1", "line.l2", "line.l3", "transformer.tx1", "bus.b2"]
    c = wrap(compound, mode)
    c.apply(state)
    assert check_consistency(state) == []
    assert "b2" not in state.grid.buses and not state.grid.regulators
    c.revert(state)
    assert_same(state, start)


def test_delta_revert_keeps_object_identity() -> None:
    state = mini_state()
    load = state.grid.loads["ld1"]
    c = delete_bus(state, "b1")
    c.apply(state)
    c.revert(state)
    assert state.grid.loads["ld1"] is load


@pytest.mark.parametrize("mode", MODES)
def test_random_1000_mini(mode: str) -> None:
    run_core(mini_state(), mode, seed=5, n=1000)


# --- IEEE 123 --------------------------------------------------------------------------


@pytest.mark.opendss
@pytest.mark.parametrize(
    "mode,seed", [("delta", 1), ("delta", 2026), ("delta", 7), ("pickle", 2026), ("deepcopy", 2026)]
)
def test_random_1000_ieee123(ieee123: DesignState, mode: str, seed: int) -> None:
    run_core(copy.deepcopy(ieee123), mode, seed=seed, n=1000)


# --- QUndoStack ------------------------------------------------------------------------


@pytest.fixture
def stack(qapp):
    from PySide6.QtGui import QUndoStack

    s = QUndoStack()
    yield s
    s.clear()


def drag(stack, state: DesignState, gid: str, gesture: int, events: int, dx: float) -> None:
    from qt_commands import UndoCommand

    for k in range(events):
        stack.push(UndoCommand(state, MoveGlyph(gid, 100.0 + dx * (k + 1) / events, 5.0, gesture)))


@pytest.mark.parametrize("mode", ["delta", "pickle"])
def test_drag_of_200_events_is_one_step(stack, mode: str) -> None:
    from qt_commands import UndoCommand

    state = mini_state()
    start = copy.deepcopy(state)
    for k in range(200):
        cmd = wrap(MoveGlyph("bus.b1", 100.0 + k, 5.0, gesture=7), mode)
        stack.push(UndoCommand(state, cmd))
    assert stack.count() == 1
    assert state.diagram.bus_glyphs["b1"].x == 299.0
    stack.undo()
    assert_same(state, start)
    stack.redo()
    assert state.diagram.bus_glyphs["b1"].x == 299.0


def test_two_drags_are_two_steps(stack) -> None:
    state = mini_state()
    drag(stack, state, "bus.b1", gesture=1, events=50, dx=10)
    drag(stack, state, "bus.b1", gesture=2, events=50, dx=20)
    assert stack.count() == 2
    stack.undo()
    assert state.diagram.bus_glyphs["b1"].x == 110.0


def test_drag_back_to_start_is_dropped(stack) -> None:
    from qt_commands import UndoCommand

    state = mini_state()
    x0, y0 = state.diagram.bus_glyphs["b1"].x, state.diagram.bus_glyphs["b1"].y
    stack.push(UndoCommand(state, MoveGlyph("bus.b1", x0 + 3, y0, gesture=1)))
    stack.push(UndoCommand(state, MoveGlyph("bus.b1", x0, y0, gesture=1)))
    assert stack.count() == 0


def test_set_property_merge_only_with_key(stack) -> None:
    from qt_commands import UndoCommand

    state = mini_state()
    for v in (1.0, 2.0, 3.0):
        stack.push(UndoCommand(state, SetProperty("load.ld1", "kw", v)))
    assert stack.count() == 3
    for v in (4.0, 5.0):
        stack.push(UndoCommand(state, SetProperty("load.ld1", "kw", v, merge_key="spin1")))
    assert stack.count() == 4
    stack.undo()
    assert state.grid.loads["ld1"].kw == 3.0


@pytest.mark.parametrize("how", ["macro", "compound"])
def test_macro_and_compound_delete(stack, how: str) -> None:
    from qt_commands import UndoCommand, push_macro

    state = mini_state()
    start = copy.deepcopy(state)
    compound: CompoundCommand = delete_bus(state, "b2")
    if how == "macro":
        push_macro(stack, state, compound)
    else:
        stack.push(UndoCommand(state, compound))
    assert stack.count() == 1
    assert check_consistency(state) == []
    after = copy.deepcopy(state)
    stack.undo()
    assert_same(state, start)
    stack.redo()
    assert_same(state, after)


@pytest.mark.opendss
@pytest.mark.parametrize("mode", ["delta", "pickle"])
def test_random_1000_through_qundostack(stack, ieee123: DesignState, mode: str) -> None:
    """1000 user actions, drags of 1-200 mouse-move events: one undo step per action
    (minus drags that end where they started, which become obsolete)."""
    from qt_commands import UndoCommand

    state = copy.deepcopy(ieee123)
    start = copy.deepcopy(state)
    expected = 0
    n_steps = 1000 if mode == "delta" else 50  # two snapshots per mouse-move: slow
    for step in steps(state, seed=11, n=n_steps, max_drag_events=200):
        first = step.commands[0]
        if isinstance(first, MoveGlyph):
            g = (
                state.diagram.bus_glyphs[first.glyph_id[4:]]
                if first.glyph_id.startswith("bus.")
                else state.diagram.element_glyphs[first.glyph_id]
            )
            last = step.commands[-1]
            assert isinstance(last, MoveGlyph)
            expected += last.new != (g.x, g.y)
        else:
            expected += 1
        for c in step.commands:
            stack.push(UndoCommand(state, wrap(c, mode)))
    assert stack.count() == expected
    end = copy.deepcopy(state)
    stack.setIndex(0)
    assert_same(state, start)
    stack.setIndex(stack.count())
    assert_same(state, end)
    assert check_consistency(state) == []
