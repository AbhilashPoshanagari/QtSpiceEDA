"""S5-S8: SchematicController's editor side (models, selection, undo, edits)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from bridge.schematic_controller import SchematicController, SyncModel
from core.schematic.connectivity import connect
from core.schematic.model import Instance, Schematic, TextItem, Wire

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def rc() -> Schematic:
    data = json.loads((FIXTURES / "schematics" / "rc" / "rc.json").read_text("utf-8"))
    return Schematic.from_dict(data)[0]


@pytest.fixture
def ctrl(qapp: object, tmp_path: Path) -> SchematicController:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    c = SchematicController(settings=settings)
    c.load_schematic(rc())
    return c


def check_undo_redo(ctrl: SchematicController, action) -> Schematic:
    """Run ``action``; one undo gives back the sheet before, one redo the sheet after."""
    before = ctrl.schematic()
    steps = ctrl.undo_stack().count()
    action()
    after = ctrl.schematic()
    assert after != before, "the action changed nothing"
    assert ctrl.undo_stack().count() == steps + 1, "one action = one undo step"
    ctrl.undo()
    assert ctrl.schematic() == before
    ctrl.redo()
    assert ctrl.schematic() == after
    return after


# ---------------------------------------------------------------- S5: models


def test_load_fills_the_models(ctrl: SchematicController) -> None:
    rows = ctrl.instances.rows()
    assert [r["instName"] for r in rows] == ["V1", "C1", "R2"]
    r2 = rows[2]
    assert (r2["posX"], r2["posY"], r2["matrix"]) == (144, 96, [0, 1, 1, 0])
    assert r2["value"] == "10k" and r2["missing"] is False
    assert ctrl.wires.count == 7
    assert [(f["kind"], f["name"]) for f in ctrl.flags.rows()] == [
        ("label", "IN"),
        ("label", "OUT"),
        ("ground", "0"),
        ("ground", "0"),
    ]
    assert ctrl.property("wirePath").startswith("M 144 112 L 64 112 M 304 112")
    assert ctrl.property("junctions") == []
    assert ctrl.property("texts") == [
        {
            "index": 0,
            "posX": -96,
            "posY": 176,
            "text": ".ac dec 100 1 1MEG",
            "directive": True,
            "selected": False,
        }
    ]
    assert ctrl.property("canUndo") is False


def test_symbol_geometry_is_cached_and_missing_symbols_draw_a_box(
    ctrl: SchematicController,
) -> None:
    res = ctrl.symbolGeometry("res")
    assert res["path"].startswith("M 16 0") and len(res["pins"]) == 2
    assert ctrl.symbolGeometry("res") == res
    missing = ctrl.symbolGeometry("nosuch")
    assert missing["missing"] is True and missing["path"].startswith("M -16 -16")


def test_hit_testing_and_snapping(ctrl: SchematicController) -> None:
    assert ctrl.itemAt(192, 112, 4) == {"kind": "instance", "index": 2}
    assert ctrl.itemAt(900, 900, 4) == {}
    assert ctrl.snapPoint(70, 101, 4) == {"x": 64, "y": 96, "pin": False}
    assert ctrl.snapPoint(141, 110, 6) == {"x": 144, "y": 112, "pin": True}  # R2 pin
    assert ctrl.contentBounds()[0] == -96


def test_selection(ctrl: SchematicController, qtbot) -> None:
    with qtbot.waitSignal(ctrl.selectionChanged):
        ctrl.select("instance", 2, False)
    assert ctrl.instances.row(2)["selected"] is True
    ctrl.select("wire", 0, True)
    assert ctrl.selection() == {("instance", 2), ("wire", 0)}
    assert ctrl.property("selectedWirePath") == "M 144 112 L 64 112"
    assert "M 144 112 L 64 112" not in ctrl.property("wirePath")
    ctrl.select("instance", 2, True)  # Ctrl+click again: off
    assert ctrl.selection() == {("wire", 0)}
    ctrl.selectRect(250, 100, 340, 300, False)
    assert ("instance", 1) in ctrl.selection() and ("wire", 0) not in ctrl.selection()
    ctrl.selectAll()
    assert ctrl.property("selectionCount") == 3 + 7 + 2 + 2 + 1  # parts, wires, flags, text
    ctrl.clearSelection()
    assert ctrl.selection() == set()
    ctrl.select("instance", 99, False)  # out of range: ignored
    assert ctrl.selection() == set()


def test_edits_go_through_the_undo_stack(ctrl: SchematicController) -> None:
    check_undo_redo(
        ctrl, lambda: ctrl._edit("add wire", lambda s: s.wires.append(Wire(0, 0, 16, 0)))
    )
    assert ctrl.property("undoText") == "add wire"
    steps = ctrl.undo_stack().count()
    ctrl._edit("nothing", lambda s: None)
    assert ctrl.undo_stack().count() == steps


def test_live_session_is_one_undo_step_or_nothing(ctrl: SchematicController) -> None:
    before = ctrl.schematic()
    ctrl._begin_live("drag")
    for x in (10, 20, 30):
        s = ctrl.schematic()
        s.instances[0].x = x
        ctrl._live_set(s)
    assert ctrl.property("live") is True
    ctrl.cancelLive()
    assert ctrl.schematic() == before and ctrl.property("canUndo") is False

    ctrl._begin_live("drag")
    s = ctrl.schematic()
    s.instances[0].x = 32
    ctrl._live_set(s)
    ctrl.commitLive()
    assert ctrl.undo_stack().count() == 1 and ctrl.property("undoText") == "drag"
    ctrl.undo()
    assert ctrl.schematic() == before


def test_load_clears_undo_and_selection(ctrl: SchematicController) -> None:
    ctrl._edit("add wire", lambda s: s.wires.append(Wire(0, 0, 16, 0)))
    ctrl.select("wire", 0, False)
    ctrl.load_schematic(None)
    assert ctrl.property("canUndo") is False and ctrl.selection() == set()
    assert ctrl.instances.count == 0 and ctrl.schematic_or_none() is None


def test_sync_model_changes_only_what_differs(qapp: object, qtbot) -> None:
    model = SyncModel(["a"])
    model.sync([{"a": 1}, {"a": 2}, {"a": 3}])
    inserted: list[tuple[int, int]] = []
    removed: list[tuple[int, int]] = []
    changed: list[int] = []
    model.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
    model.rowsRemoved.connect(lambda _p, first, last: removed.append((first, last)))
    model.dataChanged.connect(lambda tl, _br, _roles: changed.append(tl.row()))
    model.sync([{"a": 1}, {"a": 9}, {"a": 2}, {"a": 3}])
    assert inserted == [(1, 1)] and changed == [] and removed == []
    model.sync([{"a": 1}, {"a": 7}, {"a": 3}])
    assert changed == [1] and removed == [(2, 2)]
    assert [r["a"] for r in model.rows()] == [1, 7, 3]


def test_missing_symbol_part_still_shows(ctrl: SchematicController) -> None:
    ctrl.load_schematic(Schematic(instances=[Instance("nosuch", "X1", 0, 0)]))
    row = ctrl.instances.row(0)
    assert row["missing"] is True and row["texts"][0]["text"] == "X1: nosuch?"


# ---------------------------------------------------------------- S6: placing parts


def test_next_name_uses_the_prefix_and_skips_used_numbers(ctrl: SchematicController) -> None:
    assert ctrl.nextName("res") == "R1"  # R2 exists, R1 is free
    assert ctrl.nextName("cap") == "C2"
    assert ctrl.nextName("opamp") == "U1"  # Prefix X parts are U1, U2...
    assert ctrl.nextName("voltage") == "V2"
    assert "ground" not in ctrl.placeableSymbols() and "label" not in ctrl.placeableSymbols()
    assert "res" in ctrl.placeableSymbols()
    assert "Resistor" in ctrl.symbolDescription("res")


def test_each_placed_part_is_one_undo_step(ctrl: SchematicController) -> None:
    ctrl.load_schematic(None)
    assert ctrl.beginPlace("res", 100, 100) == "R1"
    assert ctrl.property("placing") == "res" and ctrl.instances.count == 1  # floating
    assert ctrl.property("canUndo") is False
    assert ctrl.placeCommit(100, 100) == "R2"  # R1 dropped, R2 floats
    ctrl.placeMoveTo(300, 100)
    assert ctrl.placeCommit(300, 100) == "R3"
    ctrl.stopPlacing()
    s = ctrl.schematic()
    assert [i.name for i in s.instances] == ["R1", "R2"]
    assert ctrl.undo_stack().count() == 2 and ctrl.property("undoText") == "place R2"
    # Origins are on the grid, with the part's box centred near the pointer.
    for inst in s.instances:
        assert inst.x % 16 == 0 and inst.y % 16 == 0
    assert (s.instances[0].x, s.instances[0].y) == (80, 48)
    assert s.instances[1].x == 288  # 300 - 16 (box centre) snaps to 288
    ctrl.undo()
    assert [i.name for i in ctrl.schematic().instances] == ["R1"]
    ctrl.redo()
    assert [i.name for i in ctrl.schematic().instances] == ["R1", "R2"]


def test_esc_while_placing_leaves_nothing_behind(ctrl: SchematicController) -> None:
    before = ctrl.schematic()
    ctrl.beginPlace("cap", 0, 0)
    ctrl.placeMoveTo(64, 64)
    ctrl.stopPlacing()
    assert ctrl.schematic() == before and ctrl.property("placing") == ""


def test_rotate_and_mirror_while_placing_carry_over(ctrl: SchematicController) -> None:
    ctrl.load_schematic(None)
    ctrl.beginPlace("res", 0, 0)
    assert ctrl.placeRotate() and ctrl.placeMirror()
    ctrl.placeCommit(0, 0)
    s = ctrl.schematic()
    assert (s.instances[0].rotation, s.instances[0].mirror) == (90, True)  # LTspice M90
    assert (s.instances[1].rotation, s.instances[1].mirror) == (90, True)  # next one too
    ctrl.stopPlacing()
    assert ctrl.placeRotate() is False  # not placing


def test_pending_library_part_is_placed_with_its_model(ctrl: SchematicController) -> None:
    ctrl.load_schematic(None)
    ctrl._pending = {"symbol": "diode", "attrs": {"SpiceModel": "1N4148", "ModelFile": "d.lib"}}
    ctrl.beginPlace("diode", 0, 0)
    ctrl.placeCommit(0, 0)
    ctrl.stopPlacing()
    [d1] = ctrl.schematic().instances
    assert d1.attrs == {"SpiceModel": "1N4148", "ModelFile": "d.lib"}
    assert ctrl.property("pendingPart") == {}
    ctrl.beginPlace("diode", 0, 0)
    assert ctrl.schematic().instances[-1].attrs == {}
    ctrl.stopPlacing()


def test_unknown_symbol_is_not_placed(ctrl: SchematicController) -> None:
    assert ctrl.beginPlace("nosuch", 0, 0) == ""
    assert ctrl.property("live") is False


# ---------------------------------------------------------------- S7: wires and flags


def test_add_wires_is_one_undo_step_and_skips_zero_length(ctrl: SchematicController) -> None:
    after = check_undo_redo(
        ctrl, lambda: ctrl.addWires([[400, 0, 400, 0], [400, 0, 480, 0], [480, 0, 480, 64]])
    )
    assert after.wires[-2:] == [Wire(400, 0, 480, 0), Wire(480, 0, 480, 64)]
    assert ctrl.property("undoText") == "wire"
    assert ctrl.addWires([[0, 0, 0, 0]]) is False
    assert ctrl.addWires([["a", 0, 1, 2]]) is False


def test_t_join_gets_a_junction_dot(ctrl: SchematicController, qtbot) -> None:
    with qtbot.waitSignal(ctrl.sheetChanged):
        ctrl.addWires([[100, 112, 100, 48]])  # ends on the middle of the IN wire
    assert ctrl.property("junctions") == [{"x": 100, "y": 112}]
    ctrl.undo()
    assert ctrl.property("junctions") == []


def test_connection_points_end_the_wire_tool(ctrl: SchematicController) -> None:
    assert ctrl.isConnectionPoint(144, 112)  # R2 pin and wire end
    assert ctrl.isConnectionPoint(100, 112)  # on a wire
    assert ctrl.isConnectionPoint(64, 288)  # ground flag
    assert not ctrl.isConnectionPoint(500, 500)


def test_labels_and_grounds(ctrl: SchematicController) -> None:
    after = check_undo_redo(ctrl, lambda: ctrl.addLabel(400, 0, " vout "))
    assert after.labels[-1].name == "vout" and ctrl.property("undoText") == "label vout"
    assert ctrl.addLabel(0, 0, "two words") is False and ctrl.addLabel(0, 0, " ") is False
    after = check_undo_redo(ctrl, lambda: ctrl.addGround(400, 64))
    assert (after.grounds[-1].x, after.grounds[-1].y) == (400, 64)


# ---------------------------------------------------------------- S8: editing


def test_rotate_mirror_delete_each_undo_and_redo(ctrl: SchematicController) -> None:
    ctrl.select("instance", 2, False)
    after = check_undo_redo(ctrl, ctrl.rotateSelection)
    r2 = after.instances[2]
    assert (r2.rotation, r2.mirror) == (0, True)  # M90 turned clockwise is M0
    assert ctrl.property("undoText") == "rotate"
    check_undo_redo(ctrl, ctrl.mirrorSelection)
    ctrl.select("instance", 2, False)
    after = check_undo_redo(ctrl, ctrl.deleteSelection)
    assert [i.name for i in after.instances] == ["V1", "C1"]
    ctrl.clearSelection()
    assert ctrl.rotateSelection() is False and ctrl.deleteSelection() is False


def test_drag_is_one_undo_step_and_wires_follow(ctrl: SchematicController) -> None:
    ctrl.select("instance", 1, False)  # C1
    before = ctrl.schematic()

    def drag() -> None:
        assert ctrl.beginMove(304, 176, True)
        for x in (310, 330, 350, 368):
            ctrl.moveTo(x, 176)
        assert ctrl.property("moving") is True
        ctrl.endMove()

    after = check_undo_redo(ctrl, drag)
    assert after.instances[1].x == before.instances[1].x + 64
    assert ctrl.property("undoText") == "drag"
    nets = connect(after, ctrl.lookup)
    assert nets.pins[1] == ["OUT", "0"]  # still connected


def test_move_detaches(ctrl: SchematicController) -> None:
    ctrl.select("instance", 1, False)
    ctrl.beginMove(304, 176, False)
    ctrl.moveTo(368, 176)
    ctrl.endMove()
    after = ctrl.schematic()
    assert after.wires == rc().wires
    assert ctrl.property("undoText") == "move"
    assert connect(after, ctrl.lookup).pins[1] == ["", ""]


def test_copy_paste_floats_then_drops_as_one_step(ctrl: SchematicController) -> None:
    ctrl.select("instance", 2, False)
    assert ctrl.property("canPaste") is False
    assert ctrl.copySelection() and ctrl.property("canPaste") is True

    def paste() -> None:
        assert ctrl.beginPaste(192, 400)
        ctrl.moveTo(192, 480)
        ctrl.endMove()

    after = check_undo_redo(ctrl, paste)
    assert [i.name for i in after.instances] == ["V1", "C1", "R2", "R1"]
    assert after.instances[3].attrs == {"Value": "10k"}
    assert ctrl.property("undoText") == "paste"
    # Esc while the copy floats: nothing is added.
    before = ctrl.schematic()
    ctrl.beginPaste(0, 0)
    ctrl.cancelLive()
    assert ctrl.schematic() == before


def test_undo_during_a_live_session_cancels_it_first(ctrl: SchematicController) -> None:
    ctrl.addGround(400, 400)
    ctrl.beginPlace("res", 0, 0)
    ctrl.undo()  # drops the floating part and undoes the ground
    assert ctrl.schematic() == rc()


# ---------------------------------------------------------------- S9: attributes, texts


def test_simple_part_attributes_show_value_and_value2(ctrl: SchematicController) -> None:
    v1 = ctrl.partAttributes(0)
    assert (v1["name"], v1["prefix"], v1["simple"]) == ("V1", "V", True)
    assert [(f["key"], f["value"]) for f in v1["fields"]] == [
        ("Value", "PULSE(0 5 10m 1u 1u 10m 20m)"),
        ("Value2", "AC 1"),
    ]
    r2 = ctrl.partAttributes(2)
    assert [f["key"] for f in r2["fields"]] == ["Value"]
    assert ctrl.partAttributes(99) == {}


def test_model_part_shows_all_fields_with_symbol_defaults(ctrl: SchematicController) -> None:
    ctrl.load_schematic(Schematic(instances=[Instance("diode", "D1", 0, 0)]))
    d1 = ctrl.partAttributes(0)
    assert d1["simple"] is False
    fields = {f["key"]: (f["value"], f["default"]) for f in d1["fields"]}
    assert list(fields) == ["Value", "Value2", "SpiceModel", "ModelFile", "SpiceLine", "SpiceLine2"]
    assert fields["Value"] == ("D", "D")


def test_set_attributes_is_one_step_and_stores_only_changes(ctrl: SchematicController) -> None:
    after = check_undo_redo(ctrl, lambda: ctrl.setAttributes(2, "R1", {"Value": "2k"}))
    assert after.instances[2].name == "R1" and after.instances[2].attrs == {"Value": "2k"}
    assert ctrl.property("undoText") == "edit R2"
    ctrl.load_schematic(Schematic(instances=[Instance("diode", "D1", 0, 0)]))
    assert ctrl.setAttributes(0, "D1", {"Value": "D", "SpiceModel": "1N4148"}) == ""
    assert ctrl.schematic().instances[0].attrs == {"SpiceModel": "1N4148"}  # Value = default
    steps = ctrl.undo_stack().count()
    assert ctrl.setAttributes(0, "D1", {"SpiceModel": "1N4148"}) == ""
    assert ctrl.undo_stack().count() == steps  # nothing changed, no step


def test_changed_value_reaches_the_netlist(ctrl: SchematicController) -> None:
    from core.schematic.netlister import netlist_schematic

    ctrl.setAttributes(2, "R2", {"Value": "2k"})
    text = netlist_schematic(ctrl.schematic(), ctrl.lookup).text
    assert "R2 IN OUT 2k" in text


def test_bad_attributes_are_refused_with_the_reason(ctrl: SchematicController) -> None:
    assert ctrl.checkAttributes(2, "C1", {"Value": "1k"}) == "another part is already called C1"
    assert ctrl.checkAttributes(2, "R 2", {"Value": "1k"}) == "the name must be one word"
    assert "bad number" in ctrl.checkAttributes(2, "R2", {"Value": "1kz!"})
    before = ctrl.schematic()
    assert ctrl.setAttributes(2, "C1", {"Value": "1k"}) != ""
    assert ctrl.schematic() == before
    assert ctrl.checkAttributes(2, "Rload", {"Value": "47"}) == ""


def test_rename_label(ctrl: SchematicController) -> None:
    after = check_undo_redo(ctrl, lambda: ctrl.renameLabel(1, "VOUT"))
    assert after.labels[1].name == "VOUT" and ctrl.labelName(1) == "VOUT"
    assert ctrl.renameLabel(1, "two words") is False and ctrl.renameLabel(9, "x") is False


def test_add_and_edit_sheet_texts(ctrl: SchematicController) -> None:
    after = check_undo_redo(ctrl, lambda: ctrl.addText(0, 400, " .tran 5m ", True))
    assert after.texts[-1] == TextItem(0, 400, ".tran 5m", True)
    assert ctrl.property("undoText") == "add directive"
    after = check_undo_redo(ctrl, lambda: ctrl.setText(1, "note", False))
    assert after.texts[1] == TextItem(0, 400, "note", False)
    assert ctrl.textAt(1) == {"index": 1, "text": "note", "directive": False}
    assert ctrl.addText(0, 0, "  ", True) is False and ctrl.setText(5, "x", True) is False


def test_texts_are_selectable_movable_and_deletable(ctrl: SchematicController) -> None:
    assert ctrl.itemAt(-80, 176, 4) == {"kind": "text", "index": 0}
    ctrl.select("text", 0, False)
    ctrl.beginMove(-80, 176, True)
    ctrl.moveTo(-48, 208)
    ctrl.endMove()
    assert (ctrl.schematic().texts[0].x, ctrl.schematic().texts[0].y) == (-64, 208)
    ctrl.deleteSelection()
    assert ctrl.schematic().texts == []
