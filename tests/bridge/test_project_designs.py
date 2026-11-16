"""D9: ProjectManager keeps the Grid Network Designer's designs in the project (.qsp
format version 5): .qgrid paths, the active design and each design's own settings and
limits. A project without designs stays version 4. No OpenDSS needed."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from bridge.diagram_controller import DiagramController  # noqa: E402
from bridge.grid_controller import GridController  # noqa: E402
from bridge.grid_diagram_controller import GridDiagramController  # noqa: E402
from bridge.project_manager import ProjectManager  # noqa: E402
from bridge.simulator import Simulator  # noqa: E402
from bridge.waveform_model import WaveformModel  # noqa: E402
from core.grid.config import CONTROL_OFF, PowerFlowConfig  # noqa: E402
from core.project.io import GridDesignSettings, load_project  # noqa: E402
from tests.bridge.test_grid_diagram_controller import small_design  # noqa: E402

LIMITS_A = PowerFlowConfig(voltage_min_pu=0.93, line_loading_limit_pct=80.0)
LIMITS_B = PowerFlowConfig(control_mode=CONTROL_OFF, transformer_loading_limit_pct=120.0)


class Parts:
    def __init__(self, settings: QSettings) -> None:
        self.sim, self.diagram, self.waveforms = Simulator(), DiagramController(), WaveformModel()
        self.grid = GridController()
        self.designer = GridDiagramController(grid_controller=self.grid)
        self.projects = ProjectManager(settings=settings)
        self.projects.attach(self.sim, self.diagram, self.waveforms)
        self.projects.attachGrid(self.grid)
        self.projects.attachDesigner(self.designer)

    def shutdown(self) -> None:
        self.designer.shutdown()
        self.grid.shutdown()
        self.sim.shutdown()
        self.diagram.shutdown()


@pytest.fixture
def parts(qapp: object, tmp_path: Path) -> Iterator[Parts]:
    p = Parts(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    yield p
    p.shutdown()


def designs_of(path: Path) -> list[GridDesignSettings]:
    loaded = load_project(path)
    assert loaded.ok and loaded.project is not None, loaded.errors
    return loaded.project.grid_designs


def test_an_unsaved_design_is_not_part_of_the_project(parts: Parts, tmp_path: Path) -> None:
    small_design(parts.designer)
    assert not parts.projects.property("modified")
    path = tmp_path / "p.qsp"
    assert parts.projects.saveAs(str(path))
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == 4 and "grid_designs" not in data


def test_saved_designs_are_listed_with_their_settings(parts: Parts, tmp_path: Path) -> None:
    projects, designer = parts.projects, parts.designer
    qsp = tmp_path / "p.qsp"
    assert projects.saveAs(str(qsp))
    small_design(designer)
    designer.set_config(LIMITS_A)
    (tmp_path / "designs").mkdir()
    assert designer.save(str(tmp_path / "designs" / "a"))  # .qgrid is added
    assert projects.property("modified")
    assert projects.save()
    assert not projects.property("modified")
    data = json.loads(qsp.read_text("utf-8"))
    assert data["version"] == 5
    assert data["grid_designs"] == [GridDesignSettings("designs/a.qgrid", True, LIMITS_A).to_dict()]
    # the run scripts go to the project's results folder
    script = designer.script_path()
    assert script == tmp_path / "p_results" / "grid" / "d1" / "d1.dss"

    designer.set_config(LIMITS_B)  # a settings change is a project change
    assert projects.property("modified")
    designer.set_config(LIMITS_A)
    assert not projects.property("modified")

    # Save As gives a second design file, the active one now
    assert designer.save(str(tmp_path / "b.qgrid"))
    designer.set_config(LIMITS_B)
    assert projects.save()
    assert designs_of(qsp) == [
        GridDesignSettings("designs/a.qgrid", False, LIMITS_A),
        GridDesignSettings("b.qgrid", True, LIMITS_B),
    ]

    # opening a listed design brings its own settings back
    assert designer.load(str(tmp_path / "designs" / "a.qgrid"))
    assert designer.config == LIMITS_A
    assert projects.property("modified")  # the active design changed
    assert designer.load(str(tmp_path / "b.qgrid"))
    assert designer.config == LIMITS_B
    assert not projects.property("modified")


def test_opening_a_project_loads_its_active_design(parts: Parts, tmp_path: Path) -> None:
    projects, designer = parts.projects, parts.designer
    qsp = tmp_path / "p.qsp"
    small_design(designer)
    designer.set_config(LIMITS_B)
    assert designer.save(str(tmp_path / "net.qgrid"))
    assert projects.saveAs(str(qsp))
    before = designer.design()

    assert projects.newProject()  # no designs: the designer is closed
    assert not designer.hasDesign and designer.config == PowerFlowConfig()
    assert designer.script_path() is None
    assert projects.newProject() and not projects.property("modified")

    assert projects.open(str(qsp)), projects.property("errorString")
    assert designer.hasDesign and designer.filePath == str((tmp_path / "net.qgrid").resolve())
    assert designer.design() == before
    assert designer.config == LIMITS_B
    assert not designer.modified and not designer.canUndo
    assert not projects.property("modified")
    assert designer.script_path() == tmp_path / "p_results" / "grid" / "d1" / "d1.dss"
    assert projects.save()
    assert designs_of(qsp) == [GridDesignSettings("net.qgrid", True, LIMITS_B)]


def test_a_missing_design_file_is_kept_and_reported(parts: Parts, tmp_path: Path) -> None:
    projects, designer = parts.projects, parts.designer
    qsp = tmp_path / "p.qsp"
    small_design(designer)
    assert designer.save(str(tmp_path / "gone.qgrid"))
    assert projects.saveAs(str(qsp))
    (tmp_path / "gone.qgrid").unlink()
    assert projects.newProject()

    assert projects.open(str(qsp))
    assert "grid design" in projects.property("statusText")
    assert "missing" in projects.property("statusText")
    assert not designer.hasDesign
    assert not projects.property("modified")
    assert projects.save()
    assert designs_of(qsp) == [GridDesignSettings("gone.qgrid", True)]


def test_a_busy_designer_blocks_replacing_the_project(parts: Parts) -> None:
    parts.designer._running = True  # a run in flight (the property reads this flag)
    try:
        assert not parts.projects.newProject()
        assert "power flow" in parts.projects.property("errorString")
    finally:
        parts.designer._running = False


def test_close_design_clears_everything(parts: Parts) -> None:
    designer = parts.designer
    small_design(designer)
    assert designer.hasDesign and designer.canUndo
    designer.closeDesign()
    assert not designer.hasDesign and not designer.canUndo and not designer.modified
    assert designer.elementCount == 0 and designer.busCount == 0
    assert designer.filePath == "" and designer.errorCount == 0
    assert math.isclose(designer.baseFrequency, 0.0)
