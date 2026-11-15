"""core/grid/diagram/layout.py: file coordinates, auto layout (S3 packed-mid and
place_missing), shunt and branch glyphs, on a small model and IEEE 13/34/123."""

from __future__ import annotations

import copy
import itertools
import math
import random

import pytest

from core.grid.diagram import layout as lay
from core.grid.diagram import model as dm
from core.grid.model import LOAD_PQ, WYE, Bus, GridModel, Line, Load
from core.grid.topology import GridGraph
from tests.core.grid.test_validate import LV_LN, T, good_model

FEEDERS = ["ieee13", "ieee34", "ieee123"]


def _ieee(feeder: str) -> GridModel:
    pytest.importorskip("dss")
    from tests.core.grid.test_topology import _feeder_model

    return copy.deepcopy(_feeder_model(feeder))


def _without_coordinates(m: GridModel) -> GridModel:
    m = copy.deepcopy(m)
    for b in m.buses.values():
        b.x = b.y = None
    return m


def _segments(m: GridModel, pos: lay.Pos) -> list[tuple[str, str]]:
    graph = GridGraph.build(m)
    return sorted({tuple(sorted((e.bus_a, e.bus_b))) for e in graph.edges if e.bus_a != e.bus_b})


def _crossings(m: GridModel, pos: lay.Pos) -> int:
    segs = _segments(m, pos)
    n = 0
    for (a, b), (c, d) in itertools.combinations(segs, 2):
        if {a, b} & {c, d}:
            continue
        if lay.seg_cross(pos[a], pos[b], pos[c], pos[d]):
            n += 1
    return n


def _glyph_overlaps(m: GridModel, pos: lay.Pos) -> int:
    shunts = lay.shunt_counts(m)
    boxes = [lay._glyph_box(shunts, b, p) for b, p in pos.items()]
    return sum(1 for p, q in itertools.combinations(boxes, 2) if lay._boxes_overlap(p, q, 0.0))


# ---- file coordinates


def _with_coordinates() -> GridModel:
    m = good_model()
    for name, (x, y) in {"src": (0, 0), "a": (100, 0), "b": (200, 0), "c": (200, -50),
                         "d": (200, -150)}.items():  # fmt: skip
        m.buses[name].x, m.buses[name].y = x, y
    return m


def test_from_bus_coordinates_scales_median_branch_and_flips_y() -> None:
    pos = lay.from_bus_coordinates(_with_coordinates())
    # branch lengths 100, 100, 50, 100 -> median 100 -> 6 units; y down
    assert pos == {"src": (0, 0), "a": (6, 0), "b": (12, 0), "c": (12, 3), "d": (12, 9)}
    assert all(isinstance(v, float) for p in pos.values() for v in p)


def test_from_bus_coordinates_skips_missing_and_coincident() -> None:
    m = _with_coordinates()
    m.buses["d"].x = m.buses["d"].y = None
    m.buses["c"].x, m.buses["c"].y = 200, 0  # on top of b; b is nearer the source
    pos = lay.from_bus_coordinates(m)
    assert set(pos) == {"src", "a", "b"}


def test_no_coordinates_gives_empty() -> None:
    assert lay.from_bus_coordinates(good_model()) == {}


# ---- auto layout


def test_auto_layout_tree_places_every_bus_on_whole_units() -> None:
    m = good_model()
    pos = lay.auto_layout(m)
    assert set(pos) == set(m.buses)
    assert all(float(v).is_integer() for p in pos.values() for v in p)
    assert len(set(pos.values())) == len(pos)
    xs = [pos[b][0] for b in ("src", "a", "b", "c", "d")]
    assert xs == sorted(xs) and len(set(xs)) == 5  # depth is drawn left to right
    assert _crossings(m, pos) == 0 and _glyph_overlaps(m, pos) == 0


def test_auto_layout_is_deterministic_and_order_independent() -> None:
    m = good_model()
    shuffled = copy.deepcopy(m)
    names = list(shuffled.lines)
    random.Random(3).shuffle(names)
    shuffled.lines = {n: shuffled.lines[n] for n in names}
    assert lay.auto_layout(m) == lay.auto_layout(m) == lay.auto_layout(shuffled)


def test_auto_layout_keeps_fixed_and_places_missing() -> None:
    m = good_model()
    fixed = {"src": (0.0, 0.0), "a": (6.0, 0.0), "b": (12.0, 0.0)}
    pos = lay.auto_layout(m, fixed)
    assert {b: pos[b] for b in fixed} == fixed
    assert set(pos) == set(m.buses)
    assert len(set(pos.values())) == len(pos)
    for b in ("c", "d"):
        assert math.dist(pos[b], pos["b" if b == "c" else "c"]) <= lay.RADIUS * math.sqrt(2)
    assert _crossings(m, pos) == 0


def test_isolated_part_goes_below_everything() -> None:
    m = good_model()
    for name in ("e", "f"):
        m.buses[name] = Bus(name, LV_LN)
    m.lines["ef"] = Line("ef", T("e.1"), T("f.1"), 1, 10.0, "lc1")
    fixed = {"src": (0.0, 0.0), "a": (6.0, 0.0), "b": (12.0, 0.0)}
    pos = lay.auto_layout(m, fixed)
    lowest = max(pos[b][1] for b in ("src", "a", "b", "c", "d"))
    assert pos["e"][1] > lowest and pos["f"][1] > lowest
    assert pos["e"] != pos["f"]


def test_empty_model() -> None:
    m = GridModel("empty", 50.0)
    assert lay.auto_layout(m) == {}
    d = lay.build_diagram(m)
    assert d == dm.DiagramModel()


# ---- shunts and branches


def test_place_shunts_row_under_the_bus_without_overlap() -> None:
    m = good_model()
    for i in range(4):
        m.loads[f"x{i}"] = Load(f"x{i}", T("c"), 3, WYE, 0.4, 1.0, 0.0, LOAD_PQ)
    d = lay.build_diagram(m)
    on_c = [k for k in d.element_glyphs if k in {"load.ldd", "load.ldll"} or k.startswith("load.x")]
    bus = d.bus_glyphs["c"]
    assert bus.length == pytest.approx(6 * lay.SHUNT_PITCH)
    xs = sorted(d.element_glyphs[k].x for k in on_c)
    assert all(d.element_glyphs[k].y == bus.y + lay.SHUNT_DY for k in on_c)
    assert all(b - a >= 2 * lay.SHUNT_HALF for a, b in itertools.pairwise(xs))
    assert xs[0] - lay.SHUNT_HALF >= bus.x - bus.length / 2 - 1e-9
    assert xs[-1] + lay.SHUNT_HALF <= bus.x + bus.length / 2 + 1e-9
    assert sum(xs) / len(xs) == pytest.approx(bus.x)


def test_place_shunts_vertical_bus() -> None:
    m = good_model()
    buses = {"b": dm.BusGlyph(10.0, 20.0, 90, 2.0)}
    glyphs = lay.place_shunts(m, buses)
    assert glyphs == {"load.ld3": dm.ElementGlyph(12.0, 20.0, 90, 0.0, lay.LABEL_DY)}


def test_source_is_a_shunt_and_branch_sits_between_its_buses() -> None:
    m = good_model()
    d = lay.build_diagram(m)
    src, a = d.bus_glyphs["src"], d.bus_glyphs["a"]
    assert d.element_glyphs["vsource.s"].y == src.y + lay.SHUNT_DY
    line = d.element_glyphs["line.l1"]
    assert (line.x, line.y) == ((src.x + a.x) / 2, (src.y + a.y) / 2)


def test_element_on_unknown_bus_still_gets_a_glyph() -> None:
    m = good_model()
    m.loads["lost"] = Load("lost", T("nowhere"), 3, WYE, 0.4, 1.0, 0.0, LOAD_PQ)
    d = lay.build_diagram(m)
    assert "load.lost" in d.element_glyphs and "nowhere" not in d.bus_glyphs
    assert dm.check_consistency(m, d) == []


def test_build_diagram_uses_file_coordinates() -> None:
    m = _with_coordinates()
    d = lay.build_diagram(m)
    assert {b: (g.x, g.y) for b, g in d.bus_glyphs.items()} == lay.from_bus_coordinates(m)
    assert dm.check_consistency(m, d) == []


def test_moving_is_presentation_only() -> None:
    m = good_model()
    before = copy.deepcopy(m)
    lay.build_diagram(m)
    assert m == before  # layout never changes the GridModel


# ---- IEEE feeders


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_ieee_auto_layout_no_crossings_or_overlaps(feeder: str) -> None:
    m = _without_coordinates(_ieee(feeder))
    pos = lay.auto_layout(m)
    assert set(pos) == set(m.buses)
    assert len(set(pos.values())) == len(pos)
    assert _crossings(m, pos) == 0
    assert _glyph_overlaps(m, pos) == 0
    d = lay.build_diagram(m)
    assert dm.check_consistency(m, d) == []


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_ieee_diagram_from_file_coordinates(feeder: str) -> None:
    m = _ieee(feeder)
    coords = lay.from_bus_coordinates(m)
    d = lay.build_diagram(m)
    assert dm.check_consistency(m, d) == []
    for b, p in coords.items():  # file coordinates are kept
        assert (d.bus_glyphs[b].x, d.bus_glyphs[b].y) == p
    positions = [(g.x, g.y) for g in d.bus_glyphs.values()]
    assert len(set(positions)) == len(positions)


@pytest.mark.opendss
def test_ieee123_buses_without_coordinates_get_positions() -> None:
    m = _ieee("ieee123")
    missing = {b.name for b in m.buses.values() if b.x is None or b.y is None}
    assert {"300_open", "94_open"} <= missing
    coords = lay.from_bus_coordinates(m)
    assert not {"300_open", "94_open"} & set(coords)
    # the regulator buses on top of their bus: the one nearer the source keeps the spot
    assert {"150r", "9r", "25r", "160r"}.isdisjoint(coords)
    d = lay.build_diagram(m)
    others = {(g.x, g.y) for b, g in d.bus_glyphs.items() if b not in {"300_open", "94_open"}}
    for b in ("300_open", "94_open"):
        g = d.bus_glyphs[b]
        assert math.isfinite(g.x) and math.isfinite(g.y)
        assert (g.x, g.y) not in others
    assert _crossings(m, {b: (g.x, g.y) for b, g in d.bus_glyphs.items()}) == 0
