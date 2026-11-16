"""D8: the designer's results overlay, feasibility report and stale flag
(GridDiagramController), and the overlay of the G6 view (GridController).

The fake run hands back a GridResult typed here (test inputs, as in the D5 tests);
the IEEE 13 checks solve the fixture file in OpenDSS and compare the overlay with
core/grid/analysis.py.
"""

from __future__ import annotations

import copy
import math
import os
from collections.abc import Iterator
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402

import bridge.grid_controller as grid_controller  # noqa: E402
from bridge.grid_controller import GridController  # noqa: E402
from bridge.grid_diagram_controller import GridDiagramController  # noqa: E402
from core.grid.analysis import (  # noqa: E402
    CHECK_LINE_LOADING,
    CHECK_VOLTAGE_MIN,
    FAIL,
    NONE,
    PASS,
    SNAPSHOT_LIMITATION,
    feasibility_report,
    results_overlay,
)
from core.grid.config import CONTROL_STATIC, PowerFlowConfig  # noqa: E402
from core.grid.result import GridResult  # noqa: E402
from core.grid.runner import OK, GridRunOutcome  # noqa: E402
from tests.bridge.test_grid_diagram_controller import (  # noqa: E402
    RUN_TIMEOUT_MS,
    grid,  # noqa: F401 - fixture
    small_design,
)
from tests.core.grid.helpers import IEEE13  # noqa: E402
from tests.core.grid.test_feasibility import _branch  # noqa: E402
from tests.core.grid.test_overlay import expected_bus_status  # noqa: E402

# The result the fake run returns for small_design (typed for the test): b1 is below
# 0.95 pu on phase c; l1 (rated 100 A here) carries 50 A.
NODES = ["src.1", "src.2", "src.3", "b1.1", "b1.2", "b1.3"]
PU = [1.0, 1.0, 1.0, 0.97, 0.96, 0.94]


def fake_result() -> GridResult:
    return GridResult(
        converged=True,
        iterations=3,
        node_names=list(NODES),
        node_voltage=np.asarray(PU, dtype=complex) * 7200.0,
        node_voltage_pu=np.asarray(PU, dtype=float),
        branches={"line.l1": _branch("line.l1", [50.0, 48.0, 47.0])},
        total_power_kw=905.0,
        total_power_kvar=310.0,
        total_losses_kw=5.0,
        total_losses_kvar=10.0,
    )


class FakeGrid(QObject):
    """Stands in for GridController.runModel / show_profile: answers every run with
    ``result`` on the next turn of the event loop."""

    modelRunFinished = Signal(object)

    def __init__(self, result: GridResult) -> None:
        super().__init__()
        self.result = result
        self.runs: list[Any] = []
        self.profiles: list[Any] = []

    def runModel(self, model: Any, config: Any, script: Any) -> str:  # noqa: N802
        self.runs.append((model, config))
        QTimer.singleShot(0, self._finish)
        return ""

    def _finish(self) -> None:
        self.modelRunFinished.emit(GridRunOutcome(OK, result=copy.deepcopy(self.result)))

    def show_profile(self, model: Any, result: Any, config: Any) -> list[Any]:
        self.profiles.append((model, result, config))
        return []

    def stop(self) -> None:
        pass


@pytest.fixture
def fake(qapp: object, monkeypatch: pytest.MonkeyPatch) -> Iterator[GridDiagramController]:
    monkeypatch.setattr(grid_controller, "engine_available", lambda: True)
    runner = FakeGrid(fake_result())
    c = GridDiagramController(grid_controller=runner)
    c._fake = runner  # type: ignore[attr-defined] - keeps it alive for the test
    yield c
    c.shutdown()


def run(qtbot, ctl: GridDiagramController) -> None:
    with qtbot.waitSignal(ctl.resultReady, timeout=RUN_TIMEOUT_MS):
        assert ctl.runPowerFlow(), ctl.statusText


def rated_design(ctl: GridDiagramController) -> None:
    small_design(ctl)
    assert ctl.setProperty("line.l1", "norm_amps", 100)


# ---- without OpenDSS (a fake run)


def test_no_overlay_or_report_before_a_run(fake: GridDiagramController) -> None:
    rated_design(fake)
    assert fake.overlay == {} and fake.overlayPaths == [] and fake.overlayBuses == []
    assert fake.feasibility == {} and not fake.hasReport and not fake.resultsStale
    assert fake.snapshotNote == SNAPSHOT_LIMITATION  # shown even before a run


def test_overlay_values_come_from_the_analysis(qtbot, fake: GridDiagramController) -> None:
    rated_design(fake)
    run(qtbot, fake)
    design = fake.design()
    assert design is not None
    expected = results_overlay(design.grid, fake.result(), fake.config)
    overlay = fake.overlay
    assert set(overlay) == set(expected.items)
    for key, item in expected.items.items():
        row = overlay[key]
        assert (row["status"], row["text"], row["weight"]) == (item.status, item.text, item.weight)
    assert overlay["bus.src"]["status"] == PASS
    b1 = overlay["bus.b1"]
    assert b1["status"] == FAIL and b1["value"] == 0.94 and b1["unit"] == "pu"
    l1 = overlay["line.l1"]
    assert l1["status"] == PASS and l1["value"] == pytest.approx(50.0)
    assert l1["ratio"] == pytest.approx(0.5) and l1["weight"] == 1
    assert overlay["load.ld1"]["status"] == NONE and math.isnan(overlay["load.ld1"]["value"])
    # every bus status equals the analysis status
    buses = {k: v["status"] for k, v in overlay.items() if k.startswith("bus.")}
    assert buses == expected_bus_status(design.grid, fake.result(), fake.config)
    # paths: every drawn edge of an element in the overlay, grouped by status and weight
    paths = {p["key"]: p for p in fake.overlayPaths}
    assert "pass_1" in paths and paths["pass_1"]["path"].count("M") == 1  # l1
    assert "none_0" in paths  # source and load stubs
    assert {b["id"]: b["status"] for b in fake.overlayBuses} == buses
    limits = fake.overlayLimits
    assert (limits["voltageMinPu"], limits["lineLimitPct"]) == (0.95, 100.0)
    # the voltage profile goes to the waveform window through GridController (rule 7)
    profile_model, profile_result, _ = fake._fake.profiles[-1]  # type: ignore[attr-defined]
    assert profile_result is fake.result() and profile_model.name == design.grid.name


def test_feasibility_report_and_console(qtbot, fake: GridDiagramController) -> None:
    rated_design(fake)
    run(qtbot, fake)
    report = fake.report()
    assert report is not None
    design = fake.design()
    assert design is not None
    same = feasibility_report(design.grid, fake.result(), fake.config)
    assert [c.status for c in report.level2] == [c.status for c in same.level2]
    f = fake.feasibility
    assert f["status"] == report.status == FAIL and f["solved"]
    assert f["limitation"] == SNAPSHOT_LIMITATION
    assert f["level1Status"] == report.level1_status
    assert f["level2Status"] == FAIL
    assert len(f["level1"]) == len(report.level1)
    rows = {r["name"]: r for r in f["level2"]}
    assert rows[CHECK_VOLTAGE_MIN]["status"] == FAIL
    assert rows[CHECK_VOLTAGE_MIN]["element"] == "bus.b1"
    assert rows[CHECK_VOLTAGE_MIN]["value"] == "0.9400 pu"
    assert rows[CHECK_LINE_LOADING]["status"] == PASS
    assert rows[CHECK_LINE_LOADING]["element"] == "line.l1"
    assert [s["label"] for s in f["summary"]][:3] == ["Load", "Source", "Losses"]
    assert "Feasibility (one snapshot): FAIL" in fake.consoleText
    assert SNAPSHOT_LIMITATION in fake.consoleText


def test_any_change_after_a_run_makes_the_results_stale(qtbot, fake: GridDiagramController) -> None:
    rated_design(fake)
    run(qtbot, fake)
    assert not fake.resultsStale and fake.hasResult
    # a move (presentation only) is a change
    with qtbot.waitSignal(fake.staleChanged):
        assert fake.moveGlyph("bus.b1", 3.0, 8.0)
    assert fake.resultsStale
    assert fake.overlay and fake.hasReport  # still shown, hatched in the UI
    run(qtbot, fake)
    assert not fake.resultsStale
    # an edit, then undo back to the state that was run: still stale (kept simple)
    assert fake.setProperty("load.ld1", "kw", 950)
    assert fake.resultsStale
    fake.undo()
    assert fake.resultsStale
    run(qtbot, fake)
    fake.undo()  # the property edit was undone; this undoes the move
    assert fake.resultsStale
    run(qtbot, fake)
    fake.redo()
    assert fake.resultsStale
    # new settings are a change too
    run(qtbot, fake)
    fake.set_config(PowerFlowConfig(voltage_min_pu=0.9))
    assert fake.resultsStale
    fake.set_config(PowerFlowConfig(voltage_min_pu=0.9))  # the same again
    run(qtbot, fake)
    assert not fake.resultsStale
    assert fake.overlay["bus.b1"]["status"] == PASS  # 0.94 is inside 0.9..1.05
    # selection is view state: no change
    fake.select("bus.b1")
    assert not fake.resultsStale
    # another design: no results, nothing stale
    small_design(fake)
    assert not fake.resultsStale and not fake.hasResult and fake.overlay == {}


def test_focus_element_selects_and_gives_the_position(fake: GridDiagramController) -> None:
    rated_design(fake)
    design = fake.design()
    assert design is not None
    g = design.diagram.bus_glyphs["b1"]
    assert fake.focusElement("bus.b1") == [g.x, g.y]
    assert fake.selection == ["bus.b1"]
    load = design.diagram.element_glyphs["load.ld1"]
    assert fake.focusElement("load.ld1") == [load.x, load.y]
    assert fake.selection == ["load.ld1"]
    assert fake.focusElement("load.nothere") == [] and fake.focusElement("") == []


def test_a_failed_run_clears_the_results(qtbot, fake: GridDiagramController) -> None:
    rated_design(fake)
    run(qtbot, fake)
    fake._fake.result = GridResult(converged=False, iterations=15)  # type: ignore[attr-defined]
    assert fake.runPowerFlow()
    qtbot.waitUntil(lambda: not fake.busy, timeout=RUN_TIMEOUT_MS)
    assert not fake.hasResult and fake.overlay == {} and not fake.resultsStale
    f = fake.feasibility  # Level 1 still, and "not solved"
    assert f["status"] == FAIL and not f["solved"] and f["level2"] == []


# ---- with OpenDSS: IEEE 13 as a design


@pytest.fixture
def designer(grid: GridController) -> Iterator[GridDiagramController]:  # noqa: F811
    c = GridDiagramController(grid_controller=grid)
    yield c
    c.shutdown()


@pytest.mark.opendss
def test_ieee13_design_overlay_bus_status_equals_the_analysis(
    qtbot,
    designer: GridDiagramController,
    grid: GridController,  # noqa: F811
) -> None:
    config = PowerFlowConfig(control_mode=CONTROL_STATIC, max_control_iterations=30)
    designer.set_config(config)
    with qtbot.waitSignal(designer.designOpened, timeout=RUN_TIMEOUT_MS):
        assert designer.openDssAsDesign(str(IEEE13))
    run(qtbot, designer)
    design = designer.design()
    result = designer.result()
    assert design is not None and result is not None and result.converged
    report = designer.report()
    assert report is not None and report.solved
    expected = expected_bus_status(design.grid, result, config)
    overlay = designer.overlay
    buses = {k: v["status"] for k, v in overlay.items() if k.startswith("bus.")}
    assert buses == expected
    assert {f"bus.{b}" for b in design.grid.buses} <= set(buses)
    # and the whole overlay is analysis.results_overlay of the design that was run
    core = results_overlay(design.grid, result, config)
    assert {k: v["status"] for k, v in overlay.items()} == {
        k: v.status for k, v in core.items.items()
    }
    # IEEE 13 is outside 0.95..1.05 somewhere exactly when the report says so
    failed = report.check(CHECK_VOLTAGE_MIN).status == FAIL
    assert failed == any(
        v["status"] == FAIL and v["value"] < config.voltage_min_pu
        for k, v in overlay.items()
        if k.startswith("bus.")
    )
    assert designer.feasibility["level2"] and designer.overlayPaths
    # the voltage profile went to the waveform window through GridController (rule 7)
    assert grid.profile is not None and len(grid.profile.vectors[grid.profile.axis]) > 0


@pytest.mark.opendss
def test_g6_view_overlay_equals_the_analysis(qtbot, grid: GridController) -> None:  # noqa: F811
    assert grid.openFile(str(IEEE13))
    qtbot.waitUntil(lambda: grid.canRun, timeout=RUN_TIMEOUT_MS)
    with qtbot.waitSignal(grid.resultReady, timeout=RUN_TIMEOUT_MS):
        assert grid.run()
    model, result = grid.model(), grid.result()
    assert model is not None and result is not None
    expected = expected_bus_status(model, result, grid.config)
    overlay = grid.overlay
    assert {k: v["status"] for k, v in overlay.items() if k.startswith("bus.")} == expected
    drawn = {b["id"] for b in grid.overlayBuses}
    assert drawn == {f"bus.{b['name']}" for b in grid.geometry["buses"]}
    assert grid.overlayPaths and all(p["path"].startswith("M") for p in grid.overlayPaths)
    # new limits recompute the overlay
    grid.set_config(PowerFlowConfig(voltage_min_pu=1.04, voltage_max_pu=1.2))
    assert {k: v["status"] for k, v in grid.overlay.items() if k.startswith("bus.")} == (
        expected_bus_status(model, result, grid.config)
    )
    assert grid.overlayLimits["voltageMinPu"] == 1.04
