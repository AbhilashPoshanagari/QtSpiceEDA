"""L8: SchematicController's library side (search paths, search, use with symbol)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QUrl

from bridge.schematic_controller import SchematicController
from core.schematic.model import PartInstance
from core.schematic.netlister import netlist_part
from core.symbols.library import BUILTIN_DIR, SymbolLibrary

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LIB = FIXTURES / "lib"
ASY = FIXTURES / "asy"
BUILTIN_NAMES = sorted(p.stem for p in BUILTIN_DIR.glob("*.asy"))


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def ctrl(qapp: object, settings: QSettings) -> SchematicController:
    return SchematicController(settings=settings)


def _one(results: list[dict], name: str) -> dict:
    [entry] = [e for e in results if e["name"].casefold() == name.casefold()]
    return entry


def test_starts_with_builtin_symbols_and_no_library(ctrl: SchematicController) -> None:
    assert sorted(ctrl.property("symbolNames")) == BUILTIN_NAMES
    assert ctrl.property("symbolPaths") == [] and ctrl.property("libraryPaths") == []
    assert ctrl.searchLibrary("") == []
    assert ctrl.property("libraryStatus").startswith("0 models, 0 subcircuits")


def test_symbol_path_change_refreshes_the_list(ctrl: SchematicController, qtbot) -> None:
    with qtbot.waitSignal(ctrl.symbolsChanged):
        ctrl.setProperty("symbolPaths", [str(ASY)])
    names = ctrl.property("symbolNames")
    assert {"opamp5", "cmosn", "cmosp"} <= set(names)
    assert [p["name"] for p in ctrl.symbolPins("opamp5")] == ["In+", "In-", "V+", "V-", "OUT"]
    with qtbot.waitSignal(ctrl.symbolsChanged):
        ctrl.removeFolder("symbols", 0)
    assert sorted(ctrl.property("symbolNames")) == BUILTIN_NAMES
    assert ctrl.symbolPins("opamp5") == []


def test_library_path_change_refreshes_search(ctrl: SchematicController, qtbot) -> None:
    with qtbot.waitSignal(ctrl.libraryChanged):
        ctrl.addFolder("libraries", QUrl.fromLocalFile(str(LIB)).toString())
    assert ctrl.property("libraryPaths") == [str(LIB)]
    opamp = _one(ctrl.searchLibrary("opamp"), "opamp5")
    assert opamp["kind"] == "subckt"
    assert opamp["pins"] == ["inp", "inn", "vcc", "vee", "out"]
    assert opamp["file"] == str(LIB / "opamp.lib") and opamp["fileName"] == "opamp.lib"
    cmosn = _one(ctrl.searchLibrary("cmos"), "CMOSN")
    assert (cmosn["kind"], cmosn["type"].upper()) == ("model", "NMOS")
    assert len(ctrl.searchLibrary("nmos")) >= 1  # the model type matches too
    assert "encrypted" in ctrl.property("libraryStatus")
    with qtbot.waitSignal(ctrl.libraryChanged):
        ctrl.setProperty("libraryPaths", [])
    assert ctrl.searchLibrary("opamp") == []


def test_paths_are_kept_in_settings(qapp: object, settings: QSettings, tmp_path: Path) -> None:
    first = SchematicController(settings=settings)
    first.setProperty("symbolPaths", [str(ASY), str(ASY), " "])
    first.setProperty("libraryPaths", [str(LIB)])
    first.setLtspiceFolderUrl(str(tmp_path))
    second = SchematicController(settings=settings)
    assert second.property("symbolPaths") == [str(ASY)]
    assert second.property("libraryPaths") == [str(LIB)]
    assert second.property("ltspiceFolder") == str(tmp_path)
    assert "opamp5" in second.property("symbolNames")


def test_ltspice_folder_adds_symbols_and_models(ctrl: SchematicController, tmp_path: Path) -> None:
    lt = tmp_path / "LTspice" / "lib"
    (lt / "sym" / "Opamps").mkdir(parents=True)
    (lt / "cmp").mkdir()
    (lt / "sym" / "Opamps" / "myamp.asy").write_bytes((ASY / "opamp5.asy").read_bytes())
    (lt / "cmp" / "standard.bjt").write_text(".model 2N3904 NPN(IS=1e-14)\n", encoding="utf-8")
    ctrl.setLtspiceFolderUrl(QUrl.fromLocalFile(str(lt)).toString())
    assert "Opamps/myamp" in ctrl.property("symbolNames")
    entry = _one(ctrl.searchLibrary("2n39"), "2N3904")
    assert ctrl.suggestedSymbol(entry) == "npn"
    ctrl.setLtspiceFolderUrl("")
    assert "Opamps/myamp" not in ctrl.property("symbolNames")
    assert ctrl.searchLibrary("2n39") == []


def test_use_subcircuit_with_symbol_fills_attrs(ctrl: SchematicController, qtbot) -> None:
    ctrl.setProperty("libraryPaths", [str(LIB)])
    opamp = _one(ctrl.searchLibrary("opamp5"), "opamp5")
    assert ctrl.suggestedSymbol(opamp) == "opamp"
    with qtbot.waitSignal(ctrl.pendingPartChanged):
        result = ctrl.useWithSymbol(opamp, "opamp")
    assert result["ok"] and result["message"] == ""
    assert result["attrs"] == {"SpiceModel": "opamp5", "ModelFile": str(LIB / "opamp.lib")}
    assert ctrl.property("pendingPart") == {"symbol": "opamp", "attrs": result["attrs"]}

    # The pending part netlists through L7 with the library included.
    symbol, _ = SymbolLibrary().lookup("opamp")
    part = PartInstance("U1", symbol, ["p", "n", "vcc", "vee", "o"], result["attrs"])
    out = netlist_part(part)
    assert out.line == "XU1 p n vcc vee o opamp5"
    assert out.includes == [f".include {LIB / 'opamp.lib'}"]


def test_use_with_wrong_symbol_is_refused(ctrl: SchematicController) -> None:
    ctrl.setProperty("libraryPaths", [str(LIB)])
    opamp = _one(ctrl.searchLibrary("opamp5"), "opamp5")
    result = ctrl.useWithSymbol(opamp, "res")
    assert not result["ok"] and "Prefix X" in result["message"]
    cmosn = _one(ctrl.searchLibrary("cmosn"), "CMOSN")
    assert ctrl.suggestedSymbol(cmosn) == "nmos"
    assert not ctrl.useWithSymbol(cmosn, "npn")["ok"]
    assert ctrl.useWithSymbol(cmosn, "nmos")["ok"]
    assert not ctrl.useWithSymbol(cmosn, "no-such-symbol")["ok"]
    assert ctrl.property("pendingPart")["symbol"] == "nmos"


def test_use_with_warnings(ctrl: SchematicController, tmp_path: Path) -> None:
    sym = tmp_path / "sym"
    sym.mkdir()
    pins = "".join(f"PIN 0 {16 * i} NONE 0\nPINATTR SpiceOrder {i + 1}\n" for i in range(3))
    (sym / "amp3.asy").write_text("SYMATTR Prefix X\n" + pins, encoding="cp1252")
    ctrl.setProperty("symbolPaths", [str(sym)])
    ctrl.setProperty("libraryPaths", [str(LIB)])
    result = ctrl.useWithSymbol(_one(ctrl.searchLibrary("opamp5"), "opamp5"), "amp3")
    assert result["ok"] and "5 pins" in result["message"]
    corner = ctrl.searchLibrary("dcorner")[0]
    assert ctrl.suggestedSymbol(corner) == "diode"
    result = ctrl.useWithSymbol(corner, "diode")
    assert result["ok"] and ".lib section" in result["message"]
