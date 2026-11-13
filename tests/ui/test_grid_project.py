"""G7: the Grid tab's feeder, power-flow settings and limits are saved in the project
(.qsp format version 4) and restored when it opens."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

import bridge.grid_controller as grid_controller  # noqa: E402
from bridge.diagram_controller import DiagramController  # noqa: E402
from bridge.grid_controller import GridController  # noqa: E402
from bridge.project_manager import ProjectManager  # noqa: E402
from bridge.simulator import Simulator  # noqa: E402
from bridge.waveform_model import WaveformModel  # noqa: E402
from core.grid.config import CONTROL_OFF, PowerFlowConfig  # noqa: E402
from core.project.io import GridSettings, Project, save_project  # noqa: E402
from tests.core.grid.helpers import FIXTURES, IEEE13  # noqa: E402

RUN_TIMEOUT_MS = 60_000
LIMITS = PowerFlowConfig(control_mode=CONTROL_OFF, voltage_min_pu=0.97, voltage_max_pu=1.04)


class Parts:
    def __init__(self, settings: QSettings) -> None:
        self.sim, self.diagram, self.waveforms = Simulator(), DiagramController(), WaveformModel()
        self.grid = GridController()
        self.sim.attachWaveforms(self.waveforms)
        self.diagram.attachWaveforms(self.waveforms)
        self.grid.attachWaveforms(self.waveforms)
        self.projects = ProjectManager(settings=settings)
        self.projects.attach(self.sim, self.diagram, self.waveforms)
        self.projects.attachGrid(self.grid)

    def shutdown(self) -> None:
        self.grid.shutdown()
        self.sim.shutdown()
        self.diagram.shutdown()


@pytest.fixture
def parts(qapp: object, tmp_path: Path) -> Iterator[Parts]:
    p = Parts(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    yield p
    p.shutdown()


def copy_ieee13(folder: Path) -> Path:
    """IEEE 13 in ``folder/grid/ieee13`` with the shared linecodes one level up (its
    IEEELineCodes.DSS redirects to ../IEEELineCodes.DSS)."""
    (folder / "grid").mkdir(parents=True)
    shutil.copytree(IEEE13.parent, folder / "grid" / "ieee13")
    shutil.copyfile(FIXTURES / "IEEELineCodes.DSS", folder / "grid" / "IEEELineCodes.DSS")
    return folder / "grid" / "ieee13" / IEEE13.name


def grid_section(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))["grid"]


def wait_idle(qtbot, grid: GridController) -> None:
    qtbot.waitUntil(lambda: not grid.running, timeout=RUN_TIMEOUT_MS)


@pytest.mark.opendss
def test_feeder_and_limits_are_saved_and_restored(qtbot, parts: Parts, tmp_path: Path) -> None:
    dss = copy_ieee13(tmp_path)
    projects, grid = parts.projects, parts.grid
    assert not projects.property("modified")
    assert grid.openFile(str(dss))
    wait_idle(qtbot, grid)
    assert grid.canRun, grid.consoleText
    assert projects.property("modified")
    grid.set_config(LIMITS)
    path = tmp_path / "feeder.qsp"
    assert projects.saveAs(str(path))
    assert not projects.property("modified")
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == 4
    assert data["grid"] == GridSettings("grid/ieee13/IEEE13Nodeckt.dss", LIMITS).to_dict()

    assert projects.newProject()
    assert grid.filePath == "" and grid.config == PowerFlowConfig() and not grid.canRun

    assert projects.open(str(path))
    assert grid.filePath == str(dss.resolve())
    assert grid.config == LIMITS and grid.voltageMinPu == 0.97
    wait_idle(qtbot, grid)
    assert grid.canRun, grid.consoleText
    assert not projects.property("modified")
    grid.set_config(replace(LIMITS, loading_limit_pct=90.0))
    assert projects.property("modified")


@pytest.mark.opendss
def test_new_limits_recompute_the_violations_shown(qtbot, parts: Parts) -> None:
    grid = parts.grid
    assert grid.openFile(str(IEEE13))
    wait_idle(qtbot, grid)
    assert grid.run()
    wait_idle(qtbot, grid)
    assert grid.hasResult, grid.consoleText
    before = len(grid.violation_list())
    with qtbot.waitSignal(grid.configChanged):
        grid.set_config(PowerFlowConfig(voltage_min_pu=0.99, voltage_max_pu=1.01))
    assert grid.voltageMinPu == 0.99 and grid.voltageMaxPu == 1.01
    assert len(grid.violation_list()) > before
    assert parts.grid.profile is not None
    assert set(parts.grid.profile.vectors["v_min"]) == {0.99}


def test_feeder_is_kept_without_dss_python(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grid_controller, "engine_available", lambda: False)
    dss = copy_ieee13(tmp_path)
    path = tmp_path / "feeder.qsp"
    save_project(path, Project(grid=GridSettings("grid/ieee13/IEEE13Nodeckt.dss", LIMITS)))
    parts = Parts(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        assert parts.projects.open(str(path))
        assert "dss-python is not installed" in parts.projects.property("statusText")
        assert parts.grid.filePath == "" and parts.grid.config == LIMITS
        assert not parts.projects.property("modified")
        first = grid_section(path)
        assert parts.projects.save()
        assert grid_section(path) == first
        assert dss.is_file()
    finally:
        parts.shutdown()


def test_missing_feeder_is_reported_and_kept(parts: Parts, tmp_path: Path) -> None:
    path = tmp_path / "feeder.qsp"
    save_project(path, Project(grid=GridSettings("gone/feeder.dss", LIMITS)))
    assert parts.projects.open(str(path))
    assert "grid feeder" in parts.projects.property("statusText")
    assert "is missing" in parts.projects.property("statusText")
    first = grid_section(path)
    assert parts.projects.save()
    assert grid_section(path) == first


def test_feeder_outside_the_project_folder_is_stored_absolute(parts: Parts, tmp_path: Path) -> None:
    outside = tmp_path / "feeders" / "f.dss"
    path = tmp_path / "projects" / "p.qsp"
    path.parent.mkdir()
    save_project(path, Project(grid=GridSettings(str(outside))))
    assert parts.projects.open(str(path))
    assert parts.projects.save()
    stored = json.loads(path.read_text("utf-8"))["grid"]["dss_file"]
    assert Path(stored) == outside.resolve()
