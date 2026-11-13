"""M7: the parameter sweep in DiagramController, the waveform window and the project."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from bridge.diagram_controller import DiagramController, DiagramState
from bridge.project_manager import ProjectManager
from bridge.simulator import Simulator
from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from core.project.io import Project, load_project, save_project
from core.sim.result import SimulationResult


@pytest.fixture
def ctl(qapp: object) -> Iterator[DiagramController]:
    controller = DiagramController()
    yield controller
    controller.shutdown()


def _gain(ctl: DiagramController) -> None:
    """Constant1 (2) -> Gain1 -> Scope1."""
    for i, name in enumerate(("Constant", "Gain", "Scope")):
        ctl.addBlock(name, 160 * i, 100)
    assert ctl.setSetting("Constant1", "value", "2")
    assert ctl.connectPorts("Constant1.y", "Gain1.u")
    assert ctl.connectPorts("Gain1.y", "Scope1.u")


# ---- DiagramController


def test_set_sweep_checks_block_parameter_and_values(ctl: DiagramController) -> None:
    _gain(ctl)
    assert ctl.sweepBlocks() == ["Constant1", "Gain1"]
    assert ctl.sweepParameters("Gain1") == ["k"]
    assert ctl.sweepParameters("Scope1") == []
    assert "no block" in ctl.setSweep("Gain9", "k", "1 2")
    assert "no parameter" in ctl.setSweep("Gain1", "gain", "1 2")
    assert "not a number" in ctl.setSweep("Gain1", "k", "1 lots")
    assert ctl.property("sweepBlock") == ""
    assert ctl.setSweep("Gain1", "k", " 1 2m 3 ") == ""
    assert ctl.property("sweepBlock") == "Gain1"
    assert ctl.property("sweepParameter") == "k"
    assert ctl.property("sweepValues") == "1 2m 3"
    ctl.clearSweep()
    assert ctl.property("sweepBlock") == ""


def test_run_sweep_without_a_sweep_does_not_start(ctl: DiagramController) -> None:
    _gain(ctl)
    assert ctl.runSweep(0.1, 0.01) is False
    assert "no parameter sweep" in ctl.property("runStatus")


def test_run_sweep_overlays_labelled_runs(ctl: DiagramController, qtbot) -> None:
    model, plots = WaveformModel(), WaveformController()
    ctl.attachWaveforms(model)
    plots.attach(model)
    _gain(ctl)
    assert ctl.setSweep("Gain1", "k", "1 2 3") == ""
    with qtbot.waitSignal(ctl.resultReady, timeout=60_000):
        assert ctl.runSweep(0.1, 0.01) is True
    assert ctl.property("runStatus") == "Finished: 3 runs (k=1, k=2, k=3)"
    assert "Parameter sweep: 3 runs of Gain1.k = 1, 2, 3" in ctl.property("consoleText")
    result = ctl.result
    assert result.runs == ["k=1", "k=2", "k=3"]  # type: ignore[union-attr]
    for k in (1, 2, 3):
        np.testing.assert_allclose(result.vectors[f"Scope1@{k}"], 2.0 * k)  # type: ignore[union-attr]
    (pane,) = plots.property("panes")
    assert [t["label"] for t in pane["traces"]] == [
        "Scope1  k=1",
        "Scope1  k=2",
        "Scope1  k=3",
    ]
    # a plain run afterwards is a single run again
    with qtbot.waitSignal(ctl.resultReady, timeout=60_000):
        assert ctl.run(0.1, 0.01) is True
    assert ctl.result.runs == []  # type: ignore[union-attr]
    assert plots.property("runs") == []


def test_stop_ends_a_sweep(ctl: DiagramController, qtbot) -> None:
    assert ctl.apply("@block\ndef hang(u):\n    while True:\n        pass\n")
    ctl.addBlock("Constant", 0, 0)
    ctl.addBlock("hang", 200, 0)
    ctl.addBlock("Scope", 400, 0)
    ctl.connectPorts("Constant1.y", "hang1.u")
    ctl.connectPorts("hang1.y", "Scope1.u")
    assert ctl.setSweep("Constant1", "value", "1 2 3") == ""
    assert ctl.runSweep(1.0, 0.1) is True
    qtbot.wait(500)
    ctl.stop()
    qtbot.waitUntil(lambda: not ctl.property("running"), timeout=10_000)
    assert ctl.property("runStatus") == "Stopped"


# ---- the waveform window


def sweep_result() -> SimulationResult:
    t = np.linspace(0.0, 1.0, 11)
    vectors = {"time": t}
    units = {"time": "s"}
    for k in (1, 2, 3):
        vectors[f"inertia.A.w@{k}"] = k * t
        units[f"inertia.A.w@{k}"] = "rad/s"
        vectors[f"speed@{k}"] = k * t
        units[f"speed@{k}"] = "rad/s"
    return SimulationResult(
        axis="time",
        vectors=vectors,
        units=units,
        analysis="cosim",
        runs=["J=0.002", "J=0.005", "J=0.01"],
    )


@pytest.fixture
def plots(qapp: object) -> Iterator[tuple[WaveformModel, WaveformController]]:
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    model.set_result(sweep_result())
    yield model, ctl


def pane_traces(ctl: WaveformController, pane: int = 0) -> list[str]:
    return [t["name"] for t in ctl.property("panes")[pane]["traces"]]


def test_trace_list_and_add_trace_show_base_names(plots) -> None:
    _, ctl = plots
    assert ctl.property("runs") == ["J=0.002", "J=0.005", "J=0.01"]
    assert ctl.vectorNames("") == ["inertia.A.w", "speed"]
    assert ctl.groupedVectorNames("") == ["speed", "inertia.A.w"]
    rows = ctl.property("resultTraces")
    assert [r["name"] for r in rows] == ["inertia.A.w", "speed"]
    assert all(r["shown"] for r in rows)  # the first run shows every vector


def test_adding_a_base_name_adds_every_run(plots) -> None:
    _, ctl = plots
    assert ctl.setTraceShown("speed", False)
    assert ctl.setTraceShown("inertia.A.w", False)
    assert pane_traces(ctl) == []
    assert ctl.addTrace("inertia.A.w")
    assert pane_traces(ctl) == ["inertia.A.w@1", "inertia.A.w@2", "inertia.A.w@3"]
    (pane,) = ctl.property("panes")
    assert pane["traces"][1]["label"] == "inertia.A.w  J=0.005"
    assert len({t["color"] for t in pane["traces"]}) == 3
    assert pane["yLabel"] == "[rad/s]"
    ctl.undo()  # one undo step for all runs
    assert pane_traces(ctl) == []
    assert ctl.dropTrace(-1, "speed", 0, False)
    assert pane_traces(ctl) == ["speed@1", "speed@2", "speed@3"]


def test_an_expression_is_plotted_once_per_run(plots) -> None:
    _, ctl = plots
    assert ctl.checkExpression("2*speed") == ""
    assert ctl.plotExpression("2*speed", 0) == ""
    names = pane_traces(ctl)
    assert names[-3:] == ["2*speed@1", "2*speed@2", "2*speed@3"]
    store = ctl._store  # noqa: SLF001 - the computed values
    for k in (1, 2, 3):
        np.testing.assert_allclose(store.raw(f"2*speed@{k}"), 2 * k * np.linspace(0, 1, 11))
    assert ctl.plotExpression("2*speed", 0) == "the pane already shows this trace"


def test_cursor_readout_names_the_run(plots) -> None:
    _, ctl = plots
    assert ctl.attachCursor(1, 0, "speed@2")
    (cursor,) = ctl.property("cursors")
    assert cursor["label"] == "speed  J=0.005"


# ---- the project


def test_sweep_is_kept_in_the_diagram_state(ctl: DiagramController) -> None:
    _gain(ctl)
    assert ctl.setSweep("Gain1", "k", "1 2") == ""
    state = ctl.export_state()
    assert state.sweep == {"block": "Gain1", "parameter": "k", "values": "1 2"}
    other = DiagramController()
    try:
        assert other.load_state(state) == []
        assert other.property("sweepValues") == "1 2"
        bad = DiagramState(diagram=state.diagram, sweep={"block": "x", "parameter": "k"})
        warnings = other.load_state(bad)
        assert any("parameter sweep was not loaded" in w for w in warnings)
        assert other.property("sweepBlock") == ""
    finally:
        other.shutdown()


def test_project_file_keeps_the_sweep(tmp_path: Path) -> None:
    project = Project(sweep={"block": "inertia", "parameter": "J", "values": "1m 2m 5m"})
    save_project(tmp_path / "p.qsp", project)
    loaded = load_project(tmp_path / "p.qsp")
    assert loaded.ok and loaded.project is not None
    assert loaded.project.sweep == {"block": "inertia", "parameter": "J", "values": "1m 2m 5m"}
    save_project(tmp_path / "none.qsp", Project())
    assert "sweep" not in (tmp_path / "none.qsp").read_text(encoding="utf-8")
    text = (tmp_path / "p.qsp").read_text(encoding="utf-8").replace('"values"', '"vals"')
    (tmp_path / "bad.qsp").write_text(text, encoding="utf-8")
    bad = load_project(tmp_path / "bad.qsp")
    assert not bad.ok and "sweep must have" in bad.errors[0]


def test_project_manager_saves_and_opens_the_sweep(qapp: object, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    sim, diagram, model = Simulator(), DiagramController(), WaveformModel()
    try:
        projects = ProjectManager(settings=settings)
        projects.attach(sim, diagram, model)
        _gain(diagram)
        assert projects.saveAs(str(tmp_path / "g.qsp"))
        assert diagram.setSweep("Gain1", "k", "1 2 3") == ""
        assert projects.property("modified")
        assert projects.save()
        assert projects.newProject()
        assert diagram.property("sweepBlock") == ""
        assert projects.open(str(tmp_path / "g.qsp"))
        assert diagram.property("sweepValues") == "1 2 3"
    finally:
        sim.shutdown()
        diagram.shutdown()


# ---- the M7 acceptance case: sweeping J gives three labelled speed curves


@pytest.mark.ngspice
def test_sweeping_j_shows_three_labelled_speed_curves(ctl: DiagramController, qtbot) -> None:
    model, plots = WaveformModel(), WaveformController()
    ctl.attachWaveforms(model)
    plots.attach(model)
    ids = {
        "ref": ctl.addBlock("MechanicalRotationalReference", 0, 300),
        "solver": ctl.addBlock("SolverConfiguration", 160, 300),
        "inertia": ctl.addBlock("Inertia", 0, 0),
        "damper": ctl.addBlock("RotationalDamper", 160, 0),
        "torque": ctl.addBlock("TorqueSource", 320, 0),
        "sensor": ctl.addBlock("AngularVelocitySensor", 480, 0),
        "scope": ctl.addBlock("Scope", 640, 0),
    }
    pairs = [
        ("solver.port", "ref.R"),
        ("inertia.A", "damper.A"),
        ("damper.B", "ref.R"),
        ("torque.A", "inertia.A"),
        ("torque.B", "ref.R"),
        ("sensor.A", "inertia.A"),
        ("sensor.B", "ref.R"),
    ]
    for a, b in pairs:
        (ka, pa), (kb, pb) = a.split("."), b.split(".")
        assert ctl.connectPhysical(f"{ids[ka]}.{pa}", f"{ids[kb]}.{pb}"), (a, b)
    assert ctl.connectPorts(f"{ids['sensor']}.w", f"{ids['scope']}.u")
    assert ctl.setSetting(ids["scope"], "label", "w")
    assert ctl.setSetting(ids["damper"], "b", "10m")
    assert ctl.setSetting(ids["torque"], "tau", "0.5")
    assert ctl.setSweep(ids["inertia"], "J", "2m 5m 10m") == ""
    ctl.timeout = 120
    with qtbot.waitSignal(ctl.resultReady, timeout=120_000):
        assert ctl.runSweep(0.5, 1e-3) is True
    assert ctl.property("runStatus") == "Finished: 3 runs (J=0.002, J=0.005, J=0.01)"
    for pane in range(len(plots.property("panes"))):
        for name in [t["name"] for t in plots.property("panes")[pane]["traces"]]:
            plots.removeTrace(pane, name)
    assert plots.addTrace("w")
    (pane,) = plots.property("panes")
    assert [t["label"] for t in pane["traces"]] == ["w  J=0.002", "w  J=0.005", "w  J=0.01"]
    assert len({t["color"] for t in pane["traces"]}) == 3
    result = ctl.result
    t = result.vectors["time"]  # type: ignore[union-attr]
    for k, j in enumerate((2e-3, 5e-3, 10e-3), start=1):
        w = result.vectors[f"w@{k}"]  # type: ignore[union-attr]
        # Scope samples, held between ticks: within one tick of the analytic curve
        np.testing.assert_allclose(w, 50.0 * (1 - np.exp(-t * 0.01 / j)), atol=0.5)
