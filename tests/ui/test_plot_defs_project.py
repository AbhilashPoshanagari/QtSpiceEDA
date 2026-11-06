"""Plot definitions (W9) saved in and restored from the project by ProjectManager."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from bridge.diagram_controller import DiagramController
from bridge.project_manager import ProjectManager
from bridge.simulator import Simulator
from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from core.project.io import load_project
from core.waveform.plotdefs import DEFAULT_TEXT
from tests.ui.fakes import tran_result


def test_plot_defs_are_saved_in_the_project(qapp: object, tmp_path: Path) -> None:
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
        sim.restore_result(tran_result())
        text = ".func twice(x) 2*x\n"
        ctl.setPlotDefs(text)
        ctl.plotExpression("twice(V(out))", 0)
        assert projects.property("modified")
        path = tmp_path / "w.qsp"
        assert projects.saveAs(str(path))
        loaded = load_project(path)
        assert loaded.project is not None and loaded.project.plot_defs == text
        assert loaded.project.plots["tran"].panes[0].traces[-1] == "twice(V(out))"

        assert projects.newProject()
        assert ctl.property("plotDefs") == DEFAULT_TEXT
        assert projects.open(str(path))
        assert ctl.property("plotDefs") == text and not ctl.property("canUndo")
    finally:
        sim.shutdown()
        diagram.shutdown()
