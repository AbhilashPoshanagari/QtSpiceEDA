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


def test_signal_diagram_keeps_small_steps(ctl: DiagramController, qtbot) -> None:
    ctl.addBlock("Constant", 0, 0)
    ctl.addBlock("Scope", 200, 0)
    ctl.connectPorts("Constant1.y", "Scope1.u")
    ctl.timeout = 30
    assert ctl.run(1e-4, 1e-6) is True  # no physical blocks: no 10 us limit
    qtbot.waitUntil(lambda: not ctl.property("running"), timeout=60000)
    assert ctl.property("runStatus").startswith("Finished")


def test_shutdown_during_a_run_waits_for_its_thread(ctl: DiagramController) -> None:
    ctl.addBlock("Constant", 0, 0)
    ctl.addBlock("Scope", 200, 0)
    ctl.connectPorts("Constant1.y", "Scope1.u")
    assert ctl.run(10.0, 1e-3) is True
    ctl.shutdown()  # cancels the child and joins the run thread
    assert ctl._thread is not None and not ctl._thread.is_alive()  # noqa: SLF001


def test_missing_ngspice_is_refused(ctl: DiagramController) -> None:
    build_speed_loop(ctl)
    ctl.ngspice_available = lambda: False
    assert ctl.run(0.1, 1e-3) is False
    assert "ngspice is not available" in ctl.property("runStatus")
    assert marked(ctl) == set()


def test_solver_ts_sets_the_canvas_step(ctl: DiagramController) -> None:
    ids = build_speed_loop(ctl)
    assert ctl.property("dt") == pytest.approx(1e-3)
    ctl.setSetting(ids["solver"], "Ts", "2m")
    assert ctl.property("dt") == pytest.approx(2e-3)
    ctl.undo()
    assert ctl.property("dt") == pytest.approx(1e-3)
    ctl.redo()
    assert ctl.property("dt") == pytest.approx(2e-3)


def test_run_gives_the_solver_the_time_step(ctl: DiagramController) -> None:
    ids = build_speed_loop(ctl)
    ctl.setSetting(ids["solver"], "Ts", "2m")
    ctl.ngspice_available = lambda: False  # stop after the checks
    steps_before = ctl.undo_stack.count()
    assert ctl.run(0.05, 5e-4) is False
    assert "ngspice" in ctl.property("runStatus")  # passed the Ts and network checks
    assert ctl.diagram.effective_settings(ids["solver"])["Ts"] == pytest.approx(5e-4)
    assert ctl.property("dt") == pytest.approx(5e-4)
    assert f"{ids['solver']}: Ts 0.002 s set to the time step 0.0005 s" in ctl.property(
        "consoleText"
    )
    # One undo step, which brings the solver's Ts (and the step) back.
    assert ctl.undo_stack.count() == steps_before + 1
    ctl.undo()
    assert ctl.diagram.effective_settings(ids["solver"])["Ts"] == pytest.approx(2e-3)
    assert ctl.property("dt") == pytest.approx(2e-3)


def test_run_with_matching_ts_adds_no_undo_step(ctl: DiagramController) -> None:
    build_speed_loop(ctl)
    ctl.ngspice_available = lambda: False
    steps = ctl.undo_stack.count()
    ctl.run(0.05, 1e-3)  # the default Ts is 1 ms already
    assert ctl.undo_stack.count() == steps
    assert "set to the time step" not in ctl.property("consoleText")


@pytest.mark.ngspice
def test_step_and_solver_ts_differ_and_the_run_still_works(ctl: DiagramController, qtbot) -> None:
    ids = build_speed_loop(ctl)
    ctl.setSetting(ids["solver"], "Ts", "2m")
    assert ctl.run(0.05, 1e-3) is True, ctl.property("runStatus")
    qtbot.waitUntil(lambda: not ctl.property("running"), timeout=60000)
    assert ctl.property("runStatus").startswith("Finished"), ctl.property("runDetails")


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
