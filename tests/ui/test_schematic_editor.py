"""S5-S8: SchematicController's editor side (models, selection, undo, edits)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from bridge.schematic_controller import SchematicController, SyncModel
from core.schematic.model import Instance, Schematic, Wire

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
        {"posX": -96, "posY": 176, "text": ".ac dec 100 1 1MEG", "directive": True}
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
    assert ctrl.property("selectionCount") == 3 + 7 + 2 + 2
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
