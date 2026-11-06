"""H2: SchematicController makes circuit symbols into the project folder."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QSettings

from app.__main__ import create_engine
from bridge.schematic_controller import SchematicController
from core.schematic.model import Instance, PortFlag
from core.symbols.asy import AsyParser
from tests.ui.test_schematic_canvas import rc
from tests.ui.test_schematic_ports import RC_NETLIST


@pytest.fixture
def ctrl(qapp: object, tmp_path: Path) -> SchematicController:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    c = SchematicController(settings=settings)
    sheet = rc()
    sheet.ports = [PortFlag(64, 112, "IN", "in"), PortFlag(304, 112, "OUT", "out")]
    c.load_schematic(sheet)
    return c


def test_unsaved_project_must_be_saved_first(ctrl: SchematicController) -> None:
    r = ctrl.makeSymbol("rclp", False)
    assert r["ok"] is False and r["needsSave"] is True


def test_make_symbol_from_the_sheet(ctrl: SchematicController, tmp_path: Path, qtbot) -> None:
    ctrl.set_project_file(str(tmp_path / "proj.qsp"))
    with qtbot.waitSignal(ctrl.symbolMade) as made:
        r = ctrl.makeSymbol("rclp", False)
    assert r["ok"] is True, r
    assert r["pins"] == ["IN", "OUT"]
    assert made.args == ["rclp", str(tmp_path / "rclp.asy")]
    assert AsyParser.parse(tmp_path / "rclp.asy")[1] == []
    assert (tmp_path / "rclp.sub").is_file()


def test_existing_files_are_replaced_only_when_asked(
    ctrl: SchematicController, tmp_path: Path
) -> None:
    ctrl.set_project_file(str(tmp_path / "proj.qsp"))
    assert ctrl.makeSymbol("rclp", False)["ok"] is True
    (tmp_path / "rclp.sub").write_text("old", "utf-8")
    again = ctrl.makeSymbol("rclp", False)
    assert again["ok"] is False and again["exists"] is True
    assert (tmp_path / "rclp.sub").read_text("utf-8") == "old"
    assert ctrl.makeSymbol("rclp", True)["ok"] is True
    assert (tmp_path / "rclp.sub").read_text("utf-8").startswith("* QtSpice circuit symbol")


def test_names_that_clash_with_library_symbols_are_refused(
    ctrl: SchematicController, tmp_path: Path
) -> None:
    ctrl.set_project_file(str(tmp_path / "proj.qsp"))
    assert "already a symbol called res" in ctrl.symbolNameProblem("res")
    assert ctrl.symbolNameProblem("2bad") != ""
    assert ctrl.symbolNameProblem("rclp") == ""
    r = ctrl.makeSymbol("res", False)
    assert r["ok"] is False and not (tmp_path / "res.asy").exists()


def test_errors_are_reported(ctrl: SchematicController, tmp_path: Path) -> None:
    ctrl.set_project_file(str(tmp_path / "proj.qsp"))
    sheet = ctrl.schematic()
    sheet.ports = []
    ctrl.load_schematic(sheet)
    r = ctrl.makeSymbol("rclp", False)
    assert r["ok"] is False and "no ports" in r["message"]
    sheet.instances.append(Instance("nosuch", "U1", 800, 800))
    sheet.ports = [PortFlag(64, 112, "IN", "in")]
    ctrl.load_schematic(sheet)
    r = ctrl.makeSymbol("rclp", False)
    assert r["ok"] is False and "symbol not found: nosuch" in r["message"]


def test_make_symbol_from_a_netlist(ctrl: SchematicController, tmp_path: Path) -> None:
    ctrl.set_project_file(str(tmp_path / "proj.qsp"))
    ports = [{"node": "in", "direction": "in"}, {"node": "out", "direction": "out"}]
    r = ctrl.makeSymbolFromNetlist(RC_NETLIST, "rcnet", ports, False)
    assert r["ok"] is True, r
    sub = (tmp_path / "rcnet.sub").read_text("utf-8").splitlines()
    assert ".subckt rcnet in out" in sub and ".ac dec 10 1 1k" not in sub


# ---------------------------------------------------------------- the main window


def test_make_symbol_from_the_ports_panel_in_the_main_window(
    qapp: object, tmp_path: Path, qtbot
) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    pm = engine.singletonInstance("QtSpice", "ProjectManager")
    ctl = engine.singletonInstance("QtSpice", "SchematicController")
    try:
        assert pm.saveAs(str(tmp_path / "proj.qsp")) is True
        sheet = rc()
        sheet.ports = [PortFlag(64, 112, "IN", "in"), PortFlag(304, 112, "OUT", "out")]
        ctl.load_schematic(sheet)
        qtbot.wait(20)

        def make() -> None:
            window.findChild(QObject, "makeSymbolButton").clicked.emit()
            qtbot.wait(20)
            window.findChild(QObject, "makeSymbolName").setProperty("text", "rclp")
            qtbot.wait(20)
            window.findChild(QObject, "makeSymbolDialogOk").clicked.emit()
            qtbot.wait(20)

        make()
        result = window.findChild(QObject, "symbolResultDialog")
        assert result.property("visible") is True
        assert result.property("message").startswith("Made rclp.asy and rclp.sub")
        assert (tmp_path / "rclp.asy").is_file()
        result.close()
        qtbot.wait(20)
        names = window.findChild(QObject, "partPicker").property("names")
        assert "rclp" in list(names)  # H3: the new symbol is in the part picker

        make()  # the same name again: asks before replacing
        replace = window.findChild(QObject, "replaceSymbolDialog")
        assert replace.property("visible") is True
        replace.accept()
        qtbot.wait(20)
        assert result.property("visible") is True
        assert result.property("title") == "Symbol made"
    finally:
        ctl.load_schematic(None)
        for name in ("Simulator", "DiagramController"):
            engine.singletonInstance("QtSpice", name).shutdown()
        window.close()
        engine.deleteLater()


@pytest.mark.ngspice
def test_hierarchy_demo_runs_from_the_main_window(qapp: object, qtbot) -> None:

    demo = Path(__file__).resolve().parents[2] / "examples" / "hierarchy" / "rc2.qsp"
    engine = create_engine()
    window = engine.rootObjects()[0]
    pm = engine.singletonInstance("QtSpice", "ProjectManager")
    ctl = engine.singletonInstance("QtSpice", "SchematicController")
    sim = engine.singletonInstance("QtSpice", "Simulator")
    try:
        assert pm.open(str(demo)) is True
        assert "rclp" in ctl.placeableSymbols()
        assert ctl.check() is True, ctl.property("problems")
        with qtbot.waitSignal(sim.resultReady, timeout=10_000):
            assert ctl.run() is True
        result = sim.result
        f = np.real(result.vectors["frequency"])
        db = 20 * np.log10(np.abs(result.vectors["V(out)"]))
        assert np.interp(5.0, np.log10(f), db) - np.interp(4.0, np.log10(f), db) == pytest.approx(
            -40, abs=0.5
        )
    finally:
        ctl.load_schematic(None)
        for name in ("Simulator", "DiagramController"):
            engine.singletonInstance("QtSpice", name).shutdown()
        window.close()
        engine.deleteLater()
