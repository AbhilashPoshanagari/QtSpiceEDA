"""N0: the default library folder and ``.include <name>`` through the app.

One folder (``library/`` in the repository when running from source,
Documents/QtSpice/library in the packaged app, unless the user picks another) holds the user's
.lib files and .asy symbols. Symbols in it are listed, its models are in the library
browser, and a netlist (typed, pasted or made from a schematic) can say
``.include tsmc018.lib`` with no path.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings

import bridge.schematic_controller as sc
from bridge.schematic_controller import (
    DEFAULT_LIBRARY_KEY,
    SchematicController,
    ensure_default_library_folder,
)
from bridge.simulator import Simulator
from core.schematic.model import GroundFlag, Instance, NetLabel, Schematic, TextItem, Wire
from core.symbols.library import BUILTIN_DIR

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
TSMC = FIXTURES / "lib" / "tsmc018.lib"
CMOSN = FIXTURES / "asy" / "cmosn.asy"
XOR = Path(__file__).resolve().parents[1] / "netlists" / "good" / "cmos_xor.cir"
BUILTIN_NAMES = sorted(p.stem for p in BUILTIN_DIR.glob("*.asy"))

INVERTER = """inverter with a library model
.include tsmc018.lib
VDD vdd 0 1.8
VIN in 0 0
MP1 out in vdd vdd CMOSP W=2u L=0.18u
MN1 out in 0 0 CMOSN W=1u L=0.18u
.op
.end
"""


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A library folder as a user would fill it: the model file and a symbol."""
    folder = tmp_path / "QtSpice" / "library"
    (folder / "models").mkdir(parents=True)
    (folder / "symbols").mkdir()
    shutil.copy(TSMC, folder / "models" / "tsmc018.lib")
    shutil.copy(CMOSN, folder / "symbols" / "cmosn.asy")
    return folder


@pytest.fixture
def ctrl(qapp: object, settings: QSettings, library: Path) -> SchematicController:
    settings.setValue(DEFAULT_LIBRARY_KEY, str(library))
    return SchematicController(settings=settings)


@pytest.fixture
def sim(qapp: object):
    s = Simulator()
    yield s
    s.shutdown()


def errors(sim: Simulator) -> list[str]:
    return [d["message"] for d in sim.property("diagnostics") if d["severity"] == "error"]


# ---- the folder itself


def test_default_folder_when_none_is_chosen(qapp: object, settings: QSettings) -> None:
    ctrl = SchematicController(settings=settings)
    assert ctrl.property("defaultLibraryFolder") == str(sc.default_library_folder())


def test_from_source_the_default_is_the_repository_library(monkeypatch) -> None:
    monkeypatch.undo()  # the real function, not the autouse stand-in
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert sc.default_library_folder() == REPO / "library"
    assert (REPO / "library" / "README.md").is_file()


def test_packaged_app_uses_documents(qapp: object, monkeypatch) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    folder = sc.default_library_folder()
    assert folder.parts[-2:] == ("QtSpice", "library") and REPO not in folder.parents


def test_open_creates_the_folder_and_starts_searching_it(
    qapp: object, settings: QSettings, tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "new" / "library"
    settings.setValue(DEFAULT_LIBRARY_KEY, str(folder))
    ctrl = SchematicController(settings=settings)
    opened: list[str] = []
    monkeypatch.setattr(
        sc.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True
    )
    assert ctrl.openDefaultLibraryFolder() is True
    assert folder.is_dir() and [Path(p) for p in opened] == [folder]
    shutil.copy(CMOSN, folder / "cmosn.asy")
    ctrl.refresh()
    assert ctrl.lookup("cmosn") is not None


def test_reset_goes_back_to_the_default(ctrl: SchematicController, settings: QSettings) -> None:
    assert ctrl.property("defaultLibraryFolder") != str(sc.default_library_folder())
    ctrl.resetDefaultLibraryFolder()
    assert ctrl.property("defaultLibraryFolder") == str(sc.default_library_folder())
    assert settings.value(DEFAULT_LIBRARY_KEY) is None
    assert ctrl.lookup("cmosn") is None


def test_missing_default_folder_changes_nothing(qapp: object, settings: QSettings) -> None:
    ctrl = SchematicController(settings=settings)
    assert sorted(ctrl.property("symbolNames")) == BUILTIN_NAMES
    assert ctrl.property("libraryStatus").startswith("0 models, 0 subcircuits")
    assert "not found" not in ctrl.property("libraryStatus")


def test_ensure_creates_the_chosen_folder(tmp_path: Path, settings: QSettings) -> None:
    folder = tmp_path / "mine" / "library"
    settings.setValue(DEFAULT_LIBRARY_KEY, str(folder))
    assert ensure_default_library_folder(settings) == folder
    assert folder.is_dir()
    assert ensure_default_library_folder(settings) == folder  # already there: fine


def test_symbols_and_models_in_the_folder_are_found(ctrl: SchematicController) -> None:
    assert "symbols/cmosn" in ctrl.property("symbolNames")  # listed by path in the folder
    assert ctrl.lookup("cmosn") is not None  # found by its bare name
    assert ctrl.property("libraryStatus").startswith("2 models")
    names = {e["name"].casefold() for e in ctrl.searchLibrary("cmos")}
    assert {"cmosn", "cmosp"} <= names


def test_folder_can_be_changed_and_is_saved(
    qapp: object, settings: QSettings, library: Path, tmp_path: Path, qtbot
) -> None:
    ctrl = SchematicController(settings=settings)
    assert ctrl.lookup("cmosn") is None
    with qtbot.waitSignal(ctrl.defaultLibraryFolderChanged):
        ctrl.setProperty("defaultLibraryFolder", str(library))
    assert ctrl.lookup("cmosn") is not None
    assert settings.value(DEFAULT_LIBRARY_KEY) == str(library)
    assert SchematicController(settings=settings).property("defaultLibraryFolder") == str(library)


def test_include_folders_order(ctrl: SchematicController, tmp_path: Path, library: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    ctrl.setProperty("libraryPaths", [str(other)])
    assert ctrl.include_folders() == [library, other]


# ---- the netlist editor


def test_pasted_netlist_with_a_bare_include_checks(ctrl: SchematicController, sim) -> None:
    assert sim.check(INVERTER) is False  # no folders known yet: cannot find it
    ctrl.attachSimulator(sim)  # the app does this at start-up
    assert sim.include_folders() == tuple(ctrl.include_folders())
    assert errors(sim) == []  # re-checked when the folders arrived
    assert sim.check(INVERTER) is True


def test_folder_change_reaches_the_simulator(
    qapp: object, settings: QSettings, library: Path, sim
) -> None:
    ctrl = SchematicController(settings=settings)
    ctrl.attachSimulator(sim)
    assert sim.check(INVERTER) is False
    ctrl.setProperty("defaultLibraryFolder", str(library))
    assert errors(sim) == []


def test_absolute_include_still_works(qapp: object, settings: QSettings, sim) -> None:
    ctrl = SchematicController(settings=settings)
    ctrl.attachSimulator(sim)
    text = INVERTER.replace(".include tsmc018.lib", f'.include "{TSMC}"')
    assert sim.check(text) is True, errors(sim)


def test_file_next_to_the_netlist_still_wins(
    ctrl: SchematicController, sim, tmp_path: Path
) -> None:
    ctrl.attachSimulator(sim)
    project = tmp_path / "project"
    project.mkdir()
    (project / "tsmc018.lib").write_text(".model CMOSN NMOS\n", encoding="utf-8")
    sim.set_file_path(str(project / "inv.cir"))
    assert sim.check(INVERTER) is False  # the local file has no CMOSP
    assert any("cmosp" in e.casefold() for e in errors(sim))


# ---- the schematic


def inverter_sheet() -> Schematic:
    """MN1 from the library's cmosn symbol (ModelFile tsmc018.lib, no path, and the
    file is NOT next to the symbol), drain pulled up by R1."""
    sheet = Schematic(title="library nmos")
    sheet.instances = [
        Instance("cmosn", "MN1", 0, 0, attrs={"SpiceLine": "W=1u L=0.18u"}),
        Instance("res", "R1", 32, -96, attrs={"Value": "10k"}),
        Instance("voltage", "VDD", 160, -96, attrs={"Value": "1.8"}),
        Instance("voltage", "VG", -128, 64, attrs={"Value": "1.8"}),
    ]
    sheet.labels = [NetLabel(48, 0, "d"), NetLabel(0, 80, "g")]
    sheet.texts = [TextItem(-200, 200, ".op")]
    sheet.grounds = [GroundFlag(48, 96)]
    sheet.wires = [Wire(48, 48, 48, 96)]
    return sheet


def test_schematic_model_file_found_in_the_library(ctrl: SchematicController, sim) -> None:
    ctrl.attachSimulator(sim)
    ctrl.load_schematic(inverter_sheet())
    text = ctrl.netlist_text()
    assert ".include tsmc018.lib" in text  # left bare: not beside the symbol
    assert not [p for p in ctrl._netlist().diagnostics if "cannot find" in p.message]


# ---- with ngspice


@pytest.mark.ngspice
def test_xor_runs_with_its_models_in_the_library(
    qapp: object, settings: QSettings, tmp_path: Path, qtbot
) -> None:
    folder = tmp_path / "library"
    folder.mkdir()
    lines = XOR.read_text().splitlines()
    models = [ln for ln in lines if ln.lower().startswith(".model")]
    (folder / "xor_models.lib").write_text("\n".join(models) + "\n", encoding="utf-8")
    text = "\n".join(
        ".include xor_models.lib" if ln.lower().startswith(".model") and ln == models[0] else ln
        for ln in lines
        if ln not in models[1:]
    )
    settings.setValue(DEFAULT_LIBRARY_KEY, str(folder))
    ctrl = SchematicController(settings=settings)
    sim = Simulator()
    try:
        ctrl.attachSimulator(sim)
        assert sim.check(text), errors(sim)
        with qtbot.waitSignal(sim.resultReady, timeout=30000):
            assert sim.run(text)
        with_lib = np.asarray(sim.result.vectors["V(y)"]).real
        with qtbot.waitSignal(sim.resultReady, timeout=30000):
            assert sim.run(XOR.read_text())
        inline = np.asarray(sim.result.vectors["V(y)"]).real
        assert with_lib.shape == inline.shape
        assert np.allclose(with_lib, inline, atol=1e-9)
    finally:
        sim.shutdown()
