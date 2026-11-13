"""O2: the v0.3 physical samples open in the app's bridge objects: every block and
physical wire is on the canvas, the step is the Solver Configuration's Ts, the
rpm/degC toggle follows the project, and nothing counts as modified."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from bridge.diagram_controller import DiagramController
from bridge.project_manager import ProjectManager
from bridge.simulator import Simulator
from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from tests.fixtures.physical.samples import SAMPLES, sample_project

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.parametrize("name", list(SAMPLES))
def test_physical_sample_opens_on_the_canvas(qapp: object, tmp_path: Path, name: str) -> None:
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
        assert projects.open(str(EXAMPLES / name)), projects.property("errorString")
        expected = sample_project(name)
        data = expected.diagrams[0].diagram
        assert diagram.property("blocks").rowCount() == len(data["blocks"])
        assert diagram.property("physicalWires").rowCount() == len(data["physical_wires"])
        assert diagram.property("dt") == expected.analysis.diagram_dt
        assert diagram.property("tEnd") == expected.analysis.diagram_t_end
        assert ctl.property("displayUnits") is expected.display_units
        assert not projects.property("modified")
    finally:
        sim.shutdown()
        diagram.shutdown()
