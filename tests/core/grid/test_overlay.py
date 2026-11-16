"""D8 results overlay: status and values per bus and element (core/grid/analysis.py).

The overlay must agree with the D7 analysis it is drawn from: bus statuses with
voltage_violations over the energized buses, branch statuses with the loadings of the
feasibility report. Feeder values come from the OpenDSS reference fixtures.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.grid.analysis import (
    CHECK_LINE_LOADING,
    CHECK_TRANSFORMER_LOADING,
    CHECK_VOLTAGE_MAX,
    CHECK_VOLTAGE_MIN,
    FAIL,
    NONE,
    OFF,
    OVERLAY_STATUSES,
    PASS,
    WARN,
    WEIGHT_STEPS,
    _split_node,
    feasibility_report,
    loading_status,
    results_overlay,
    voltage_violations,
)
from core.grid.config import PowerFlowConfig
from core.grid.model import LOAD_PQ, WYE, Bus, GridModel, Load, Switch, Terminal
from core.grid.result import GridResult
from core.grid.topology import GridGraph
from tests.core.grid.helpers import reference, solve_feeder
from tests.core.grid.test_feasibility import FEEDERS, _branch, _design, _reference_result


def expected_bus_status(model: GridModel, result: GridResult, config: PowerFlowConfig) -> dict:
    """Bus statuses derived independently: OFF when not energized, FAIL when a phase
    node of the bus is a voltage violation, else PASS (NONE without phase nodes)."""
    energized = GridGraph.build(model).energized_buses()
    bad = {_split_node(v.location)[0] for v in voltage_violations(result, config)}
    with_phases = {
        _split_node(n)[0]
        for n, pu in zip(result.node_names, result.node_voltage_pu, strict=True)
        if _split_node(n)[1] in (1, 2, 3) and not np.isnan(pu)
    }
    out = {}
    for bus in GridGraph.build(model).buses:
        if bus not in energized:
            out[f"bus.{bus}"] = OFF
        elif bus not in with_phases:
            out[f"bus.{bus}"] = NONE
        else:
            out[f"bus.{bus}"] = FAIL if bus in bad else PASS
    return out


# ---- a hand-built design, without OpenDSS


def _cut_off_design() -> tuple[GridModel, GridResult]:
    """_design (l2 rated 10 A, l3 unrated) plus bus e behind an open switch, with a
    result typed for the test."""
    t = Terminal.parse
    model = _design(rating_l2=10.0)
    model.buses["e"] = Bus("e", 0.0)
    model.switches["sw1"] = Switch("sw1", t("a"), t("e"), 3, closed=False, r_ohm=1e-4)
    model.loads["ld3"] = Load("ld3", t("e.1.2.3"), 3, WYE, 11.0, 10.0, 5.0, LOAD_PQ)
    result = GridResult(
        converged=True,
        node_names=["src.1", "src.2", "src.3", "a.1", "b.1", "b.2", "c.1", "e.1"],
        node_voltage_pu=np.array([1.0, 1.0, 1.0, 0.99, 0.96, 0.94, 0.985, 0.0]),
        branches={
            "line.l1": _branch("line.l1", [100.0, 90.0, 80.0]),
            "line.l2": _branch("line.l2", [9.5, 9.0, 9.0]),  # 95 % of 10 A
            "line.l3": _branch("line.l3", [3.0, 3.0, 3.0]),
            "line.sw1": _branch("line.sw1", [0.0, 0.0, 0.0]),
        },
    )
    return model, result


def test_overlay_statuses_values_and_weights() -> None:
    model, result = _cut_off_design()
    config = PowerFlowConfig()
    overlay = results_overlay(model, result, config)
    assert overlay.solved
    items = overlay.items
    assert list(items)[:5] == ["bus.src", "bus.a", "bus.b", "bus.c", "bus.e"]
    assert {i.status for i in items.values()} <= set(OVERLAY_STATUSES)
    assert {k: v.status for k, v in items.items() if k.startswith("bus.")} == (
        expected_bus_status(model, result, config)
    )
    b = items["bus.b"]
    assert (b.status, b.value, b.limit, b.unit) == (FAIL, 0.94, 0.95, "pu")
    assert "a 0.9600" in b.text and "b 0.9400" in b.text and "below 0.95" in b.text
    assert items["bus.a"].value == 0.99 and items["bus.a"].limit == 0.95
    assert items["bus.e"].status == OFF

    l1, l2, l3 = items["line.l1"], items["line.l2"], items["line.l3"]
    assert (l1.status, l1.weight) == (PASS, 0) and l1.value == pytest.approx(1e-2)
    assert (l2.status, l2.weight, l2.unit) == (WARN, 2, "%")
    assert l2.value == pytest.approx(95.0) and l2.ratio == pytest.approx(0.95)
    assert (l3.status, l3.value, l3.ratio) == (NONE, None, None)
    assert "no rating" in l3.text and "3.0 A" in l3.text
    assert items["line.sw1"].status == OFF  # open
    assert items["load.ld3"].status == OFF and items["load.ld1"].status == NONE
    assert items["vsource.source"].status == NONE
    assert all(0 <= i.weight < WEIGHT_STEPS for i in items.values())


def test_overlay_follows_the_config_limits() -> None:
    model, result = _cut_off_design()
    overlay = results_overlay(
        model, result, PowerFlowConfig(voltage_min_pu=0.995, line_loading_limit_pct=90.0)
    )
    assert overlay.items["bus.a"].status == FAIL
    l2 = overlay.items["line.l2"]
    assert (l2.status, l2.weight, l2.limit) == (FAIL, 3, 90.0)


def test_overlay_weight_steps() -> None:
    model, result = _cut_off_design()
    for amps, weight in ((4.0, 0), (6.0, 1), (9.0, 2), (10.0, 2), (10.5, 3)):
        result.branches["line.l2"] = _branch("line.l2", [amps, 0.0, 0.0])
        assert results_overlay(model, result, PowerFlowConfig()).items["line.l2"].weight == weight


def test_overlay_of_no_or_an_unconverged_result_is_empty() -> None:
    model, _ = _cut_off_design()
    assert not results_overlay(model, None, PowerFlowConfig()).solved
    empty = results_overlay(model, GridResult(converged=False), PowerFlowConfig())
    assert not empty.solved and empty.items == {}


# ---- IEEE feeders


@pytest.fixture(scope="module", params=FEEDERS)
def feeder(request: pytest.FixtureRequest) -> tuple[GridModel, GridResult]:
    pytest.importorskip("dss")
    model, _ = solve_feeder(request.param)
    return model, _reference_result(reference(request.param))


@pytest.mark.opendss
@pytest.mark.parametrize("band", [(0.95, 1.05), (1.0, 1.04)])
def test_feeder_overlay_equals_the_analysis(feeder, band) -> None:
    model, result = feeder
    config = PowerFlowConfig(voltage_min_pu=band[0], voltage_max_pu=band[1])
    overlay = results_overlay(model, result, config)
    report = feasibility_report(model, result, config)
    buses = {k: v.status for k, v in overlay.items.items() if k.startswith("bus.")}
    assert buses == expected_bus_status(model, result, config)
    # the Level 2 voltage checks fail exactly when a bus of the overlay fails
    failed = report.check(CHECK_VOLTAGE_MIN).status == FAIL or (
        report.check(CHECK_VOLTAGE_MAX).status == FAIL
    )
    assert failed == (FAIL in buses.values())
    worst = report.check(CHECK_VOLTAGE_MIN).worst_element
    assert overlay.items[worst].status == (
        FAIL if report.check(CHECK_VOLTAGE_MIN).status == FAIL else PASS
    )
    # every loading of the report, with its status, is in the overlay
    for row in report.line_loadings + report.transformer_loadings:
        item = overlay.items[row.element]
        limit = (
            config.line_loading_limit_pct
            if row.winding == 0
            else (config.transformer_loading_limit_pct)
        )
        assert item.value == row.pct
        assert item.status == loading_status(row.pct, limit, config.warn_fraction)
    for name in (CHECK_LINE_LOADING, CHECK_TRANSFORMER_LOADING):
        check = report.check(name)
        if check.worst_element:
            assert overlay.items[check.worst_element].status == check.status
    # every bus and drawn element has an item
    drawn = [
        el.id
        for attr in (
            "sources",
            "lines",
            "switches",
            "transformers",
            "loads",
            "capacitors",
            "pvsystems",
        )  # fmt: skip
        for el in getattr(model, attr).values()
    ]
    assert set(drawn) <= set(overlay.items)
    assert {f"bus.{b}" for b in model.buses} <= set(overlay.items)
