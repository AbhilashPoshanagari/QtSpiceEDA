"""core/grid/diagram/hittest.py (D6): what a click or a rubber band hits.

S1 rule: the nearest shape within the tolerance wins, the kind only breaks ties; the
spatial index gives the same answer as a linear scan.
"""

from __future__ import annotations

import random

from core.grid.design_io import GridDesign
from core.grid.diagram import hittest as h
from core.grid.diagram.layout import build_diagram
from core.grid.diagram.model import BusGlyph, ElementGlyph
from tests.core.grid.test_validate import good_model


def _design() -> GridDesign:
    m = good_model()
    return GridDesign(m, build_diagram(m))


def _index(design: GridDesign) -> h.HitIndex:
    return h.HitIndex(h.design_shapes(design))


def test_every_bus_and_drawn_element_has_a_shape() -> None:
    d = _design()
    ids = {s.id for s in h.design_shapes(d)}
    assert {f"bus.{b}" for b in d.grid.buses} <= ids
    assert set(d.diagram.element_glyphs) <= ids
    assert not any(i.startswith(("linecode.", "regcontrol.")) for i in ids)


def test_segments_follow_the_terminals() -> None:
    d = _design()
    g = d.diagram
    g.bus_glyphs["a"] = BusGlyph(0.0, 0.0, 0, 4.0)
    g.bus_glyphs["src"] = BusGlyph(10.0, -6.0, 90, 2.0)
    line = d.grid.lines["l1"]
    ((p, q),) = h.element_segments("lines", line, g.element_glyphs["line.l1"], g.bus_glyphs)
    assert p == (10.0, -5.0)  # the vertical bar's end nearest to bus a
    assert q == (2.0, 0.0)  # the horizontal bar's end nearest to src
    g.element_glyphs["capacitor.cap1"] = ElementGlyph(1.0, 2.0)
    cap = d.grid.capacitors["cap1"]
    (p, q), = h.element_segments("capacitors", cap, g.element_glyphs["capacitor.cap1"],
                                 g.bus_glyphs)  # fmt: skip
    assert p == (1.0, 2.0) and q == (1.0, 0.0)
    # a terminal on a bus without a glyph draws nothing
    del g.bus_glyphs["a"]
    assert h.element_segments("lines", line, g.element_glyphs["line.l1"], g.bus_glyphs) == []


def test_nearest_wins_and_the_kind_only_breaks_ties() -> None:
    bus = h.Shape("bus.a", h.BUS, (-1.0, 0.0), (1.0, 0.0), 0.25)
    load = h.Shape("load.x", h.SYMBOL, (0.0, 2.0), (0.0, 2.0), 0.5)
    stub = h.Shape("load.x", h.EDGE, (0.0, 2.0), (0.0, 0.0), 0.0, band=False)
    line = h.Shape("line.l", h.EDGE, (1.0, 0.0), (9.0, 0.0))
    idx = h.HitIndex([bus, load, stub, line])
    # at fit zoom the tolerance is large: near the bar, the bar wins over its load
    assert idx.hit(-0.5, 0.3, 1.3) == "bus.a"
    assert idx.hit(0.0, 1.7, 1.3) == "load.x"
    assert idx.hit(0.0, 1.0, 0.2) == "load.x"  # on the stub
    assert idx.hit(5.0, 0.4, 0.5) == "line.l"
    assert idx.hit(5.0, 3.0, 0.5) is None
    # at the bar's end the bus and the line are both at distance 0: the bus wins
    assert idx.hit(1.0, 0.0, 0.5) == "bus.a"
    assert idx.hit(float("nan"), 0.0, 1.0) is None


def test_index_matches_a_linear_scan() -> None:
    idx = _index(_design())
    x0, y0, x1, y1 = idx.bounds()  # type: ignore[misc]
    rnd = random.Random(5)
    hits = 0
    for _ in range(3000):
        x, y = rnd.uniform(x0 - 2, x1 + 2), rnd.uniform(y0 - 2, y1 + 2)
        tol = rnd.choice((0.1, 0.5, 1.5))
        got = idx.hit(x, y, tol)
        assert got == idx.hit_linear(x, y, tol)
        hits += got is not None
    assert hits > 100


def test_rubber_band_takes_what_is_wholly_inside() -> None:
    d = _design()
    idx = _index(d)
    x0, y0, x1, y1 = idx.bounds()  # type: ignore[misc]
    every = idx.in_rect(x0, y0, x1, y1)
    assert set(every) == {s.id for s in idx.shapes}
    g = d.diagram.bus_glyphs["d"]
    near = idx.in_rect(g.x - 3, g.y - 0.5, g.x + 3, g.y + 4)
    assert "bus.d" in near and "load.ld1" in near  # the stub does not count
    assert "line.l2" not in near  # it reaches bus c
    assert idx.in_rect(x1 + 5, y1 + 5, x1 + 9, y1 + 9) == []


def test_empty_design() -> None:
    idx = h.HitIndex([])
    assert idx.bounds() is None and idx.hit(0, 0, 1) is None and idx.in_rect(0, 0, 1, 1) == []
