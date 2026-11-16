"""GridController (G6): open, run, stop, tables, profile and single-line geometry.

Reference values come from tests/fixtures/grid/<feeder>/opendss_reference.json (the
OpenDSS result of the original file), never typed here.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

import bridge.grid_controller as grid_controller  # noqa: E402
from bridge.grid_controller import LIMIT_HIGH, LIMIT_LOW, GridController  # noqa: E402
from bridge.waveform_controller import WaveformController  # noqa: E402
from bridge.waveform_model import WaveformModel  # noqa: E402
from core.grid.analysis import OVERLOAD, find_violations  # noqa: E402
from tests.core.grid.helpers import (  # noqa: E402
    BAD_REDIRECT,
    IEEE13,
    IEEE123,
    reference,
)

# IEEE 13 solves inside `compile`; the run solves it once more, which moves it by up
# to 1.3e-5 pu here (spike S2 saw 5e-6 on IEEE 34). Compare within OpenDSS's own
# convergence tolerance (Solution.Tolerance, 1e-4 pu by default).
PU_TOL = 1e-4
RUN_TIMEOUT_MS = 60_000


@pytest.fixture
def ctl(qapp: object) -> Iterator[GridController]:
    c = GridController()
    yield c
    c.shutdown()


def wait_idle(qtbot, c: GridController) -> None:
    qtbot.waitUntil(lambda: not c.running, timeout=RUN_TIMEOUT_MS)


def open_and_run(qtbot, c: GridController, path) -> None:
    assert c.openFile(str(path))
    wait_idle(qtbot, c)
    assert c.canRun, c.consoleText
    assert c.run()
    wait_idle(qtbot, c)
    assert c.hasResult, c.consoleText


@pytest.mark.opendss
def test_ieee13_open_then_run(qtbot, ctl: GridController) -> None:
    assert ctl.available
    assert ctl.openFile(str(IEEE13))
    assert ctl.running and not ctl.canRun
    wait_idle(qtbot, ctl)
    assert ctl.statusText.startswith("Opened ieee13nodeckt"), ctl.statusText
    assert ctl.canRun and not ctl.hasResult
    geo = ctl.geometry
    assert geo["hasCoordinates"] and geo["missing"] == 0
    assert len(geo["buses"]) == 16
    assert all(math.isnan(b["minPu"]) for b in geo["buses"])  # not solved yet

    assert ctl.run()
    wait_idle(qtbot, ctl)
    assert ctl.statusText.startswith("Finished: converged"), ctl.statusText
    ref = reference("ieee13")
    result = ctl.result()
    assert result is not None and result.node_names == ref["node_names"]
    assert np.max(np.abs(result.node_voltage_pu - np.array(ref["node_pu"]))) < PU_TOL
    assert ctl.nodes.rowCount() == len(ref["node_names"])
    summary = ctl.summary
    assert abs(float(summary["lossesKw"]) - ref["losses_kw"]) < 0.05
    assert abs(float(summary["totalKw"]) + ref["total_power_kw"]) < 0.5  # sign flipped
    assert "Losses:" in ctl.consoleText


@pytest.mark.opendss
def test_bus_675_phase_voltages(qtbot, ctl: GridController) -> None:
    assert ctl.busVoltages("675") == []  # no result yet
    open_and_run(qtbot, ctl, IEEE13)
    ref = reference("ieee13")
    ctl.selectBus("675")
    assert ctl.selectedBus == "675"
    rows = ctl.selectedVoltages
    assert [r["phase"] for r in rows] == ["a", "b", "c"]
    for r in rows:
        i = ref["node_names"].index(r["node"])
        v = complex(*ref["node_v"][i])
        assert abs(float(r["pu"]) - ref["node_pu"][i]) < PU_TOL
        assert abs(float(r["kv"]) - abs(v) / 1000) < 1e-3
        assert abs(float(r["angle"]) - math.degrees(math.atan2(v.imag, v.real))) < 0.01


@pytest.mark.opendss
def test_violations_are_from_analysis_and_highlighted(qtbot, ctl: GridController) -> None:
    open_and_run(qtbot, ctl, IEEE13)
    result = ctl.result()
    expected = find_violations(result, ctl.config, ctl.model())
    assert ctl.violation_list() == expected and expected
    assert ctl.violations.rowCount() == len(expected)
    assert ctl.summary["violationCount"] == len(expected)
    voltage = {v.location for v in expected if v.kind != OVERLOAD}
    overloads = {v.location for v in expected if v.kind == OVERLOAD}
    nodes = ctl.nodes.rows()
    assert {f"{r.keys[0]}.{'abc'.index(r.keys[1]) + 1}" for r in nodes if r.highlight} == voltage
    assert {r.keys[0] for r in ctl.branches.rows() if r.highlight} == overloads
    # overloaded branches are drawn thick (every IEEE 13 bus has coordinates)
    drawn = {b["id"] for b in ctl.geometry["branches"] if b["overloaded"]}
    assert drawn == overloads
    for v in expected:
        assert v.message() in ctl.consoleText


@pytest.mark.opendss
def test_tables_sort_by_any_column(qtbot, ctl: GridController) -> None:
    open_and_run(qtbot, ctl, IEEE13)
    nodes = ctl.nodes
    nodes.sortBy(3)  # V (pu)
    assert nodes.sortColumn == 3 and nodes.sortAscending
    pus = [r.keys[3] for r in nodes.rows() if not math.isnan(r.keys[3])]
    assert pus == sorted(pus)
    assert "▲" in nodes.headerData(3, grid_controller.Qt.Orientation.Horizontal, 0)
    nodes.sortBy(3)
    pus = [r.keys[3] for r in nodes.rows() if not math.isnan(r.keys[3])]
    assert pus == sorted(pus, reverse=True) and not nodes.sortAscending
    branches = ctl.branches
    branches.sortBy(0)
    names = [r.keys[0] for r in branches.rows()]
    assert names == sorted(names)
    assert nodes.cell(0, 0) == nodes.rows()[0].cells[0]


@pytest.mark.opendss
def test_voltage_profile_goes_to_the_waveform_window(qtbot, ctl: GridController) -> None:
    model = WaveformModel()
    waveforms = WaveformController()
    waveforms.attach(model)
    ctl.attachWaveforms(model)
    open_and_run(qtbot, ctl, IEEE13)
    sim = ctl.profile
    assert sim is not None and model.result() is sim
    assert sim.axis == "distance" and sim.units["distance"] == "km"
    assert sim.analysis == "profile"
    for name in ("v_pu.a", "v_pu.b", "v_pu.c"):
        assert sim.units[name] == "pu"
    assert np.all(sim.vectors[LIMIT_LOW] == 0.95) and np.all(sim.vectors[LIMIT_HIGH] == 1.05)
    x = sim.vectors["distance"]
    assert x[0] == 0 and np.all(np.diff(x) >= 0) and x[-1] > 1  # IEEE 13 is ~1.5 km long
    # the waveform window shows it with every trace in the first pane
    assert waveforms.analysis == "profile" and waveforms.hasData
    shown = {t["name"] for t in waveforms.panes[0]["traces"]}
    assert {"v_pu.a", "v_pu.b", "v_pu.c", LIMIT_LOW, LIMIT_HIGH} <= shown
    assert waveforms.xLabel == "distance [km]"
    assert model.formatX(0.5) == "0.5 km"


@pytest.mark.opendss
def test_file_with_an_error_is_reported_and_does_not_run(qtbot, ctl: GridController) -> None:
    assert ctl.openFile(str(BAD_REDIRECT))
    wait_idle(qtbot, ctl)
    assert ctl.statusText.startswith("Not opened"), ctl.statusText
    assert "error: circuit:" in ctl.consoleText
    assert "IEEELineCodez.dss" in ctl.consoleText
    assert not ctl.canRun
    assert not ctl.run()
    assert not ctl.running
    assert ctl.statusText.startswith("Not started")
    assert not ctl.hasResult and ctl.nodes.rowCount() == 0


@pytest.mark.opendss
def test_ieee123_runs_without_blocking_and_stop_works(qtbot, ctl: GridController) -> None:
    assert ctl.openFile(str(IEEE123))
    wait_idle(qtbot, ctl)
    assert ctl.canRun, ctl.consoleText
    # IEEE123Master.dss loads no bus coordinates: the view shows a message instead
    geo = ctl.geometry
    assert not geo["hasCoordinates"] and geo["missing"] > 0

    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    assert ctl.run()
    wait_idle(qtbot, ctl)
    timer.stop()
    assert ctl.hasResult, ctl.consoleText
    assert len(ticks) > 5  # the event loop kept running during the child's run
    assert ctl.nodes.rowCount() > 250

    assert ctl.run()
    qtbot.wait(100)
    assert ctl.running
    ctl.stop()
    wait_idle(qtbot, ctl)
    assert ctl.statusText == "Stopped"
    assert ctl.hasResult  # the previous result stays
    # a new run works afterwards
    assert ctl.run()
    wait_idle(qtbot, ctl)
    assert ctl.statusText.startswith("Finished")


@pytest.mark.opendss
def test_open_switch_is_drawn_open(qtbot, ctl: GridController, tmp_path) -> None:
    """IEEE 13 with switch 671692 opened: drawn open, the buses behind it at 0 V."""
    dss = tmp_path / "ieee13_open_switch.dss"
    dss.write_text(f'Redirect "{IEEE13}"\nOpen Line.671692 Term=1\n', encoding="utf-8")
    open_and_run(qtbot, ctl, dss)
    model = ctl.model()
    assert not model.switches["671692"].closed
    switches = [b for b in ctl.geometry["branches"] if b["kind"] == "switch"]
    assert [(b["id"], b["open"]) for b in switches] == [("line.671692", True)]
    minpu = {b["name"]: b["minPu"] for b in ctl.geometry["buses"]}
    assert minpu["692"] < 0.01 and minpu["675"] < 0.01  # cut off: drawn red
    assert minpu["671"] > 0.9
    assert ctl.busVoltages("675") and all(float(r["pu"]) < 0.01 for r in ctl.busVoltages("675"))


def test_missing_file_is_refused(ctl: GridController, tmp_path) -> None:
    if not ctl.available:
        pytest.skip("dss-python not installed")
    assert not ctl.openFile(str(tmp_path / "nope.dss"))
    assert ctl.statusText.startswith("Not opened: file not found")
    assert not ctl.running and not ctl.canRun


def test_without_dss_python(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grid_controller, "engine_available", lambda: False)
    c = GridController()
    assert not c.available
    assert not c.openFile(str(IEEE13))
    assert "dss-python" in c.statusText
    assert not c.run() and not c.canRun and not c.running
    assert c.geometry == {"hasCoordinates": False, "buses": [], "branches": [], "missing": 0}


@pytest.mark.opendss
def test_summary_has_losses_percent_and_voltage_range(qtbot, ctl: GridController) -> None:
    open_and_run(qtbot, ctl, IEEE13)
    r = ctl.result()
    s = ctl.summary
    assert float(s["lossesPct"]) == pytest.approx(
        100 * r.total_losses_kw / r.total_power_kw, abs=0.005
    )
    # lowest / highest voltage over the phase nodes (a, b, c) of the result
    phase = [
        (name, float(pu))
        for name, pu in zip(r.node_names, r.node_voltage_pu, strict=True)
        if name.rpartition(".")[2] in ("1", "2", "3") and not math.isnan(float(pu))
    ]
    low = min(phase, key=lambda x: x[1])
    high = max(phase, key=lambda x: x[1])
    assert float(s["minPu"]) == pytest.approx(low[1], abs=5e-5)
    assert float(s["maxPu"]) == pytest.approx(high[1], abs=5e-5)
    assert s["minPuBus"] == low[0].rpartition(".")[0]
    assert s["maxPuBus"] == high[0].rpartition(".")[0]


@pytest.mark.opendss
def test_bus_table_folds_the_node_table(qtbot, ctl: GridController) -> None:
    open_and_run(qtbot, ctl, IEEE13)
    by_bus: dict[str, dict[str, float]] = {}
    highlighted: set[str] = set()
    for row in ctl.nodes.rows():
        bus, phase, pu = row.keys[0], row.keys[1], row.keys[3]
        if phase in ("a", "b", "c") and not math.isnan(pu):
            by_bus.setdefault(bus, {})[phase] = pu
        if row.highlight:
            highlighted.add(bus)
    rows = {r.keys[0]: r for r in ctl.buses.rows()}
    assert set(rows) == set(by_bus)
    for bus, phases in by_bus.items():
        keys = rows[bus].keys
        for i, p in enumerate("abc"):
            if p in phases:
                assert keys[1 + i] == phases[p]
            else:
                assert math.isnan(keys[1 + i]) and rows[bus].cells[1 + i] == "—"
        assert keys[4] == min(phases.values()) and keys[5] == max(phases.values())
        assert rows[bus].highlight == (bus in highlighted)


@pytest.mark.opendss
def test_tables_filter_and_export_csv(qtbot, ctl: GridController, tmp_path) -> None:
    import csv

    open_and_run(qtbot, ctl, IEEE13)
    nodes = ctl.nodes
    total = nodes.rowCount()
    assert nodes.totalCount == nodes.count == total

    nodes.setFilter("67")  # buses 670, 671, 675 (text columns only)
    assert {r.keys[0] for r in nodes.rows()} == {"670", "671", "675"}
    assert nodes.count == nodes.rowCount() < total and nodes.totalCount == total
    assert nodes.filterText == "67"

    nodes.setFilter("")
    nodes.setOnlyHighlighted(True)
    assert nodes.rows() and all(r.highlight for r in nodes.rows())
    assert nodes.count == sum(1 for r in nodes.rows() if r.highlight)

    # sorting keeps the filter; the CSV has the shown rows in the shown order
    nodes.sortBy(3)
    path = tmp_path / "nodes.csv"
    assert nodes.saveCsv(str(path))
    with path.open(encoding="utf-8", newline="") as f:
        lines = list(csv.reader(f))
    assert lines[0] == [title for title, _ in grid_controller.NODE_COLUMNS]
    assert lines[1:] == [r.cells for r in nodes.rows()]

    # a new result keeps the filter settings
    assert ctl.run()
    wait_idle(qtbot, ctl)
    assert nodes.onlyHighlighted and all(r.highlight for r in nodes.rows())
    assert not nodes.saveCsv(str(tmp_path / "missing" / "x.csv"))
