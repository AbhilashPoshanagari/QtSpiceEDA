"""U3: one Run button for mixed diagrams, network checks first, messages in the Console."""

from __future__ import annotations

import pytest

from bridge.diagram_controller import DiagramController
from bridge.waveform_model import WaveformModel
from tests.ui.speed_loop import build_speed_loop, steady_speed


@pytest.fixture
def ctl(qapp: object) -> DiagramController:
    c = DiagramController()
    yield c
    c.shutdown()


def marked(ctl: DiagramController) -> set[str]:
    return {r["blockId"] for r in ctl.blocks.rows() if r["hasError"]}


def test_missing_reference_does_not_start_and_names_the_block(ctl: DiagramController) -> None:
    ids = build_speed_loop(ctl, reference=False)
    assert ctl.run(1.0, 1e-3) is False
    assert ctl.property("running") is False
    status = ctl.property("runStatus")
    assert status.startswith("Not started: ")
    assert "reference" in status.lower()
    named = marked(ctl)
    assert named and named <= set(ids.values())
    assert any(block in status for block in named)
    console = ctl.property("consoleText")
    assert "Physical network check failed" in console
    assert all(block in console for block in named)


def test_error_marks_clear_on_the_next_run(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 0, 0)
    r = ctl.addBlock("MechanicalRotationalReference", 200, 0)
    ctl.connectPhysical(f"{j}.A", f"{r}.R")
    ctl.run(1.0, 1e-3)
    assert marked(ctl)  # no Solver Configuration
    s = ctl.addBlock("SolverConfiguration", 200, 200)
    ctl.connectPhysical(f"{s}.port", f"{r}.R")
    ctl.ngspice_available = lambda: False
    assert ctl.run(1.0, 1e-3) is False  # passes the checks, then stops at ngspice
    assert "ngspice" in ctl.property("runStatus")
    assert marked(ctl) == set()


def test_missing_solver_names_a_block(ctl: DiagramController) -> None:
    j = ctl.addBlock("Inertia", 0, 0)
    r = ctl.addBlock("MechanicalRotationalReference", 200, 0)
    ctl.connectPhysical(f"{j}.A", f"{r}.R")
    assert ctl.run(1.0, 1e-3) is False
    assert "Solver Configuration" in ctl.property("runStatus")
    assert marked(ctl)


def test_ts_below_ten_microseconds_is_refused(ctl: DiagramController) -> None:
    build_speed_loop(ctl)
    assert ctl.run(0.01, 5e-6) is False
    status = ctl.property("runStatus")
    assert status.startswith("Not started") and "Ts" in status and "1e-05" in status
    assert "Not started" in ctl.property("consoleText")


def test_signal_diagram_keeps_small_steps(ctl: DiagramController) -> None:
    ctl.addBlock("Constant", 0, 0)
    ctl.addBlock("Scope", 200, 0)
    ctl.connectPorts("Constant1.y", "Scope1.u")
    ctl.timeout = 30
    assert ctl.run(1e-4, 1e-6) is True  # no physical blocks: no 10 us limit


def test_missing_ngspice_is_refused(ctl: DiagramController) -> None:
    build_speed_loop(ctl)
    ctl.ngspice_available = lambda: False
    assert ctl.run(0.1, 1e-3) is False
    assert "ngspice is not available" in ctl.property("runStatus")
    assert marked(ctl) == set()


def test_solver_ts_mismatch_is_reported(ctl: DiagramController, qtbot) -> None:
    ids = build_speed_loop(ctl)
    ctl.setSetting(ids["solver"], "Ts", "2m")
    ctl.ngspice_available = lambda: True
    started = ctl.run(0.05, 1e-3)
    if started:  # the Ts check happens in the run child: wait for its message
        qtbot.waitUntil(lambda: not ctl.property("running"), timeout=60000)
    assert "Ts" in ctl.property("runStatus") and "same value" in ctl.property("runStatus")
    assert "same value" in ctl.property("consoleText")


def test_signal_run_writes_engine_lines_to_the_console(ctl: DiagramController, qtbot) -> None:
    ctl.addBlock("Constant", 0, 0)
    ctl.addBlock("Scope", 200, 0)
    ctl.connectPorts("Constant1.y", "Scope1.u")
    assert ctl.run(0.01, 1e-3)
    qtbot.waitUntil(lambda: not ctl.property("running"), timeout=60000)
    lines = ctl.property("consoleText").splitlines()
    assert lines[0].startswith("Block diagram run")
    assert lines[-1].startswith("Finished")


@pytest.mark.ngspice
def test_speed_loop_runs_from_the_one_run_button(ctl: DiagramController, qtbot) -> None:
    model = WaveformModel()
    ctl.attachWaveforms(model)
    ids = build_speed_loop(ctl)
    assert ctl.run(0.2, 1e-3) is True, ctl.property("runStatus")
    qtbot.waitUntil(lambda: not ctl.property("running"), timeout=60000)
    assert ctl.property("runStatus").startswith("Finished"), ctl.property("runDetails")
    result = ctl.result
    w = result.vectors[f"{ids['inertia']}.A.w"]
    assert result.units[f"{ids['inertia']}.A.w"] == "rad/s"
    assert w[-1] == pytest.approx(steady_speed(), rel=0.01)
    console = ctl.property("consoleText")
    assert console.splitlines()[0].startswith("Combined run: 1 physical network")
    assert len(console.splitlines()) > 3  # ngspice's own lines came back from the child
    assert console.splitlines()[-1].startswith("Finished")
