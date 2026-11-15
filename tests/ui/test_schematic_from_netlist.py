"""N2: SchematicController.generateFromNetlist - draw the netlist editor's text on the
sheet as one undo step."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from bridge.schematic_controller import DEFAULT_LIBRARY_KEY, SchematicController
from bridge.simulator import Simulator
from core.schematic.model import Wire
from tests.ui.test_project_manager import App, start_app
from tests.ui.test_schematic_canvas import rc

TESTS = Path(__file__).resolve().parents[1]
XOR = TESTS / "netlists" / "good" / "cmos_xor.cir"
OPAMP = TESTS / "netlists" / "good" / "opamp_subckt.cir"
BROKEN = TESTS / "netlists" / "broken" / "unknown_model.cir"


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def parts(qapp: object, settings: QSettings) -> Iterator[tuple[SchematicController, Simulator]]:
    ctrl, sim = SchematicController(settings=settings), Simulator()
    ctrl.attachSimulator(sim)
    yield ctrl, sim
    sim.shutdown()


def names(ctrl: SchematicController) -> list[str]:
    return [i.name for i in ctrl.schematic().instances]


def test_draws_the_xor_as_one_undo_step(parts, qtbot) -> None:
    ctrl, _ = parts
    assert ctrl.property("sheetEmpty") is True
    with qtbot.waitSignal(ctrl.sheetEmptyChanged):
        r = ctrl.generateFromNetlist(XOR.read_text())
    assert r["ok"] is True and r["placed"] == 11 and r["kept"] == []
    assert len(names(ctrl)) == 11 and ctrl.property("sheetEmpty") is False
    assert ctrl.property("undoText") == "Schematic from netlist"
    assert ctrl.is_generated_unchanged()
    ctrl.undo()
    assert ctrl.property("sheetEmpty") is True and not ctrl.is_generated_unchanged()
    ctrl.redo()
    assert len(names(ctrl)) == 11 and ctrl.is_generated_unchanged()


def test_replaces_a_sheet_and_undo_brings_it_back(parts) -> None:
    ctrl, _ = parts
    ctrl.load_schematic(rc())
    before = ctrl.schematic()
    assert ctrl.generateFromNetlist(XOR.read_text())["ok"]
    assert "MP1" in names(ctrl) and "R1" not in names(ctrl)
    ctrl.undo()
    assert ctrl.schematic() == before


def test_the_drawn_sheet_netlists_like_the_netlist(parts) -> None:
    ctrl, sim = parts
    assert ctrl.generateFromNetlist(XOR.read_text())["ok"]
    assert ctrl.check() is True
    text = ctrl.netlist_text()
    assert sim.check(text) is True, sim.property("diagnostics")


def test_errors_leave_the_sheet_alone(parts) -> None:
    ctrl, _ = parts
    ctrl.load_schematic(rc())
    before = ctrl.schematic()
    r = ctrl.generateFromNetlist(BROKEN.read_text())
    assert r["ok"] is False and r["placed"] == 0
    assert ctrl.schematic() == before and ctrl.property("canUndo") is False
    problems = ctrl.property("problems")
    assert problems == r["problems"]
    assert all(p["severity"] == "error" for p in problems)
    assert any(p["message"].startswith("line ") for p in problems)
    assert problems[-1]["message"] == "fix the netlist errors before making a schematic"


def test_warnings_are_listed(parts) -> None:
    ctrl, _ = parts
    r = ctrl.generateFromNetlist(OPAMP.read_text())
    assert r["ok"] and r["kept"] == ["X1"]
    [warning] = ctrl.property("problems")
    assert warning["severity"] == "warning" and "X1" in warning["message"]


def test_analysis_panel_card_counts_as_an_analysis(parts) -> None:
    ctrl, sim = parts
    text = "no analysis\nV1 a 0 1\nR1 a 0 1k\n.end\n"
    assert not ctrl.generateFromNetlist(text)["ok"]
    sim.setProperty("analysisCard", ".op")
    assert ctrl.generateFromNetlist(text)["ok"]


def test_symbols_in_the_library_folder_are_used(qapp: object, settings, tmp_path: Path) -> None:
    folder = tmp_path / "library"
    folder.mkdir()
    shutil.copy(TESTS / "fixtures" / "asy" / "cmosn.asy", folder / "cmosn.asy")
    shutil.copy(TESTS / "fixtures" / "lib" / "tsmc018.lib", folder / "tsmc018.lib")
    settings.setValue(DEFAULT_LIBRARY_KEY, str(folder))
    ctrl, sim = SchematicController(settings=settings), Simulator()
    try:
        ctrl.attachSimulator(sim)
        text = (
            "inv\n.include tsmc018.lib\nVDD vdd 0 1.8\nVIN in 0 0.9\n"
            "MP1 out in vdd vdd CMOSP W=2u L=0.18u\nMN1 out in 0 0 CMOSN W=1u L=0.18u\n"
            ".op\n.end\n"
        )
        assert ctrl.generateFromNetlist(text)["ok"], ctrl.property("problems")
        symbols = {i.name: i.symbol for i in ctrl.schematic().instances}
        assert symbols["MN1"] == "cmosn" and symbols["MP1"] == "pmos"
        assert ctrl.check() is True, ctrl.property("problems")
    finally:
        sim.shutdown()


# ---- the project's netlist choice (ProjectManager)


@pytest.fixture
def app(qapp: object, tmp_path: Path) -> Iterator[App]:
    a = start_app(tmp_path / "settings.ini")
    a.schematic.attachSimulator(a.sim)
    yield a
    a.close()


def test_drawing_does_not_ask_which_netlist_to_keep(app: App) -> None:
    app.sim.setProperty("netlistText", XOR.read_text())
    asked: list[str] = []
    app.projects.netlistChoiceRequested.connect(asked.append)
    assert app.schematic.generateFromNetlist(XOR.read_text())["ok"]
    assert asked == [] and app.projects.property("netlistSource") == ""
    assert app.projects.property("modified")  # the sheet is part of the project
    app.schematic._edit("wire", lambda s: s.wires.append(Wire(0, 0, 16, 0)))
    assert len(asked) == 1  # the first real edit asks, as before


def test_drawn_sheet_is_saved_with_the_project(app: App, tmp_path: Path) -> None:
    app.sim.setProperty("netlistText", XOR.read_text())
    assert app.schematic.generateFromNetlist(XOR.read_text())["ok"]
    path = tmp_path / "xor.qsp"
    assert app.projects.saveAs(str(path))
    sheet = app.schematic.schematic()
    app.projects.newProject()
    assert app.schematic.property("sheetEmpty")
    assert app.projects.open(str(path))
    assert app.schematic.schematic() == sheet
    assert app.sim.property("netlistText") == XOR.read_text()


# ---- with ngspice


@pytest.mark.ngspice
def test_drawn_xor_simulates_like_the_netlist(parts, qtbot) -> None:
    ctrl, sim = parts
    with qtbot.waitSignal(sim.resultReady, timeout=30000):
        assert sim.run(XOR.read_text())
    netlist_y = np.asarray(sim.result.vectors["V(y)"]).real
    assert ctrl.generateFromNetlist(XOR.read_text())["ok"]
    with qtbot.waitSignal(sim.resultReady, timeout=30000):
        assert ctrl.run()
    sheet_y = np.asarray(sim.result.vectors["V(y)"]).real
    assert sheet_y.shape == netlist_y.shape
    assert np.allclose(sheet_y, netlist_y, atol=1e-9)
    # probing a wire of the drawn y net plots V(y) (S11)
    label = next(lab for lab in ctrl.schematic().labels if lab.name == "y")
    wire = next(w for w in ctrl.schematic().wires if (w.x2, w.y2) == (label.x, label.y))
    mid_x, mid_y = (wire.x1 + wire.x2) / 2, (wire.y1 + wire.y2) / 2
    assert ctrl.probeNet(mid_x, mid_y, 4) == "y"
    assert ctrl.probeClick(mid_x, mid_y, 4, False) == "V(y)"
