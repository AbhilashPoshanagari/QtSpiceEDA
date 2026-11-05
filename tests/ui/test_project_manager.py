from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from bridge.diagram_controller import DiagramController
from bridge.project_manager import MAX_RECENT, ProjectManager
from bridge.simulator import Simulator
from bridge.waveform_model import WaveformModel
from core.project.io import load_project
from core.sim.result import SimulationResult

RC = "* RC low-pass\nV1 in 0 PULSE(0 1 0 1n 1n 1 2)\nR1 in out 1k\nC1 out 0 1u\n.end\n"
CODE = "@block\ndef double(u):\n    return 2 * u\n"


@dataclass
class App:
    """One "run" of the app: the four bridge objects, wired like Main.qml does."""

    sim: Simulator
    diagram: DiagramController
    waveforms: WaveformModel
    projects: ProjectManager

    def close(self) -> None:
        self.sim.shutdown()
        self.diagram.shutdown()


@pytest.fixture
def settings_file(tmp_path: Path) -> Path:
    return tmp_path / "settings.ini"


def start_app(settings_file: Path) -> App:
    settings = QSettings(str(settings_file), QSettings.Format.IniFormat)
    sim, diagram, waveforms = Simulator(), DiagramController(), WaveformModel()
    sim.attachWaveforms(waveforms)
    diagram.attachWaveforms(waveforms)
    projects = ProjectManager(settings=settings)
    projects.attach(sim, diagram, waveforms)
    return App(sim, diagram, waveforms, projects)


@pytest.fixture
def app(qapp: object, settings_file: Path) -> Iterator[App]:
    a = start_app(settings_file)
    yield a
    a.close()


def tran_result() -> SimulationResult:
    t = np.linspace(0, 1e-3, 101)
    return SimulationResult(
        "time", {"time": t, "v(out)": 1 - np.exp(-t / 1e-3)}, {"time": "s", "v(out)": "V"}, "tran"
    )


def scope_result() -> SimulationResult:
    t = np.arange(0, 1, 0.01)
    return SimulationResult("time", {"time": t, "Scope1.u": 2 * np.sin(t)}, {"time": "s"}, "tran")


def build_project(app: App) -> None:
    """Fill every part of a project, as a user would."""
    app.sim.setProperty("netlistText", RC)
    app.sim.setProperty("analysisCard", ".tran 10u 1m")
    assert app.diagram.apply(CODE)
    for spec, x in (("Sine", 0), ("double", 120), ("Scope", 240)):
        assert app.diagram.addBlock(spec, x, 40)
    assert app.diagram.connectPorts("Sine1.y", "double1.u")
    assert app.diagram.connectPorts("double1.y", "Scope1.u")
    assert app.diagram.setSetting("Sine1", "frequency", "50")
    app.diagram.setProperty("tEnd", 0.2)
    app.diagram.setProperty("dt", 1e-4)
    app.sim._on_finished(tran_result())  # as if ngspice had just finished
    app.diagram.restore_result(scope_result())
    app.diagram.resultReady.emit()


def assert_same_result(a: object, b: object) -> None:
    assert isinstance(a, SimulationResult) and isinstance(b, SimulationResult)
    assert (a.axis, a.units, a.analysis, list(a.vectors)) == (
        b.axis,
        b.units,
        b.analysis,
        list(b.vectors),
    )
    for name in a.vectors:
        np.testing.assert_array_equal(a.vectors[name], b.vectors[name])


def test_save_quit_reopen_gives_an_identical_project(
    qapp: object, tmp_path: Path, settings_file: Path
) -> None:
    path = tmp_path / "rc.qsp"
    first = start_app(settings_file)
    build_project(first)
    assert first.projects.property("modified") is True
    assert first.projects.save() is False, "an untitled project needs Save As"
    assert first.projects.saveAs(str(tmp_path / "rc"))  # the suffix is added
    assert first.projects.property("filePath") == str(path.resolve())
    assert first.projects.property("modified") is False
    saved = first.projects.current_project()
    saved_bytes = path.read_bytes()
    first.close()
    del first

    second = start_app(settings_file)  # "quit and reopen"
    asked: list[tuple[str, int]] = []
    second.projects.trustRequested.connect(lambda name, n: asked.append((name, n)))
    assert second.projects.open(str(path))
    assert asked == [], "a project this app saved opens without asking"
    reopened = second.projects.current_project()

    assert reopened.to_dict() == saved.to_dict()
    assert list(reopened.results) == ["netlist", "diagram"]
    for name in saved.results:
        assert_same_result(saved.results[name], reopened.results[name])
    # The bridge objects show the loaded state.
    assert second.sim.property("netlistText") == RC
    assert second.sim.property("analysisCard") == ".tran 10u 1m"
    assert second.diagram.blocks.rowCount() == 3
    assert second.diagram.wires.rowCount() == 2
    assert any(b["name"] == "double" for b in second.diagram.property("library"))
    assert second.diagram.property("tEnd") == 0.2
    assert second.diagram.property("dt") == 1e-4
    assert second.diagram.property("canUndo") is False
    assert second.waveforms.property("traceNames") == ["v(out)"]
    assert second.projects.property("modified") is False

    assert second.projects.save()
    assert path.read_bytes() == saved_bytes
    second.close()


def test_modified_flag_follows_the_saved_state(app: App, tmp_path: Path) -> None:
    assert app.projects.property("modified") is False
    app.sim.setProperty("netlistText", RC)
    assert app.projects.property("modified") is True
    app.sim.setProperty("netlistText", "")
    assert app.projects.property("modified") is False

    assert app.diagram.addBlock("Gain", 0, 0)
    assert app.projects.property("modified") is True
    app.diagram.undo()
    assert app.projects.property("modified") is False

    app.diagram.setProperty("dt", 0.5)
    assert app.projects.property("modified") is True
    assert app.projects.saveAs(str(tmp_path / "a.qsp"))
    assert app.projects.property("modified") is False

    assert app.diagram.apply(CODE)
    assert app.projects.property("modified") is True
    assert app.projects.save()
    app.sim._on_finished(tran_result())
    assert app.projects.property("modified") is True, "a new result is a change"


def test_new_project_clears_everything(app: App, tmp_path: Path) -> None:
    build_project(app)
    assert app.projects.saveAs(str(tmp_path / "a.qsp"))
    assert app.projects.newProject()
    assert app.projects.property("filePath") == ""
    assert app.projects.property("fileName") == "Untitled"
    assert app.projects.property("modified") is False
    assert app.sim.property("netlistText") == ""
    assert app.sim.result is None
    assert app.diagram.blocks.rowCount() == 0
    assert not any(b["name"] == "double" for b in app.diagram.property("library"))
    assert app.waveforms.property("traceNames") == []


def test_project_from_elsewhere_asks_before_loading_code(
    qapp: object, tmp_path: Path, settings_file: Path
) -> None:
    path = tmp_path / "shared.qsp"
    author = start_app(tmp_path / "other-machine.ini")
    build_project(author)
    assert author.projects.saveAs(str(path))
    author.close()

    app = start_app(settings_file)
    try:
        asked: list[tuple[str, int]] = []
        app.projects.trustRequested.connect(lambda name, n: asked.append((name, n)))
        assert app.projects.open(str(path))
        assert asked == [("shared.qsp", 1)]
        assert app.sim.property("netlistText") == "", "nothing loads before the answer"

        app.projects.confirmTrust(False)
        assert app.sim.property("netlistText") == RC
        assert not any(b["name"] == "double" for b in app.diagram.property("library"))
        assert [r["blockId"] for r in app.diagram.blocks.rows()] == ["Sine1", "Scope1"]
        assert app.diagram.wires.rowCount() == 0
        assert app.projects.property("filePath") == "", "the original file is not reused"
        assert "without its block code" in app.projects.property("statusText")
        assert "double1 was not loaded" in app.projects.property("statusText")

        asked.clear()
        assert app.projects.open(str(path))
        assert asked == [("shared.qsp", 1)], "declining does not trust the file"
        app.projects.confirmTrust(True)
        assert app.diagram.blocks.rowCount() == 3
        assert app.projects.property("filePath") == str(path.resolve())

        asked.clear()
        assert app.projects.open(str(path))
        assert asked == [], "once accepted, the same code is trusted"
    finally:
        app.close()


def test_changed_code_asks_again(app: App, tmp_path: Path) -> None:
    path = tmp_path / "a.qsp"
    build_project(app)
    assert app.projects.saveAs(str(path))
    text = path.read_text("utf-8").replace("2 * u", "3 * u")
    path.write_text(text, encoding="utf-8")
    asked: list[str] = []
    app.projects.trustRequested.connect(lambda name, _n: asked.append(name))
    assert app.projects.open(str(path))
    assert asked == ["a.qsp"]


def test_bad_file_reports_and_keeps_the_project(app: App, tmp_path: Path) -> None:
    app.sim.setProperty("netlistText", RC)
    bad = tmp_path / "bad.qsp"
    bad.write_text('{"format": "qtspice-project", "version": 99}', encoding="utf-8")
    assert app.projects.open(str(bad)) is False
    assert "newer QtSpice" in app.projects.property("errorString")
    assert app.sim.property("netlistText") == RC


def test_recent_files(app: App, tmp_path: Path) -> None:
    paths = [tmp_path / f"p{i}.qsp" for i in range(MAX_RECENT + 2)]
    for p in paths:
        assert app.projects.saveAs(str(p))
    recent = app.projects.property("recentFiles")
    assert recent == [str(p.resolve()) for p in reversed(paths)][:MAX_RECENT]

    assert app.projects.open(str(paths[-3]))
    assert app.projects.property("recentFiles")[0] == str(paths[-3].resolve())

    paths[-1].unlink()
    assert app.projects.open(str(paths[-1])) is False
    assert str(paths[-1].resolve()) not in app.projects.property("recentFiles")

    app.projects.clearRecentFiles()
    assert app.projects.property("recentFiles") == []


def test_extra_netlists_are_kept(app: App, tmp_path: Path) -> None:
    path = tmp_path / "multi.qsp"
    assert app.projects.saveAs(str(path))
    text = path.read_text("utf-8").replace(
        '"netlists": [\n    {\n      "name": "main",\n      "text": ""\n    }\n  ]',
        '"netlists": [{"name": "main", "text": "* a\\n"}, {"name": "spare", "text": "* b\\n"}]',
    )
    path.write_text(text, encoding="utf-8")
    assert app.projects.open(str(path))
    assert app.sim.property("netlistText") == "* a\n"
    assert app.projects.save()
    names = [n.name for n in load_project(path).project.netlists]  # type: ignore[union-attr]
    assert names == ["main", "spare"]


def test_cannot_open_while_running(app: App, tmp_path: Path) -> None:
    path = tmp_path / "a.qsp"
    assert app.projects.saveAs(str(path))
    app.sim._set_running(True)
    assert app.projects.open(str(path)) is False
    assert "Stop the running simulation" in app.projects.property("errorString")
    app.sim._set_running(False)
