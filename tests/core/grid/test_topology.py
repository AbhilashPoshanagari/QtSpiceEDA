"""core/grid/topology.py: the grid graph on the IEEE feeders (every bus energized, zone
kV = OpenDSS kVBase, available nodes = OpenDSS bus nodes) and on small hand-built
models, plus the D1 diagnostics that validate.py asks it for."""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cache

import pytest

from core.grid import topology as tp
from core.grid import validate as v
from core.grid.model import (
    DELTA,
    LOAD_PQ,
    WYE,
    Bus,
    GridModel,
    Line,
    Load,
    Switch,
    Transformer,
    Winding,
)
from core.netlist.model import ERROR, INFO, WARNING
from tests.core.grid.test_validate import LV_LN, MV_LN, T, good_model

FEEDERS = ["ieee13", "ieee34", "ieee123"]


# ---- the IEEE feeders (OpenDSS needed to read them)


@cache
def _feeder_model(feeder: str) -> GridModel:
    from core.grid.engines.opendss.engine import OpenDSSEngine
    from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES

    engine = OpenDSSEngine()
    try:
        assert engine.load_native(FEEDER_FILES[feeder]) == []
        assert engine.context is not None
        for command in AFTER_COMPILE[feeder]:
            engine.context.Text.Command = command
        model, _ = engine.extract_model()
    finally:
        engine.close()
    assert model is not None
    return model


def _reference_buses(feeder: str) -> tuple[dict[str, set[int]], dict[str, float]]:
    """From opendss_reference.json: the nodes of every bus, and its kVBase (kV LN) as
    |V| / pu of its nodes (OpenDSS computes pu = |V| / kVBase)."""
    from tests.core.grid.helpers import reference

    ref = reference(feeder)
    nodes: dict[str, set[int]] = {}
    kv_base: dict[str, float] = {}
    for name, (re, im), pu in zip(ref["node_names"], ref["node_v"], ref["node_pu"], strict=True):
        bus, node = name.rsplit(".", 1)
        nodes.setdefault(bus, set()).add(int(node))
        base = abs(complex(re, im)) / pu / 1000.0
        if bus in kv_base:
            assert math.isclose(kv_base[bus], base, rel_tol=1e-9)
        kv_base[bus] = base
    return nodes, kv_base


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_ieee_every_bus_energized(feeder: str) -> None:
    pytest.importorskip("dss")
    model = _feeder_model(feeder)
    graph = tp.GridGraph.build(model)
    assert graph.energized_buses() == set(model.buses)
    assert set(graph.buses) == set(model.buses)
    assert graph.islands() == [tuple(model.buses)]


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_ieee_zone_kv_matches_opendss_kvbase(feeder: str) -> None:
    pytest.importorskip("dss")
    model = _feeder_model(feeder)
    zones = tp.GridGraph.build(model).voltage_zones()
    _, kv_base = _reference_buses(feeder)
    assert set(zones) == set(model.buses)
    assert set(kv_base) == set(model.buses)
    for bus, base_ln in kv_base.items():
        assert math.isclose(zones[bus] / math.sqrt(3.0), base_ln, rel_tol=1e-9), bus


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_ieee_available_nodes_match_opendss(feeder: str) -> None:
    pytest.importorskip("dss")
    model = _feeder_model(feeder)
    graph = tp.GridGraph.build(model)
    nodes, _ = _reference_buses(feeder)
    for bus in model.buses:
        assert set(graph.available_nodes(bus)) == nodes[bus], bus


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_ieee_derived_bases_equal_the_file(feeder: str) -> None:
    pytest.importorskip("dss")
    model = _feeder_model(feeder)
    derived = tp.derived_voltage_bases(model)
    # Every derived base is a file base and is used by some bus. The file may list
    # more: IEEE 34 sets 0.48 kV, which no bus has (a design keeps the file's bases).
    assert set(derived) <= set(model.voltage_bases)
    assert set(derived) == set(tp.GridGraph.build(model).voltage_zones().values())


@pytest.mark.opendss
def test_ieee13_parallel_regulators_are_no_loop() -> None:
    pytest.importorskip("dss")
    graph = tp.GridGraph.build(_feeder_model("ieee13"))
    assert graph.loops() == []
    chain = graph.path("sourcebus", "634")
    assert chain is not None
    assert [e.element_id for e in chain][-1] == "transformer.xfm1"


# ---- the good model of test_validate: src -l1- a =t1= b -sw1- c -l2(1-ph)- d


def test_graph_of_good_model() -> None:
    g = tp.GridGraph.build(good_model())
    assert g.buses == ["src", "a", "b", "c", "d"]
    assert [(e.element_id, e.kind, e.bus_a, e.bus_b) for e in g.edges] == [
        ("line.l1", tp.LINE, "src", "a"),
        ("line.l2", tp.LINE, "c", "d"),
        ("line.sw1", tp.SWITCH, "b", "c"),
        ("transformer.t1", tp.TRANSFORMER, "a", "b"),
    ]
    assert g.energized_buses() == {"src", "a", "b", "c", "d"}
    assert g.islands() == [("src", "a", "b", "c", "d")]
    assert g.loops() == []
    assert g.voltage_zones() == {"src": 11.0, "a": 11.0, "b": 0.41, "c": 0.41, "d": 0.41}
    assert g.available_nodes("b") == (1, 2, 3)
    assert g.available_nodes("d") == (1,)
    chain = g.path("src", "d")
    assert chain is not None
    assert [e.element_id for e in chain] == ["line.l1", "transformer.t1", "line.sw1", "line.l2"]
    assert g.path("d", "d") == []
    assert g.path("src", "nowhere") is None


def test_open_switch_splits_islands_but_not_zones_or_phases() -> None:
    m = good_model()
    m.switches["sw1"].closed = False
    g = tp.GridGraph.build(m)
    assert g.energized_buses() == {"src", "a", "b"}
    assert g.energized_buses(include_open=True) == {"src", "a", "b", "c", "d"}
    assert g.islands() == [("src", "a", "b"), ("c", "d")]
    assert g.path("src", "d") is None
    assert g.path("src", "d", include_open=True) is not None
    assert g.voltage_zones()["d"] == 0.41
    assert g.available_nodes("c") == (1, 2, 3)


def test_disabled_branch_is_not_an_edge() -> None:
    m = good_model()
    m.lines["l2"].enabled = False
    g = tp.GridGraph.build(m)
    assert "line.l2" not in {e.element_id for e in g.edges}
    assert "d" not in g.energized_buses()
    assert g.available_nodes("d") == ()
    assert "d" not in g.voltage_zones()


def test_line_maps_conductor_k_to_conductor_k() -> None:
    m = good_model()
    m.lines["l2"].bus2 = T("d.3")
    assert tp.GridGraph.build(m).available_nodes("d") == (3,)


def test_transformer_needs_all_its_phases_to_feed() -> None:
    m = good_model()
    m.lines["l1"] = Line("l1", T("src.1.2"), T("a.1.2"), 2, 100.0, "lc3")
    g = tp.GridGraph.build(m)
    assert g.available_nodes("a") == (1, 2)
    assert g.available_nodes("b") == ()


def test_three_winding_transformer_is_a_star() -> None:
    m = good_model()
    m.buses["e"] = Bus("e", LV_LN)
    m.transformers["t1"].windings.append(Winding(T("e.1.2.3.0"), WYE, 0.41, 500.0, 0.5))
    g = tp.GridGraph.build(m)
    pairs = [(e.winding_a, e.winding_b, e.bus_b) for e in g.edges if e.kind == tp.TRANSFORMER]
    assert pairs == [(1, 2, "b"), (1, 3, "e")]
    assert g.loops() == []
    assert g.available_nodes("e") == (1, 2, 3)


# ---- derived voltage bases


def _regulated() -> GridModel:
    """good_model plus a 1-phase wye regulator a.1 -> ar.1 rated 6.35 kV (LN)."""
    m = good_model()
    m.buses["ar"] = Bus("ar", MV_LN)
    m.transformers["reg"] = Transformer(
        "reg",
        1,
        [Winding(T("a.1"), WYE, 6.35, 100.0, 0.1), Winding(T("ar.1"), WYE, 6.35, 100.0, 0.1)],
        xhl=0.1,
        xht=0.1,
        xlt=0.1,
    )
    return m


def test_derived_bases_merge_single_phase_windings() -> None:
    m = _regulated()
    assert tp.derived_voltage_bases(m) == [0.41, 11.0]  # 6.35 * sqrt(3) = 10.998
    zones = tp.GridGraph.build(m).voltage_zones()
    assert zones["ar"] == 11.0
    m.transformers["reg"].windings[1].kv = 2.0  # a new 1-phase-only zone
    assert tp.derived_voltage_bases(m) == [0.41, 2.0 * math.sqrt(3.0), 11.0]


def test_derived_bases_skip_draft_and_disabled() -> None:
    m = good_model()
    m.transformers["t1"].windings[1].kv = None  # type: ignore[assignment]  # D2 draft
    m.sources["s"].enabled = False
    assert tp.derived_voltage_bases(m) == [11.0]
    assert tp.derived_voltage_bases(GridModel("empty", 50.0)) == []


def test_delta_and_line_to_line_single_phase_windings_are_ll() -> None:
    t = Transformer("x", 1, [], 0.0, 0.0, 0.0)
    assert tp.winding_kv_ll(t, Winding(T("a.1.2"), DELTA, 0.4, 1.0, 0.0)) == 0.4
    assert tp.winding_kv_ll(t, Winding(T("a.1.2"), WYE, 0.4, 1.0, 0.0)) == 0.4
    assert tp.winding_kv_ll(t, Winding(T("a.1.0"), WYE, 0.23, 1.0, 0.0)) == 0.23 * math.sqrt(3)


# ---- loops


def _with_tie(m: GridModel) -> GridModel:
    """A second 1-phase line c.1 -> d.1: parallel to l2 on the same phase."""
    m.lines["l3"] = Line("l3", T("c.1"), T("d.1"), 1, 60.0, "lc1")
    return m


def test_parallel_lines_on_one_phase_are_a_loop() -> None:
    loops = tp.GridGraph.build(_with_tie(good_model())).loops()
    assert loops == [tp.Loop(("line.l2", "line.l3"), ("c", "d"))]


def test_parallel_lines_on_different_phases_are_no_loop() -> None:
    m = good_model()
    m.lines["l3"] = Line("l3", T("c.2"), T("d.2"), 1, 60.0, "lc1")
    assert tp.GridGraph.build(m).loops() == []


def test_ring_is_a_loop_unless_the_switch_is_open() -> None:
    m = good_model()
    m.lines["ring"] = Line("ring", T("src.1.2.3"), T("c.1.2.3"), 3, 100.0, "lc3")
    g = tp.GridGraph.build(m)
    [loop] = g.loops()
    assert loop.element_ids[-1] == "transformer.t1"  # the last edge built closes it
    assert set(loop.element_ids) == {"line.l1", "transformer.t1", "line.sw1", "line.ring"}
    m.switches["sw1"].closed = False
    assert tp.GridGraph.build(m).loops() == []


# ---- D1 diagnostics through validate()


def _only(model: GridModel, code: str) -> list[tuple[str, str]]:
    return [(d.block, d.severity) for d in v.validate(model) if d.code == code]


def _cross_zone(m: GridModel) -> None:
    m.lines["lx"] = Line("lx", T("a.1.2.3"), T("b.1.2.3"), 3, 10.0, "lc3")


def _node_missing(m: GridModel) -> None:
    m.loads["ld1"].terminal = T("d.2.0")


def _node_missing_line(m: GridModel) -> None:
    m.buses["e"] = Bus("e", LV_LN)
    m.lines["l7"] = Line("l7", T("d.3"), T("e.3"), 1, 10.0, "lc1")


# (id, mutation, code, element, severity)
D1_FAILURES: list[tuple[str, Callable[[GridModel], None], str, str, str]] = [
    ("cross zone", _cross_zone, v.ZONE_MISMATCH, "line.lx", ERROR),
    ("load node", _node_missing, v.NODE_NOT_AVAILABLE, "load.ld1", ERROR),
    ("line node", _node_missing_line, v.NODE_NOT_AVAILABLE, "line.l7", ERROR),
    ("loop", _with_tie, v.LOOP, "line.l3", INFO),
]


@pytest.mark.parametrize(
    ("mutate", "code", "element", "severity"),
    [f[1:] for f in D1_FAILURES],
    ids=[f[0] for f in D1_FAILURES],
)
def test_d1_diagnostic_on_the_element(
    mutate: Callable[[GridModel], None], code: str, element: str, severity: str
) -> None:
    m = good_model()
    mutate(m)
    assert _only(m, code) == [(element, severity)]


def test_every_d1_code_has_a_case() -> None:
    assert {f[2] for f in D1_FAILURES} == set(v.TOPOLOGY_CODES)
    assert len(set(v.ALL_CODES)) == len(v.ALL_CODES)


def test_node_message_names_bus_and_nodes() -> None:
    m = good_model()
    _node_missing_line(m)
    [d] = [d for d in v.validate(m) if d.code == v.NODE_NOT_AVAILABLE]
    assert d.message == "line.l7 needs node 3 at bus.d, which has 1"


def test_cross_zone_message_names_both_zones() -> None:
    m = good_model()
    _cross_zone(m)
    [d] = [d for d in v.validate(m) if d.code == v.ZONE_MISMATCH]
    assert "bus a is in the 11 kV zone" in d.message
    assert "bus b in the 0.41 kV zone" in d.message


def test_not_energized_bus_with_only_branches_is_a_warning() -> None:
    m = good_model()
    m.buses["e"] = Bus("e", LV_LN)
    m.buses["f"] = Bus("f", LV_LN)
    m.lines["ef"] = Line("ef", T("e.1"), T("f.1"), 1, 10.0, "lc1")
    assert _only(m, v.UNREACHABLE_BUS) == [("bus.e", WARNING), ("bus.f", WARNING)]
    m.loads["lf"] = Load("lf", T("f.1.0"), 1, WYE, 0.23, 1.0, 0.0, LOAD_PQ)
    assert _only(m, v.UNREACHABLE_BUS) == [("bus.e", WARNING), ("bus.f", ERROR)]
    m.loads["lf"].enabled = False
    assert _only(m, v.UNREACHABLE_BUS) == [("bus.e", WARNING), ("bus.f", WARNING)]


def test_unenergized_bus_gives_no_node_diagnostic() -> None:
    m = good_model()
    m.lines["l2"].enabled = False
    m.loads["ld1"].terminal = T("d.3.0")
    assert _only(m, v.NODE_NOT_AVAILABLE) == []


def test_open_tie_switch_is_no_loop_but_zone_checked() -> None:
    m = good_model()
    m.switches["tie"] = Switch("tie", T("a"), T("b"), 3, False, 1e-4)
    assert _only(m, v.LOOP) == []
    assert _only(m, v.ZONE_MISMATCH) == [("line.tie", ERROR)]


def test_graph_build_failure_is_a_validation_error() -> None:
    m = good_model()
    m.switches["sw1"] = None  # type: ignore[assignment]
    found = [d for d in v.validate(m) if d.code == v.VALIDATION_FAILED]
    assert found and all(d.block == v.MODEL and d.severity == ERROR for d in found)
    assert any("'topology'" in d.message for d in found)


def test_core_grid_topology_is_qt_and_dss_free() -> None:
    from pathlib import Path

    text = Path(tp.__file__).read_text(encoding="utf-8")
    assert "PySide6" not in text and "import dss" not in text and "from dss" not in text


# ---- D3: deterministic walks for the diagram layout


def test_connections_merge_parallel_branches_and_prefer_closed() -> None:
    m = good_model()
    m.switches["tie"] = Switch("tie", T("a"), T("b"), 3, False, 1e-4)
    graph = tp.GridGraph.build(m)
    conns = graph.connections("a")
    assert [n for n, _ in conns] == ["b", "src"]  # name order, one per neighbour
    assert conns[0][1].element_id == "transformer.t1"  # closed transformer, not the tie
    assert [n for n, _ in graph.connections("b", include_open=False)] == ["a", "c"]


def test_spanning_forest_closed_first_then_open_then_islands() -> None:
    m = good_model()
    m.switches["sw1"].closed = False  # c and d now hang behind an open switch
    m.buses["e"] = Bus("e", LV_LN)  # an isolated bus: a tree of its own
    forest = tp.GridGraph.build(m).spanning_forest()
    assert forest.roots == ["src", "e"]
    assert forest.order == ["src", "a", "b", "c", "d", "e"]
    assert forest.parent == {"src": None, "a": "src", "b": "a", "c": "b", "d": "c", "e": None}
    assert forest.edge["c"].element_id == "line.sw1"
    assert forest.edge["b"].kind == tp.TRANSFORMER


def test_spanning_forest_without_source_starts_at_first_bus() -> None:
    m = good_model()
    m.sources.clear()
    forest = tp.GridGraph.build(m).spanning_forest()
    assert forest.roots == ["src"]
    assert set(forest.order) == set(m.buses)


def test_hop_distance() -> None:
    graph = tp.GridGraph.build(good_model())
    assert graph.hop_distance(["src"]) == {"src": 0, "a": 1, "b": 2, "c": 3, "d": 4}
    assert graph.hop_distance(["c"])["src"] == 3


# ---- D4: elements at a bus, disconnected terminals


def test_connected_elements_in_model_order() -> None:
    m = good_model()
    assert tp.connected_elements(m, "src") == ["vsource.s", "line.l1"]
    assert tp.connected_elements(m, "b") == ["line.sw1", "transformer.t1", "load.ld3"]
    assert tp.connected_elements(m, "c") == ["line.l2", "line.sw1", "load.ldd", "load.ldll"]
    m.lines["l1"].enabled = False  # disabled elements are still connected
    assert "line.l1" in tp.connected_elements(m, "a")
    assert tp.connected_elements(m, "nope") == []
    assert tp.connected_elements(m, tp.DISCONNECTED) == []


def test_disconnected_terminals_are_not_in_the_graph() -> None:
    m = good_model()
    m.lines["l2"].bus2 = T(tp.DISCONNECTED)
    m.loads["ld1"].terminal = T(tp.DISCONNECTED)
    m.transformers["t1"].windings[1].terminal = T(tp.DISCONNECTED)
    g = tp.GridGraph.build(m)
    assert tp.DISCONNECTED not in g.buses
    assert not any(e.element_id in ("line.l2", "transformer.t1") for e in g.edges)
    assert "b" not in g.voltage_zones()  # its only anchor was the disconnected winding
    msgs = {(d.block, d.message) for d in v.validate(m) if d.code == v.UNKNOWN_BUS}
    assert ("line.l2", "bus2 is not connected to a bus") in msgs
    assert ("transformer.t1", "winding 2 is not connected to a bus") in msgs
