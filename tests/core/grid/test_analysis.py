from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from core.grid.analysis import (
    OVERLOAD,
    OVERVOLTAGE,
    UNDERVOLTAGE,
    UNREACHABLE_IN_PROFILE,
    bus_distances,
    find_violations,
    loss_summary,
    voltage_profile,
)
from core.grid.config import PowerFlowConfig
from core.grid.model import (
    WYE,
    Bus,
    GridModel,
    Line,
    Source,
    Switch,
    Terminal,
    Transformer,
    Winding,
)
from core.grid.result import BranchResult, GridResult
from tests.core.grid.helpers import reference, solve_feeder

T = Terminal.parse


def _branch(element: str, losses_kw: float = 0.0, loading: float | None = None) -> BranchResult:
    return BranchResult(
        element=element,
        bus1="x",
        bus2="y",
        phases=1,
        current=np.array([1 + 0j]),
        p_kw=0.0,
        q_kvar=0.0,
        losses_kw=losses_kw,
        losses_kvar=2 * losses_kw,
        normal_amps=0.0 if loading is None else 100.0,
        loading_pct=loading,
    )


def _model() -> GridModel:
    """src -t- lv -100 m- a -200 m- b -50 m- c, lv -300 m- d, b -closed sw- e,
    b -open sw- f, and g on its own."""
    m = GridModel(name="t", base_frequency=50.0)
    for name in ("src", "lv", "a", "b", "c", "d", "e", "f", "g"):
        m.buses[name] = Bus(name, 1.0)
    m.sources["s"] = Source("s", T("src"), T("src.0.0.0"), 3, 11.0, 1.0, 0.0, 0, 0, 0, 0)
    w1 = Winding(T("src"), WYE, 11.0, 100.0, 0.5)
    w2 = Winding(T("lv"), WYE, 0.4, 100.0, 0.5)
    m.transformers["t"] = Transformer("t", 3, [w1, w2], 4.0, 0, 0)
    for name, b1, b2, length in (
        ("l1", "lv", "a", 100.0),
        ("l2", "a", "b", 200.0),
        ("l3", "b", "c", 50.0),
        ("l4", "lv", "d", 300.0),
    ):
        m.lines[name] = Line(name, T(b1), T(b2), 3, length, "lc")
    m.switches["s1"] = Switch("s1", T("b"), T("e"), 3, True, 1e-4)
    m.switches["s2"] = Switch("s2", T("b"), T("f"), 3, False, 1e-4)
    return m


def _result(nodes: dict[str, float], branches: list[BranchResult] = ()) -> GridResult:
    names = list(nodes)
    return GridResult(
        converged=True,
        node_names=names,
        node_voltage=np.array([complex(v) for v in nodes.values()]),
        node_voltage_pu=np.array(list(nodes.values()), dtype=float),
        branches={b.element: b for b in branches},
    )


# ---- violations


def test_voltage_limits_are_strict_and_per_node() -> None:
    cfg = PowerFlowConfig(voltage_min_pu=0.95, voltage_max_pu=1.05)
    res = _result({"a.1": 0.95, "a.2": 0.9499, "a.3": 1.05, "b.1": 1.0501, "b.4": 0.0, "f.1": 0.0})
    got = {(v.kind, v.location) for v in find_violations(res, cfg)}
    # b.4 is a neutral: not checked; f.1 at 0 V (cut off) is an undervoltage
    assert got == {(UNDERVOLTAGE, "a.2"), (OVERVOLTAGE, "b.1"), (UNDERVOLTAGE, "f.1")}


def test_nodes_without_a_voltage_base_are_skipped() -> None:
    res = _result({"a.1": math.nan, "a.2": 0.5})
    assert [v.location for v in find_violations(res, PowerFlowConfig())] == ["a.2"]


def test_overloads_use_the_d7_loadings_and_their_limits() -> None:
    model = _model()
    model.lines["l1"].norm_amps = 10.0  # 12 A -> 120 %
    model.lines["l2"].norm_amps = 100.0  # 12 A -> 12 %
    # l3 and l4 have no rating (their linecode has none): skipped
    hot = BranchResult("line.l1", "lv", "a", 1, np.array([12 + 0j]), 0, 0, 0, 0, 400.0, 3.0)
    cool = replace(hot, element="line.l2")
    xf = replace(
        _branch("transformer.t"), terminal_power=(101 + 0j, -(100 + 0j))
    )  # 101 % of 100 kVA on winding 1
    res = _result({"a.1": 1.0}, [hot, cool, _branch("line.l3", loading=500.0), xf])
    out = find_violations(res, PowerFlowConfig(), model)
    assert [(v.kind, v.location, v.limit) for v in out] == [
        (OVERLOAD, "line.l1", 100.0),
        (OVERLOAD, "transformer.t", 100.0),
    ]
    assert out[0].value == pytest.approx(120.0) and out[1].value == pytest.approx(101.0)
    diag = out[0].to_diagnostic()
    assert diag.block == "line.l1" and diag.code == OVERLOAD and "120.0" in diag.message
    # each class has its own limit
    cfg = PowerFlowConfig(line_loading_limit_pct=150.0, transformer_loading_limit_pct=100.0)
    assert [v.location for v in find_violations(res, cfg, model)] == ["transformer.t"]
    # the engine's loading_pct (OpenDSS NormalAmps) is never used; no model, no overloads
    assert [v.kind for v in find_violations(res, PowerFlowConfig())] == []


def test_voltage_violation_diagnostic_names_the_bus() -> None:
    (v,) = find_violations(_result({"671.2": 0.9}), PowerFlowConfig())
    diag = v.to_diagnostic()
    assert diag.block == "bus.671" and diag.code == UNDERVOLTAGE


def test_unsolved_result_has_no_violations() -> None:
    assert find_violations(GridResult.failed([]), PowerFlowConfig()) == []


# ---- profile


def test_distances_follow_the_shortest_closed_path() -> None:
    dist = bus_distances(_model())
    assert dist == {
        "src": 0.0,
        "lv": 0.0,  # through the transformer
        "a": 100.0,
        "b": 300.0,
        "c": 350.0,
        "d": 300.0,
        "e": 300.0,  # closed switch: 0 m
    }  # f (open switch) and g (isolated) are unreachable


def test_disabled_line_breaks_the_path() -> None:
    m = _model()
    m.lines["l2"].enabled = False
    dist = bus_distances(m)
    assert "b" not in dist and "c" not in dist and dist["a"] == 100.0


def test_profile_points_and_unreachable_buses() -> None:
    res = _result({"c.1": 0.97, "c.3": 0.96, "a.1": 0.99, "a.4": 0.0, "f.1": 0.0, "g.2": math.nan})
    prof = voltage_profile(_model(), res)
    assert [(p.node, p.phase, p.distance_km, p.v_pu) for p in prof.points] == [
        ("a.1", "a", 0.1, 0.99),
        ("c.1", "a", 0.35, 0.97),
        ("c.3", "c", 0.35, 0.96),
    ]
    assert prof.unreachable == ["f"]  # g.2 has no base: not a profile node at all
    assert [d.block for d in prof.diagnostics] == ["bus.f"]
    assert prof.diagnostics[0].code == UNREACHABLE_IN_PROFILE


def test_profile_as_simulation_result() -> None:
    res = _result({"c.1": 0.97, "c.3": 0.96, "a.1": 0.99, "a.2": 0.98, "d.2": 0.95})
    sim = res.to_simulation_result(voltage_profile(_model(), res))
    assert sim.axis == "distance" and sim.analysis == "profile"
    assert sim.trace_names() == ["v_pu.a", "v_pu.b", "v_pu.c"]
    assert sim.units == {"distance": "km", "v_pu.a": "pu", "v_pu.b": "pu", "v_pu.c": "pu"}
    # one row per bus: a (0.1 km), then c and d (both 0.3x km; c 0.35, d 0.3)
    np.testing.assert_allclose(sim.vectors["distance"], [0.1, 0.3, 0.35])
    np.testing.assert_allclose(sim.vectors["v_pu.a"], [0.99, np.nan, 0.97])
    np.testing.assert_allclose(sim.vectors["v_pu.b"], [0.98, 0.95, np.nan])
    np.testing.assert_allclose(sim.vectors["v_pu.c"], [np.nan, np.nan, 0.96])


def test_empty_profile_as_simulation_result() -> None:
    res = _result({})
    sim = res.to_simulation_result(voltage_profile(_model(), res))
    assert sim.vectors["distance"].size == 0 and sim.vectors["v_pu.a"].size == 0


# ---- losses


def test_loss_rows_add_up_to_the_total() -> None:
    res = _result(
        {},
        [
            _branch("line.l1", 1.0),
            _branch("line.l2", 2.0),
            _branch("line.s1", 0.5),  # a switch in the model
            _branch("transformer.t", 4.0),
            _branch("reactor.r", 0.25),
        ],
    )
    res.total_losses_kw = 8.0
    res.total_losses_kvar = 16.0
    rows = {r.element_class: r for r in loss_summary(_model(), res)}
    assert list(rows) == ["line", "switch", "transformer", "other"]
    assert (rows["line"].count, rows["line"].kw) == (2, 3.0)
    assert (rows["switch"].count, rows["switch"].kw) == (1, 0.5)
    assert (rows["transformer"].count, rows["transformer"].kw) == (1, 4.0)
    assert rows["other"].count == 1
    assert rows["other"].kw == pytest.approx(0.5)  # reactor 0.25 + unassigned 0.25
    assert sum(r.kw for r in rows.values()) == pytest.approx(8.0)
    assert sum(r.kvar for r in rows.values()) == pytest.approx(16.0)


# ---- on real feeders


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", ["ieee13", "ieee34", "ieee123"])
def test_feeder_losses_by_class_add_up_to_opendss(feeder: str) -> None:
    pytest.importorskip("dss")
    model, result = solve_feeder(feeder)
    rows = loss_summary(model, result)
    ref = reference(feeder)
    assert sum(r.kw for r in rows) == pytest.approx(ref["losses_kw"], rel=1e-3)
    by = {r.element_class: r for r in rows}
    assert by["line"].count == len(model.lines)
    assert by["switch"].count == len(model.switches)
    assert by["transformer"].count == len(model.transformers)
    assert abs(by["other"].kw) < 1e-6 * ref["losses_kw"]  # only lines and transformers


@pytest.mark.opendss
def test_ieee13_profile_distances_from_the_file() -> None:
    pytest.importorskip("dss")
    model, result = solve_feeder("ieee13")
    prof = voltage_profile(model, result)
    dist = {p.bus: p.distance_km for p in prof.points}
    # every 671 path length is the sum of the model's own line lengths
    lines = model.lines
    path = ["650632", "632670", "670671"]
    assert dist["671"] == pytest.approx(sum(lines[n].length_m for n in path) / 1000.0)
    assert dist["650"] == 0.0 and dist["rg60"] == 0.0  # regulator transformer
    assert prof.unreachable == []
    assert {p.node for p in prof.points} <= set(result.node_names)
