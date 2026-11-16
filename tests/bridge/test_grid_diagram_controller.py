"""GridDiagramController (D5): every slot, undo/redo, drag merging, debounced
validation, files, and Run through GridController.runModel.

The electrical values of the small test design are test inputs typed here (as a user
would type them); IEEE 13 values come from the fixture file through OpenDSS, never
typed here.
"""

from __future__ import annotations

import copy
import math
import os
import time
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

import bridge.grid_controller as grid_controller  # noqa: E402
from bridge.grid_controller import GridController  # noqa: E402
from bridge.grid_diagram_controller import (  # noqa: E402
    VALIDATE_DELAY_MS,
    GridDiagramController,
    coerce_input,
)
from core.grid import design_io  # noqa: E402
from core.grid.config import CONTROL_STATIC, PowerFlowConfig  # noqa: E402
from core.grid.diagram.model import check_consistency  # noqa: E402
from core.grid.model import Terminal  # noqa: E402
from core.grid.runner import GridRunner  # noqa: E402
from core.grid.topology import GridGraph  # noqa: E402
from tests.core.grid.helpers import IEEE13  # noqa: E402

RUN_TIMEOUT_MS = 60_000
PU_TOL = 1e-6
LOSS_REL_TOL = 1e-3

# Test inputs (typed by the "user" of the test): a 12.47 kV source and a short
# 3-phase line to one load.
SOURCE = dict(sourceKv=12.47, r1=0.1, x1=1.0, r0=0.1, x0=1.0)
R3 = [0.3, 0.1, 0.1, 0.1, 0.3, 0.1, 0.1, 0.1, 0.3]
X3 = [0.6, 0.2, 0.2, 0.2, 0.6, 0.2, 0.2, 0.2, 0.6]
C3 = [3.0, -1.0, -1.0, -1.0, 3.0, -1.0, -1.0, -1.0, 3.0]


@pytest.fixture
def grid(qapp: object) -> Iterator[GridController]:
    c = GridController()
    yield c
    c.shutdown()


@pytest.fixture
def ctl(grid: GridController) -> Iterator[GridDiagramController]:
    c = GridDiagramController(grid_controller=grid)
    yield c
    c.shutdown()


def new(ctl: GridDiagramController, name: str = "d1") -> None:
    assert ctl.newDesign(name, 60.0, "src", *SOURCE.values()), ctl.statusText


def small_design(ctl: GridDiagramController) -> None:
    """src --l1--> b1 with load ld1, every value set."""
    new(ctl)
    assert ctl.addElement("bus", [], 0.0, 6.0) == "bus.b1"
    values = {"name": "lc3", "r_matrix": R3, "x_matrix": X3, "c_matrix": C3}
    lc = ctl.addElement("linecode", [], math.nan, math.nan, values)
    assert lc == "linecode.lc3", ctl.statusText
    assert ctl.addElement("line", ["src", "b1"], math.nan, math.nan,
                          {"linecode": "lc3", "length_m": "500"}) == "line.l1"  # fmt: skip
    assert ctl.addElement("load", ["b1"], math.nan, math.nan, {"kw": 900, "kvar": "300"}) == (
        "load.ld1"
    )


def snapshot(ctl: GridDiagramController) -> object:
    design = ctl.design()
    assert design is not None
    return copy.deepcopy(design)


def rows(model: object) -> list[dict[str, object]]:
    return list(model.rows())  # type: ignore[attr-defined]


def row(model: object, glyph_id: str) -> dict[str, object]:
    return next(r for r in rows(model) if r.get("glyphId") == glyph_id)


def consistent(ctl: GridDiagramController) -> None:
    design = ctl.design()
    assert design is not None
    assert check_consistency(design.grid, design.diagram) == []
    GridGraph.build(design.grid)


def wait_check(qtbot, ctl: GridDiagramController) -> None:
    with qtbot.waitSignal(ctl.validated, timeout=2000):
        pass


# ---- new design and the models


def test_new_design_needs_a_frequency_and_a_valid_name(ctl: GridDiagramController) -> None:
    assert not ctl.newDesign("D 1", 60.0)
    assert not ctl.newDesign("d1", 0.0)
    assert not ctl.newDesign("d1", math.nan)
    assert not ctl.hasDesign
    assert "base frequency" in ctl.consoleText
    # a source needs every value; nothing is assumed
    assert not ctl.newDesign("d1", 50.0, "src", 11.0, 0.1, math.nan, 0.1, 1.0)
    assert "X1" in ctl.statusText and not ctl.hasDesign


def test_new_empty_design_and_with_a_source(ctl: GridDiagramController) -> None:
    assert ctl.newDesign("empty", 50.0)
    assert ctl.hasDesign and ctl.designName == "empty" and ctl.baseFrequency == 50.0
    assert ctl.busGlyphs.count == 0 and not ctl.canUndo and not ctl.modified
    new(ctl)
    design = ctl.design()
    assert design is not None and design.grid.derive_voltage_bases
    src = design.grid.sources["source"]
    assert (src.kv, src.r1_ohm, src.x0_ohm) == (12.47, 0.1, 1.0)
    assert [r["glyphId"] for r in rows(ctl.busGlyphs)] == ["bus.src"]
    assert [r["glyphId"] for r in rows(ctl.elementGlyphs)] == ["vsource.source"]
    assert not ctl.canUndo  # the starting design is not an undo step
    consistent(ctl)


def test_library_items_are_the_v04_classes(ctl: GridDiagramController) -> None:
    kinds = [i["kind"] for i in ctl.libraryItems]
    assert kinds == ["bus", "vsource", "linecode", "line", "switch", "transformer",
                     "regulator", "load", "capacitor", "pvsystem"]  # fmt: skip
    reg = next(i for i in ctl.libraryItems if i["kind"] == "regulator")
    assert reg["required"] == ["Vreg", "band", "PT ratio", "CT primary"]


def test_add_element_and_derived_edges(ctl: GridDiagramController) -> None:
    small_design(ctl)
    consistent(ctl)
    assert ctl.busGlyphs.count == 2 and ctl.elementGlyphs.count == 3
    edges = {r["edgeId"]: r for r in rows(ctl.edges)}
    assert set(edges) == {"vsource.source#0", "line.l1#0", "load.ld1#0"}
    design = ctl.design()
    assert design is not None
    src, b1 = design.diagram.bus_glyphs["src"], design.diagram.bus_glyphs["b1"]
    line = edges["line.l1#0"]
    # from bus bar to bus bar, derived from the line's terminals (rule 20)
    assert (line["y1"], line["y2"]) == (src.y, b1.y)
    load = edges["load.ld1#0"]
    glyph = design.diagram.element_glyphs["load.ld1"]
    assert (load["x1"], load["y1"]) == (glyph.x, glyph.y) and load["y2"] == b1.y
    zone = row(ctl.busGlyphs, "bus.b1")
    assert zone["zoneKv"] == 12.47 and zone["phases"] == "1.2.3"
    assert design.grid.loads["ld1"].kw == 900.0 and design.grid.loads["ld1"].kvar == 300.0
    assert design.grid.lines["l1"].length_m == 500.0
    assert ctl.undoText == "Add load ld1"


def test_add_is_refused_without_changing_anything(qtbot, ctl: GridDiagramController) -> None:
    new(ctl)
    before = snapshot(ctl)
    with qtbot.waitSignal(ctl.editRefused):
        assert ctl.addElement("line", ["src", "nowhere"], 0.0, 0.0) == ""
    assert ctl.design() == before and not ctl.canUndo
    assert "nowhere" in ctl.consoleText
    assert ctl.addElement("fuse", ["src"], 0.0, 0.0) == ""  # not a v0.4 class
    # a switch needs its R (no default: rule 24)
    assert ctl.addElement("switch", ["src", "src"], 0.0, 0.0) == ""
    assert "R" in ctl.statusText
    assert ctl.design() == before


def test_add_copy_from_and_regulator(ctl: GridDiagramController) -> None:
    small_design(ctl)
    assert ctl.addElement("load", ["b1"], math.nan, math.nan, {}, "load.ld1") == "load.ld2"
    design = ctl.design()
    assert design is not None and design.grid.loads["ld2"].kw == 900.0
    ctl.addElement("bus", [], 6.0, 6.0)
    count = ctl.undo_stack().count()
    reg = ctl.addElement(
        "regulator",
        ["b1", "b2"],
        math.nan,
        math.nan,
        {
            "vreg": "120",
            "band": 2,
            "pt_ratio": 60,
            "ct_primary": 100,
            "windings.0.kva": 1000,
            "windings.1.kva": "1000",
        },
    )
    assert reg == "regcontrol.reg1", ctl.statusText
    assert ctl.undo_stack().count() == count + 1  # transformer + RegControl, one step
    tx = design.grid.transformers["tx1"]
    assert tx.phases == 1 and [w.kva for w in tx.windings] == [1000.0, 1000.0]
    assert row(ctl.elementGlyphs, "transformer.tx1")["regulated"]
    ctl.undo()
    assert "tx1" not in design.grid.transformers and not design.grid.regulators
    consistent(ctl)


# ---- undo / redo


def test_undo_redo_texts_and_exact_revert(ctl: GridDiagramController) -> None:
    new(ctl)
    start = snapshot(ctl)
    assert ctl.addElement("bus", [], 0.0, 6.0) == "bus.b1"
    after = snapshot(ctl)
    assert ctl.canUndo and not ctl.canRedo and ctl.undoText == "Add bus b1"
    assert ctl.modified
    ctl.undo()
    assert ctl.design() == start and ctl.redoText == "Add bus b1" and not ctl.canUndo
    assert ctl.busGlyphs.count == 1
    consistent(ctl)
    ctl.redo()
    assert ctl.design() == after and ctl.busGlyphs.count == 2
    consistent(ctl)


def test_a_200_event_drag_is_one_undo_step(ctl: GridDiagramController) -> None:
    small_design(ctl)
    design = ctl.design()
    assert design is not None
    start = snapshot(ctl)
    count = ctl.undo_stack().count()
    gesture = ctl.newGesture()
    for i in range(1, 201):
        assert ctl.moveGlyph("bus.b1", 0.1 * i, 6.0 + 0.05 * i, gesture)
    assert ctl.undo_stack().count() == count + 1
    assert ctl.undoText == "Move bus.b1"
    g = design.diagram.bus_glyphs["b1"]
    assert (g.x, g.y) == pytest.approx((20.0, 16.0))
    assert row(ctl.busGlyphs, "bus.b1")["x"] == pytest.approx(20.0)
    assert design.grid == start.grid  # presentation only
    ctl.undo()
    assert ctl.design() == start
    # a second drag is a second step; a drag back to the start is no step at all
    ctl.redo()
    g2 = ctl.newGesture()
    ctl.moveGlyph("bus.b1", 1.0, 1.0, g2)
    assert ctl.undo_stack().count() == count + 2
    g3 = ctl.newGesture()
    ctl.moveGlyph("bus.b1", 5.0, 5.0, g3)
    ctl.moveGlyph("bus.b1", 1.0, 1.0, g3)
    assert ctl.undo_stack().count() == count + 2


def test_move_and_rotate_do_not_validate(qtbot, ctl: GridDiagramController) -> None:
    small_design(ctl)
    wait_check(qtbot, ctl)
    with qtbot.assertNotEmitted(ctl.validated, wait=VALIDATE_DELAY_MS * 3):
        ctl.moveGlyph("load.ld1", 3.0, 9.0)
        assert ctl.rotateGlyph("load.ld1")
    assert row(ctl.elementGlyphs, "load.ld1")["rotation"] == 90
    assert ctl.rotateGlyph("bus.b1", 1)
    assert row(ctl.busGlyphs, "bus.b1")["orientation"] == 90
    assert not ctl.rotateGlyph("bus.nope")


# ---- validation


def test_validation_is_debounced_and_reaches_the_console(qtbot, ctl: GridDiagramController) -> None:
    small_design(ctl)
    wait_check(qtbot, ctl)
    assert ctl.errorCount == 0, ctl.consoleText
    received: list[float] = []
    ctl.validated.connect(lambda: received.append(time.monotonic()))
    t0 = time.monotonic()
    assert ctl.setProperty("load.ld1", "kw", "")  # back to a draft
    assert ctl.setProperty("load.ld1", "kvar", "")
    assert not received  # not checked yet: debounced
    with qtbot.waitSignal(ctl.validated, timeout=2000):
        pass
    assert len(received) == 1 and received[0] - t0 >= VALIDATE_DELAY_MS / 1000 * 0.9
    messages = [d["message"] for d in ctl.diagnosticsFor("load.ld1")]
    assert "kW not set" in messages and "kvar not set" in messages
    assert ctl.errorCount == 2
    assert "error: load.ld1: kW not set" in ctl.consoleText
    assert row(ctl.elementGlyphs, "load.ld1")["severity"] == "error"
    assert not ctl.canRun


def test_property_sheet(qtbot, ctl: GridDiagramController) -> None:
    small_design(ctl)
    assert ctl.propertySheet == []
    ctl.select("load.ld1")
    assert ctl.selection == ["load.ld1"]
    sheet = {r["field"]: r for r in ctl.propertySheet}
    assert sheet["name"]["value"] == "ld1" and not sheet["name"]["editable"]
    assert sheet["terminal"]["value"] == "b1" and not sheet["terminal"]["editable"]
    kw = sheet["kw"]
    assert (kw["label"], kw["unit"], kw["value"], kw["editable"], kw["type"]) == (
        "kW", "kW", "900.0", True, "number"
    )  # fmt: skip
    assert sheet["conn"]["choices"] == ["wye", "delta"]
    ctl.setProperty("load.ld1", "kw", "")
    wait_check(qtbot, ctl)
    sheet = {r["field"]: r for r in ctl.propertySheet}
    assert sheet["kw"]["diagnostic"] == "kW not set" and sheet["kv"]["diagnostic"] == ""
    # windings are rows "windings.<i>.<field>"
    ctl.addElement("bus", [], 6.0, 6.0)
    ctl.addElement("transformer", ["b1", "b2"], math.nan, math.nan)
    ctl.select("transformer.tx1")
    labels = {r["field"]: r["label"] for r in ctl.propertySheet}
    assert labels["windings.1.kva"] == "winding 2 kVA"
    assert labels["windings.0.terminal"] == "winding 1 terminal"
    ctl.select("bus.b1")
    assert {r["field"] for r in ctl.propertySheet} == {"name", "kv_base_ln"}


def test_set_property_types_merge_and_refusal(ctl: GridDiagramController) -> None:
    small_design(ctl)
    design = ctl.design()
    assert design is not None
    count = ctl.undo_stack().count()
    assert ctl.setProperty("load.ld1", "kw", 950.0, "spin")
    assert ctl.setProperty("load.ld1", "kw", "960", "spin")
    assert ctl.undo_stack().count() == count + 1 and design.grid.loads["ld1"].kw == 960.0
    assert ctl.setProperty("line.l1", "phases", 3.0)  # a JS number for an int field
    assert ctl.setProperty("linecode.lc3", "r_matrix", "0.3 0.1 0.1 0.1 0.3 0.1 0.1 0.1 0.31")
    assert design.grid.linecodes["lc3"].r_matrix[-1] == 0.31
    before = snapshot(ctl)
    assert not ctl.setProperty("load.ld1", "kw", "lots")
    assert not ctl.setProperty("load.ld1", "name", "x")
    assert not ctl.setProperty("load.nope", "kw", 1)
    assert ctl.design() == before
    ctl.undo()
    assert design.grid.linecodes["lc3"].r_matrix[-1] == 0.3


def test_coerce_input() -> None:
    assert coerce_input(float | None, "") is None
    assert coerce_input(float, " 2.5 ") == 2.5
    assert coerce_input(int, 3.0) == 3 and coerce_input(int, "4") == 4
    assert coerce_input(bool, "false") is False
    assert coerce_input(tuple[float, ...] | None, "1, 2;3") == [1.0, 2.0, 3.0]
    assert coerce_input(float, "abc") == "abc"  # passed on: the command reports it


# ---- connect, rename, switch, delete


def test_connect_and_disconnect_terminal(qtbot, ctl: GridDiagramController) -> None:
    small_design(ctl)
    ctl.addElement("bus", [], 6.0, 6.0)
    assert ctl.connectTerminal("load.ld1", 1, "b2")
    design = ctl.design()
    assert design is not None and design.grid.loads["ld1"].terminal.bus == "b2"
    assert ctl.undoText == "Connect load ld1 terminal 1 to bus b2"
    edge = next(r for r in rows(ctl.edges) if r["edgeId"] == "load.ld1#0")
    assert edge["y2"] == design.diagram.bus_glyphs["b2"].y
    assert ctl.connectTerminal("line.l1", 2, "b1", [1, 2, 3])
    assert str(design.grid.lines["l1"].bus2) == "b1.1.2.3"
    assert ctl.connectTerminal("load.ld1", 1, "")
    assert "load.ld1#0" not in {r["edgeId"] for r in rows(ctl.edges)}
    wait_check(qtbot, ctl)
    assert ctl.diagnosticsFor("load.ld1")  # a disconnected terminal is reported
    assert not ctl.connectTerminal("load.ld1", 2, "b1")
    consistent(ctl)


def test_rename_follows_the_selection(ctl: GridDiagramController) -> None:
    small_design(ctl)
    ctl.select("bus.b1")
    assert ctl.rename("bus.b1", "Feeder_End")
    design = ctl.design()
    assert design is not None
    assert ctl.selection == ["bus.feeder_end"]
    assert design.grid.lines["l1"].bus2.bus == "feeder_end"
    assert row(ctl.busGlyphs, "bus.feeder_end")["selected"]
    assert ctl.undoText == "Rename bus b1 to feeder_end"
    assert not ctl.rename("bus.feeder_end", "src")  # name in use
    ctl.undo()
    assert ctl.selection == [] and "b1" in design.grid.buses
    consistent(ctl)


def test_set_switch_open(ctl: GridDiagramController) -> None:
    small_design(ctl)
    ctl.addElement("bus", [], 6.0, 6.0)
    assert ctl.addElement("switch", ["b1", "b2"], math.nan, math.nan, {"r_ohm": 1e-4}) == (
        "line.sw1"
    )
    assert ctl.setSwitchOpen("line.sw1", True)
    assert row(ctl.elementGlyphs, "line.sw1")["open"]
    assert next(r for r in rows(ctl.edges) if r["elementId"] == "line.sw1")["open"]
    assert ctl.undoText == "Open switch sw1"
    assert not ctl.setSwitchOpen("load.ld1", True)
    ctl.undo()
    assert not row(ctl.elementGlyphs, "line.sw1")["open"]


def test_delete_selection_is_one_step(ctl: GridDiagramController) -> None:
    small_design(ctl)
    ctl.addElement("load", ["b1"], math.nan, math.nan, {"kw": 1, "kvar": 1})
    before = snapshot(ctl)
    count = ctl.undo_stack().count()
    assert not ctl.deleteSelection()  # nothing selected
    ctl.setSelection(["bus.b1", "load.ld1", "linecode.lc3"])
    assert ctl.deleteSelection()
    assert ctl.undo_stack().count() == count + 1 and ctl.selection == []
    design = ctl.design()
    assert design is not None
    assert set(design.grid.buses) == {"src"} and not design.grid.loads and not design.grid.lines
    assert not design.grid.linecodes
    consistent(ctl)
    ctl.undo()
    assert ctl.design() == before
    consistent(ctl)
    ctl.select("load.ld2")
    assert ctl.deleteSelection() and ctl.undoText == "Remove load ld2"


def test_selection_is_view_state(ctl: GridDiagramController) -> None:
    small_design(ctl)
    count = ctl.undo_stack().count()
    ctl.select("bus.b1")
    ctl.select("load.ld1", True)
    assert ctl.selection == ["bus.b1", "load.ld1"]
    ctl.select("bus.b1", True)
    assert ctl.selection == ["load.ld1"] and ctl.propertySheet
    ctl.select("bus.nope")
    assert ctl.selection == ["load.ld1"]
    assert row(ctl.elementGlyphs, "load.ld1")["selected"]
    ctl.clearSelection()
    assert ctl.selection == [] and ctl.propertySheet == []
    assert ctl.undo_stack().count() == count
    # undoing the add of a selected element drops it from the selection
    ctl.select("load.ld1")
    ctl.undo()
    assert ctl.selection == []


# ---- files


def test_save_and_load(tmp_path: Path, ctl: GridDiagramController, grid: GridController) -> None:
    small_design(ctl)
    assert not ctl.save()  # no file name yet
    assert ctl.save(str(tmp_path / "small"))
    path = tmp_path / "small.qgrid"
    assert path.is_file() and ctl.filePath == str(path.resolve()) and not ctl.modified
    saved = snapshot(ctl)
    ctl.moveGlyph("bus.b1", 2.0, 2.0)
    assert ctl.modified
    assert ctl.save()
    other = GridDiagramController(grid_controller=grid)
    assert other.load(str(path))
    assert other.design() == ctl.design() and other.design() != saved
    assert not other.canUndo and other.busGlyphs.count == 2
    bad = tmp_path / "bad.qgrid"
    bad.write_text("{not json", encoding="utf-8")
    assert not other.load(str(bad))
    assert other.design() == ctl.design() and "Not opened" in other.statusText
    other.shutdown()


def test_export_dss(tmp_path: Path, ctl: GridDiagramController) -> None:
    small_design(ctl)
    ctl.moveGlyph("bus.b1", 3.0, 8.0)
    before = snapshot(ctl)
    out = tmp_path / "small.dss"
    assert ctl.exportDss(str(out)), ctl.consoleText
    text = out.read_text(encoding="utf-8")
    assert "New Circuit.d1" in text
    assert "SetBusXY bus=b1 x=3 y=-8" in text  # glyph position, y up
    assert ctl.design() == before  # exporting changes nothing
    ctl.setProperty("load.ld1", "kw", "")
    assert not ctl.exportDss(str(tmp_path / "draft.dss"))
    assert "kW not set" in ctl.consoleText and not (tmp_path / "draft.dss").exists()


def test_script_path(tmp_path: Path, ctl: GridDiagramController) -> None:
    small_design(ctl)
    assert ctl.script_path() is None
    ctl.save(str(tmp_path / "small.qgrid"))
    assert ctl.script_path() == tmp_path.resolve() / "small_results" / "grid" / "d1" / "d1.dss"
    ctl.setResultsFolder(str(tmp_path / "proj_results"))
    assert ctl.script_path() == tmp_path / "proj_results" / "grid" / "d1" / "d1.dss"


# ---- without dss-python


def test_without_dss_python(
    monkeypatch: pytest.MonkeyPatch, qtbot, tmp_path: Path, grid: GridController
) -> None:
    monkeypatch.setattr(grid_controller, "engine_available", lambda: False)
    ctl = GridDiagramController(grid_controller=grid)
    try:
        assert not ctl.available
        small_design(ctl)  # new design, add
        ctl.addElement("bus", [], 6.0, 6.0)
        assert ctl.connectTerminal("load.ld1", 1, "b2")
        assert ctl.setProperty("load.ld1", "kw", 800)
        assert ctl.moveGlyph("bus.b2", 9.0, 9.0) and ctl.rotateGlyph("bus.b2")
        assert ctl.rename("load.ld1", "big")
        assert ctl.addElement("switch", ["b1", "b2"], 0.0, 0.0, {"r_ohm": 1e-4})
        assert ctl.setSwitchOpen("line.sw1", True)
        ctl.select("line.sw1")
        assert ctl.deleteSelection()
        ctl.undo()
        ctl.redo()
        wait_check(qtbot, ctl)
        assert ctl.save(str(tmp_path / "x.qgrid")) and ctl.load(str(tmp_path / "x.qgrid"))
        assert ctl.exportDss(str(tmp_path / "x.dss"))
        # only Run (and reading a .dss file, which OpenDSS does) need dss-python
        assert not ctl.canRun and "dss-python" in ctl.runDisabledReason
        assert not ctl.runPowerFlow()
        assert "dss-python" in ctl.statusText and not ctl.busy
        assert not ctl.openDssAsDesign(str(IEEE13))
        assert "dss-python" in ctl.statusText
    finally:
        ctl.shutdown()


# ---- with OpenDSS


def _pu(result) -> dict[str, float]:
    return dict(zip(result.node_names, (float(v) for v in result.node_voltage_pu), strict=True))


@pytest.mark.opendss
def test_ieee13_design_run_equals_the_dss_run(
    qtbot, tmp_path: Path, ctl: GridDiagramController
) -> None:
    config = PowerFlowConfig(control_mode=CONTROL_STATIC, tolerance=1e-10,
                             max_control_iterations=30)  # fmt: skip
    ctl.set_config(config)
    original = IEEE13.read_bytes()
    with qtbot.waitSignal(ctl.designOpened, timeout=RUN_TIMEOUT_MS):
        assert ctl.openDssAsDesign(str(IEEE13))
        assert ctl.busy
    assert not ctl.busy and ctl.designName == "ieee13nodeckt"
    consistent(ctl)
    design = ctl.design()
    assert design is not None and not design.grid.derive_voltage_bases  # file's bases
    assert ctl.busGlyphs.count == len(design.grid.buses)
    assert ctl.errorCount == 0, ctl.consoleText
    ctl.setResultsFolder(str(tmp_path / "proj_results"))
    with qtbot.waitSignal(ctl.resultReady, timeout=RUN_TIMEOUT_MS):
        assert ctl.runPowerFlow(), ctl.statusText
        assert ctl.busy
    assert ctl.hasResult and not ctl.busy, ctl.consoleText
    script = tmp_path / "proj_results" / "grid" / "ieee13nodeckt" / "ieee13nodeckt.dss"
    assert script.is_file() and ctl.summary["scriptPath"] == str(script)
    assert "New Circuit.ieee13nodeckt" in script.read_text(encoding="utf-8")

    reference = GridRunner(IEEE13, config).run()
    assert reference.ok
    a, b = _pu(reference.result), _pu(ctl.result())
    assert sorted(a) == sorted(b)
    worst = max(abs(a[n] - b[n]) for n in a if not (math.isnan(a[n]) and math.isnan(b[n])))
    assert worst <= PU_TOL
    assert math.isclose(
        ctl.result().total_losses_kw, reference.result.total_losses_kw, rel_tol=LOSS_REL_TOL
    )
    assert IEEE13.read_bytes() == original  # the .dss file is never written (rule 28)


@pytest.mark.opendss
def test_small_design_runs_and_drafts_do_not(qtbot, ctl: GridDiagramController) -> None:
    small_design(ctl)
    with qtbot.waitSignal(ctl.resultReady, timeout=RUN_TIMEOUT_MS):
        assert ctl.runPowerFlow(), ctl.statusText
    assert ctl.hasResult and ctl.summary["lossesKw"] > 0
    assert ctl.statusText.startswith("Finished: converged"), ctl.statusText
    ctl.setProperty("load.ld1", "kw", "")
    assert not ctl.canRun or not ctl.runPowerFlow()  # checked at once, not debounced
    assert not ctl.runPowerFlow() and "error" in ctl.statusText


@pytest.mark.opendss
def test_open_dss_errors(qtbot, tmp_path: Path, ctl: GridDiagramController) -> None:
    assert not ctl.openDssAsDesign(str(tmp_path / "missing.dss"))
    bad = Path(IEEE13).parent.parent / "bad" / "misspelled_redirect.dss"
    with qtbot.waitSignal(ctl.openFailed, timeout=RUN_TIMEOUT_MS):
        assert ctl.openDssAsDesign(str(bad))
    assert not ctl.hasDesign and "Not opened" in ctl.statusText


def test_qgrid_suffix_constant() -> None:
    assert design_io.SUFFIX == ".qgrid"


# ---- D6: canvas slots (one per editor action)


def test_click_hit_and_rubber_band(ctl: GridDiagramController) -> None:
    small_design(ctl)
    design = ctl.design()
    assert design is not None
    b1 = design.diagram.bus_glyphs["b1"]
    load = design.diagram.element_glyphs["load.ld1"]
    assert ctl.glyphAt(b1.x + 0.3, b1.y, 0.3) == "bus.b1" and ctl.selection == []
    assert ctl.clickAt(b1.x + 0.3, b1.y, 0.3, False) == "bus.b1"
    assert ctl.selection == ["bus.b1"]
    assert ctl.clickAt(load.x, load.y, 0.3, True) == "load.ld1"  # Ctrl adds
    assert ctl.selection == ["bus.b1", "load.ld1"]
    assert ctl.clickAt(load.x, load.y, 0.3, False) == "load.ld1"  # kept: a drag follows
    assert ctl.selection == ["bus.b1", "load.ld1"]
    assert ctl.clickAt(load.x, load.y, 0.3, True) == "load.ld1"  # Ctrl toggles off
    assert ctl.selection == ["bus.b1"]
    assert ctl.clickAt(500.0, 500.0, 0.3, False) == "" and ctl.selection == []
    # the load sits next to b1, but busAt ignores it
    assert ctl.busAt(load.x, load.y, 3.0) == "b1"
    assert ctl.busAt(500.0, 500.0, 1.0) == ""
    x0, y0, x1, y1 = ctl.contentBounds()
    ctl.selectRect(x0, y0, x1, y1, False)
    assert set(ctl.selection) == {"bus.src", "bus.b1", "vsource.source", "line.l1", "load.ld1"}
    ctl.selectRect(1000, 1000, 1001, 1001, True)  # additive, nothing inside: kept
    assert len(ctl.selection) == 5
    assert ctl.undoText == "Add load ld1"  # selecting is not undoable


def test_drag_of_a_selection_is_one_undo_step(ctl: GridDiagramController) -> None:
    small_design(ctl)
    design = ctl.design()
    assert design is not None
    before = snapshot(ctl)
    ctl.setSelection(["bus.b1", "load.ld1"])
    gesture = ctl.newGesture()
    for _ in range(20):
        assert ctl.moveSelection(0.5, 0.0, gesture)
    assert design.diagram.bus_glyphs["b1"].x == before.diagram.bus_glyphs["b1"].x + 10
    assert design.grid == before.grid  # presentation only
    assert ctl.undoText == "Move 2 items"
    # a move only redraws what it touched: the same rows as a full redraw
    partial = [rows(m) for m in (ctl.busGlyphs, ctl.elementGlyphs, ctl.edges)]
    partial_paths = ctl.edgePaths
    ctl._refresh()
    assert partial == [rows(m) for m in (ctl.busGlyphs, ctl.elementGlyphs, ctl.edges)]
    assert partial_paths == ctl.edgePaths
    ctl.setSelection(["bus.src"])
    assert ctl.rotateSelection()
    partial = [rows(m) for m in (ctl.busGlyphs, ctl.elementGlyphs, ctl.edges)]
    ctl._refresh()
    assert partial == [rows(m) for m in (ctl.busGlyphs, ctl.elementGlyphs, ctl.edges)]
    ctl.undo()
    ctl.setSelection(["bus.b1", "load.ld1"])
    paths = ctl.edgePaths
    assert set(paths) == {"line", "switch", "open", "transformer", "stub", "selected"}
    assert paths["selected"].count("M") == 1 and paths["line"].count("M") == 1
    ctl.undo()
    assert ctl.design() == before
    assert ctl.undoText == "Add load ld1"
    ctl.clearSelection()
    assert not ctl.moveSelection(1.0, 1.0, ctl.newGesture())


def test_rotate_selection_and_toggle_switch(ctl: GridDiagramController) -> None:
    small_design(ctl)
    design = ctl.design()
    assert design is not None
    assert not ctl.rotateSelection()
    ctl.setSelection(["bus.b1", "load.ld1", "linecode.lc3"])  # a line code has no glyph
    assert ctl.rotateSelection() and ctl.undoText == "Rotate 2 items"
    assert design.diagram.bus_glyphs["b1"].orientation == 90
    assert design.diagram.element_glyphs["load.ld1"].rotation == 90
    ctl.undo()
    assert design.diagram.bus_glyphs["b1"].orientation == 0
    assert ctl.addElement("bus", [], 6.0, 6.0) == "bus.b2"
    assert ctl.drawBranch("switch", "b1", "b2") == ""  # a switch needs its R typed
    sw = ctl.addElement("switch", ["b1", "b2"], math.nan, math.nan, {"r_ohm": "0.0001"})
    assert sw == "line.sw1", ctl.statusText
    assert ctl.toggleSwitch(sw) and not design.grid.switches["sw1"].closed
    assert row(ctl.elementGlyphs, sw)["open"] and ctl.edgePaths["open"]
    assert ctl.toggleSwitch(sw) and design.grid.switches["sw1"].closed
    assert not ctl.toggleSwitch("line.l1")  # a line: nothing happens
    assert not ctl.toggleSwitch("load.ld1")


def test_draw_branch_and_drop_items(ctl: GridDiagramController) -> None:
    small_design(ctl)
    design = ctl.design()
    assert design is not None
    assert ctl.dropItem("bus", 8.0, 6.0, 0.5) == "bus.b2"
    assert ctl.selection == ["bus.b2"]
    assert design.diagram.bus_glyphs["b2"].x == 8.0
    new = ctl.drawBranch("line", "b1", "b2")
    assert new == "line.l2" and ctl.selection == ["line.l2"] and ctl.undoText == "Add line l2"
    assert design.grid.lines["l2"].bus1.bus == "b1" and design.grid.lines["l2"].bus2.bus == "b2"
    assert ctl.drawBranch("load", "b1", "b2") == "" and "not a branch" in ctl.statusText
    # a shunt dropped onto a bus goes in that bus's row of shunts
    b2 = design.diagram.bus_glyphs["b2"]
    cap = ctl.dropItem("capacitor", b2.x + 0.2, b2.y, 0.5)
    assert cap == "capacitor.c1", ctl.statusText
    assert design.grid.capacitors["c1"].terminal.bus == "b2"
    assert design.grid.capacitors["c1"].kvar is None  # a draft: nothing invented
    assert design.diagram.element_glyphs[cap].y > b2.y
    n = len(design.grid.loads)
    assert ctl.dropItem("load", 300.0, 300.0, 0.5) == "" and "onto a bus" in ctl.statusText
    assert len(design.grid.loads) == n
    assert ctl.dropItem("line", 0.0, 0.0, 0.5) == "" and "from one bus" in ctl.statusText
    assert ctl.dropItem("fuse", 0.0, 0.0, 0.5) == ""  # not in the library (v0.5 scope)
    lc = ctl.dropItem("linecode", math.nan, math.nan, 0.0)
    assert lc.startswith("linecode.") and lc != "linecode.lc3" and ctl.selection == [lc]
    assert [r["id"] for r in ctl.lineCodes] == ["linecode.lc3", lc]
    assert ctl.lineCodes[1]["selected"]
    counts = design.grid.counts()
    assert ctl.busCount == 3 and ctl.elementCount == sum(counts.values()) - 3
    consistent(ctl)


def test_set_terminal_from_text(ctl: GridDiagramController) -> None:
    small_design(ctl)
    design = ctl.design()
    assert design is not None
    assert ctl.addElement("bus", [], 6.0, 6.0) == "bus.b2"
    assert ctl.setTerminal("load.ld1", "terminal", "b2.1.2.3")
    assert design.grid.loads["ld1"].terminal == Terminal("b2", (1, 2, 3))
    assert ctl.undoText.startswith("Connect")
    assert ctl.setTerminal("line.l1", "bus2", "B2")  # lower-cased
    assert design.grid.lines["l1"].bus2.bus == "b2"
    assert ctl.setTerminal("load.ld1", "terminal", "")  # disconnects
    assert design.grid.loads["ld1"].terminal.bus == ""
    assert not ctl.setTerminal("load.ld1", "terminal", "nowhere")
    assert not ctl.setTerminal("load.ld1", "bus2", "b2")
    assert not ctl.setTerminal("vsource.source", "bus2", "b2")  # follows bus1
    assert not ctl.setTerminal("linecode.lc3", "bus1", "b2")
    assert ctl.addElement("transformer", ["src", "b2"], math.nan, math.nan) == ("transformer.tx1")
    assert ctl.setTerminal("transformer.tx1", "windings.1.terminal", "b1")
    assert design.grid.transformers["tx1"].windings[1].terminal.bus == "b1"
    assert not ctl.setTerminal("transformer.tx1", "windings.5.terminal", "b1")
    consistent(ctl)


def test_edit_grid_as_design_needs_an_open_feeder(ctl: GridDiagramController) -> None:
    assert not ctl.editGridAsDesign() and not ctl.hasDesign
    assert "Grid view" in ctl.statusText
    assert ctl.contentBounds() == [] and ctl.glyphAt(0, 0, 1) == "" and ctl.busAt(0, 0, 1) == ""


@pytest.mark.opendss
def test_edit_grid_as_design_copies_the_feeder(
    qtbot, ctl: GridDiagramController, grid: GridController
) -> None:
    original = IEEE13.read_bytes()
    assert grid.openFile(str(IEEE13))
    qtbot.waitUntil(lambda: grid.model() is not None and not grid.running, timeout=RUN_TIMEOUT_MS)
    feeder = copy.deepcopy(grid.model())
    with qtbot.waitSignal(ctl.designOpened, timeout=2000):
        assert ctl.editGridAsDesign()
    design = ctl.design()
    assert design is not None and design.grid == feeder and design.grid is not grid.model()
    assert ctl.filePath == "" and not ctl.canUndo
    ctl.setSelection(["bus.671"])
    assert ctl.deleteSelection()
    assert "671" not in design.grid.buses and "671" in grid.model().buses
    assert grid.filePath.endswith("IEEE13Nodeckt.dss")  # the view keeps its feeder
    assert IEEE13.read_bytes() == original  # never written (rule 28)
    consistent(ctl)


def test_required_values_before_an_add(ctl: GridDiagramController) -> None:
    assert ctl.requiredValues("switch", "") == []  # no design
    small_design(ctl)
    fields = lambda kind, bus="": [r["field"] for r in ctl.requiredValues(kind, bus)]  # noqa: E731
    assert fields("switch") == ["r_ohm"]
    assert fields("regulator") == ["vreg", "band", "pt_ratio", "ct_primary"]
    assert fields("vsource", "b1") == ["kv", "r1_ohm", "x1_ohm", "r0_ohm", "x0_ohm"]
    assert fields("line") == [] and fields("load", "b1") == []
    assert ctl.requiredValues("switch", "")[0] == {"field": "r_ohm", "label": "R", "unit": "ohm"}
    assert ctl.dropItem("bus", 9.0, 0.0, 0.5) == "bus.b2"  # no voltage zone yet
    assert fields("load", "b2") == ["kv"]
    assert ctl.dropItem("load", 9.0, 0.0, 0.5) == ""  # refused: kV needed
    load = ctl.dropItem("load", 9.0, 0.0, 0.5, {"kv": "12.47"})
    assert load == "load.ld2", ctl.statusText
    design = ctl.design()
    assert design is not None and design.grid.loads["ld2"].kv == 12.47
    sw = ctl.drawBranch("switch", "b1", "b2", {"r_ohm": "0.0001"})
    assert sw == "line.sw1" and design.grid.switches["sw1"].r_ohm == 1e-4
    typed = {"vreg": "122", "band": "2", "pt_ratio": "20", "ct_primary": "700"}
    reg = ctl.drawBranch("regulator", "b1", "b2", typed)
    assert reg.startswith("regcontrol."), ctl.statusText
    assert ctl.undoText.startswith("Add regulator")
    consistent(ctl)
