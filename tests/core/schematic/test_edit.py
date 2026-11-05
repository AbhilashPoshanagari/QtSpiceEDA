"""S8: rotate, mirror, move/drag, delete, copy and paste on a selection."""

from __future__ import annotations

import json
from pathlib import Path

from core.schematic import edit
from core.schematic.connectivity import connect
from core.schematic.geometry import GROUND, INSTANCE, LABEL, WIRE
from core.schematic.model import GroundFlag, Instance, NetLabel, Schematic, Wire, pin_points
from core.symbols.asy import Symbol
from core.symbols.library import BUILTIN_DIR, SymbolLibrary

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
BUILTIN = SymbolLibrary(builtin_dir=BUILTIN_DIR)


def lookup(name: str) -> Symbol | None:
    return BUILTIN.lookup(name)[0]


def rc() -> Schematic:
    data = json.loads((FIXTURES / "schematics" / "rc" / "rc.json").read_text("utf-8"))
    return Schematic.from_dict(data)[0]


def one_res() -> Schematic:
    return Schematic(
        instances=[Instance("res", "R1", 0, 0)],
        wires=[Wire(16, 0, 16, -64), Wire(16, 96, 96, 96)],
        labels=[NetLabel(16, -64, "a")],
        grounds=[GroundFlag(96, 96)],
    )


def test_rotate_four_times_is_the_identity_and_keeps_the_grid() -> None:
    s = one_res()
    sel = edit.all_items(s)
    turned = s
    for _ in range(4):
        turned = edit.rotate(turned, lookup, sel)
        for inst in turned.instances:
            assert inst.x % 16 == 0 and inst.y % 16 == 0
    assert turned == s


def test_rotate_keeps_connections() -> None:
    s = one_res()
    turned = edit.rotate(s, lookup, edit.all_items(s))
    assert turned.instances[0].rotation == 90
    assert connect(turned, lookup).pins == connect(s, lookup).pins == [["a", "0"]]


def test_mirror_twice_is_the_identity_and_keeps_connections() -> None:
    s = rc()
    sel = edit.all_items(s)
    flipped = edit.mirror(s, lookup, sel)
    assert [i.mirror for i in flipped.instances] == [True, True, False]  # R2 was M90
    assert connect(flipped, lookup).pins == connect(s, lookup).pins
    assert edit.mirror(flipped, lookup, sel) == s


def test_rotating_only_a_part_detaches_it() -> None:
    s = one_res()
    turned = edit.rotate(s, lookup, {(INSTANCE, 0)})
    assert turned.wires == s.wires
    assert connect(turned, lookup).pins[0] != ["a", "0"]


def test_drag_keeps_wires_on_the_pins_and_move_detaches() -> None:
    s = one_res()
    dragged = edit.move(s, lookup, {(INSTANCE, 0)}, 32, 16, follow=True)
    assert dragged.instances[0].x == 32
    pin_a, pin_b = pin_points(dragged.instances[0], lookup("res"))
    assert dragged.wires[0].ends[0] == pin_a == (48, 16)  # the far end stays
    assert dragged.wires[0].ends[1] == (16, -64)
    assert dragged.wires[1].ends[0] == pin_b
    assert connect(dragged, lookup).pins == [["a", "0"]]

    moved = edit.move(s, lookup, {(INSTANCE, 0)}, 32, 16, follow=False)
    assert moved.wires == s.wires
    assert connect(moved, lookup).pins == [["", ""]]


def test_drag_of_a_wire_brings_the_wires_on_its_ends() -> None:
    s = Schematic(wires=[Wire(0, 0, 64, 0), Wire(64, 0, 64, 64), Wire(0, 0, 0, -32)])
    dragged = edit.move(s, lookup, {(WIRE, 1)}, 32, 0, follow=True)
    assert dragged.wires == [Wire(0, 0, 96, 0), Wire(96, 0, 96, 64), Wire(0, 0, 0, -32)]


def test_drag_that_shrinks_a_wire_to_nothing_removes_it() -> None:
    s = Schematic(instances=[Instance("res", "R1", 0, 0)], wires=[Wire(16, 96, 16, 128)])
    dragged = edit.move(s, lookup, {(INSTANCE, 0)}, 0, 32, follow=True)
    assert dragged.wires == []


def test_delete() -> None:
    s = rc()
    gone = edit.delete(s, {(INSTANCE, 2), (WIRE, 0), (LABEL, 1), (GROUND, 0), ("bogus", 0)})
    assert [i.name for i in gone.instances] == ["V1", "C1"]
    assert len(gone.wires) == 6 and [n.name for n in gone.labels] == ["IN"]
    assert len(gone.grounds) == 1
    assert s == rc()  # the input is not changed


def test_copy_and_paste_renames_parts_and_selects_the_copy() -> None:
    s = rc()
    clip = edit.copy_selection(s, {(INSTANCE, 2), (WIRE, 0), (LABEL, 0)})
    assert [i.name for i in clip.instances] == ["R2"] and len(clip.wires) == 1
    pasted, selection = edit.paste(s, clip, 0, 160)
    assert [i.name for i in pasted.instances] == ["V1", "C1", "R2", "R1"]
    assert pasted.instances[3].y == s.instances[2].y + 160
    assert selection == {(INSTANCE, 3), (WIRE, 7), (LABEL, 2)}
    again, _ = edit.paste(pasted, clip, 0, 320)
    assert again.instances[-1].name == "R3"


def test_free_name() -> None:
    insts = [Instance("res", "R1", 0, 0), Instance("res", "r2", 0, 0)]
    assert edit.free_name("C1", insts) == "C1"
    assert edit.free_name("R1", insts) == "R3"
    assert edit.free_name("12", [*insts, Instance("res", "12", 0, 0)]) == "U1"


def test_centre_is_on_the_grid_and_none_for_nothing() -> None:
    s = rc()
    assert edit.centre(s, lookup, set()) is None
    cx, cy = edit.centre(s, lookup, edit.all_items(s))  # type: ignore[misc]
    assert cx % 16 == 0 and cy % 16 == 0
