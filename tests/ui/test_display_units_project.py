"""O1: the waveform window's rpm/degC toggle is saved in and restored from the project."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings

from bridge.diagram_controller import DiagramController
from bridge.project_manager import ProjectManager
from bridge.simulator import Simulator
from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel


def test_display_units_are_saved_and_restored(qapp: object, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    sim, diagram, model = Simulator(), DiagramController(), WaveformModel()
    ctl = WaveformController()
    try:
        sim.attachWaveforms(model)
        diagram.attachWaveforms(model)
        projects = ProjectManager(settings=settings)
        projects.attach(sim, diagram, model)
        ctl.attach(model)
        projects.attachPlots(ctl)
        assert not projects.property("modified")
        ctl.setProperty("displayUnits", True)
        assert projects.property("modified")
        path = tmp_path / "u.qsp"
        assert projects.saveAs(str(path))
        assert json.loads(path.read_text("utf-8"))["display_units"] is True
        assert not projects.property("modified")

        assert projects.newProject()
        assert ctl.property("displayUnits") is False
        assert projects.open(str(path))
        assert ctl.property("displayUnits") is True
        assert not projects.property("modified")
        ctl.setProperty("displayUnits", False)
        assert projects.property("modified")
    finally:
        sim.shutdown()
        diagram.shutdown()
