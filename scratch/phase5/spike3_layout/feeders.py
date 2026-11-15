"""S3 spike: IEEE feeders as layout graphs, and the layout metrics (Qt-free).

The feeders load through OpenDSSEngine + extract, in-process (fine for a spike, never in
the app, rule 5), in the same state as their reference files
(tests/core/grid/helpers.solve_feeder: bus coordinates included).
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import (  # noqa: E402
    BUS_HALF_H,
    SHUNT_DY,
    SHUNT_HALF,
    SHUNT_PITCH,
    Edge,
    Graph,
    Pos,
    seg_cross,
    seg_hits_box,
)

from core.grid.model import GridModel  # noqa: E402

FEEDERS = ("ieee13", "ieee34", "ieee123")


def load_model(feeder: str) -> GridModel:
    from tests.core.grid.helpers import solve_feeder

    model, _result = solve_feeder(feeder)
    return model


def build_graph(model: GridModel) -> Graph:
    edges: list[Edge] = []
    for ln in model.lines.values():
        edges.append(Edge(ln.id, "line", ln.bus1.bus, ln.bus2.bus, ln.enabled))
    for sw in model.switches.values():
        edges.append(Edge(sw.id, "switch", sw.bus1.bus, sw.bus2.bus, sw.closed and sw.enabled))
    for tr in model.transformers.values():
        first = tr.windings[0].terminal.bus
        for w in tr.windings[1:]:
            edges.append(Edge(tr.id, "transformer", first, w.terminal.bus, tr.enabled))
    shunts: dict[str, int] = {}
    for bus in [s.bus1.bus for s in model.sources.values()] + [
        el.terminal.bus
        for group in (model.loads, model.capacitors, model.pvsystems)
        for el in group.values()
    ]:
        shunts[bus] = shunts.get(bus, 0) + 1
    source = next(iter(model.sources.values())).bus1.bus
    return Graph(sorted(model.buses), edges, source, shunts)


def file_coordinates(model: GridModel) -> dict[str, tuple[float, float]]:
    return {n: (b.x, b.y) for n, b in model.buses.items() if b.x is not None and b.y is not None}


def replicate(graph: Graph, copies: int) -> Graph:
    """``copies`` copies of a feeder, all fed from one new root bus (for timing)."""
    buses = ["root"]
    edges: list[Edge] = []
    for c in range(copies):
        sfx = f"_c{c}"
        buses += [b + sfx for b in graph.buses]
        edges += [Edge(e.id + sfx, e.kind, e.a + sfx, e.b + sfx, e.closed) for e in graph.edges]
        edges.append(Edge(f"line.feed{c}", "line", "root", graph.source + sfx))
    shunts = {b + f"_c{c}": n for c in range(copies) for b, n in graph.shunts.items()}
    return Graph(sorted(buses), edges, "root", shunts)


# -------------------------------------------------------------------- metrics


def glyph_boxes(graph: Graph, pos: Pos) -> dict[str, list[tuple[float, float, float, float]]]:
    """Bus -> [bus bar box, shunt boxes...]: what the canvas draws for that bus."""
    out: dict[str, list[tuple[float, float, float, float]]] = {}
    for b, (x, y) in pos.items():
        half = graph.bus_length(b) / 2
        boxes = [(x - half, y - BUS_HALF_H, x + half, y + BUS_HALF_H)]
        n = graph.shunts.get(b, 0)
        for k in range(n):
            sx = x + (k - (n - 1) / 2) * SHUNT_PITCH
            sy = y + SHUNT_DY
            boxes.append((sx - SHUNT_HALF, sy - SHUNT_HALF, sx + SHUNT_HALF, sy + SHUNT_HALF))
        out[b] = boxes
    return out


def segments(graph: Graph, pos: Pos) -> list[tuple[str, str]]:
    """One straight segment per connected bus pair (parallel branches drawn as one)."""
    return sorted(
        {tuple(sorted((e.a, e.b))) for e in graph.edges if e.a != e.b and e.a in pos and e.b in pos}
    )


def _overlap(p: tuple[float, ...], q: tuple[float, ...]) -> bool:
    return p[0] < q[2] and q[0] < p[2] and p[1] < q[3] and q[1] < p[3]


@dataclass
class Metrics:
    buses: int
    segments: int
    crossings: int  # pairs of branch segments that intersect (no shared bus)
    overlaps: int  # pairs of buses whose glyphs (bar or shunts) overlap
    through: int  # (segment, bus) pairs: a branch drawn through another bus's glyph
    mean_len: float  # branch segment length, diagram units
    max_len: float
    width: float
    height: float
    crossing_points: list[tuple[float, float]]
    overlap_buses: list[str]


def measure(graph: Graph, pos: Pos) -> Metrics:
    segs = segments(graph, pos)
    crossings = 0
    points: list[tuple[float, float]] = []
    for (a, b), (c, d) in combinations(segs, 2):
        if len({a, b, c, d}) < 4:
            continue
        if seg_cross(pos[a], pos[b], pos[c], pos[d]):
            crossings += 1
            points.append(_meet(pos[a], pos[b], pos[c], pos[d]))
    boxes = glyph_boxes(graph, pos)
    overlaps = 0
    bad: set[str] = set()
    names = sorted(boxes)
    for u, v in combinations(names, 2):
        if any(_overlap(p, q) for p in boxes[u] for q in boxes[v]):
            overlaps += 1
            bad |= {u, v}
    through = 0
    for a, b in segs:
        for v in names:
            if v not in (a, b) and any(seg_hits_box(pos[a], pos[b], bx) for bx in boxes[v]):
                through += 1
    lens = [math.dist(pos[a], pos[b]) for a, b in segs] or [0.0]
    allx = [bx[i] for bs in boxes.values() for bx in bs for i in (0, 2)]
    ally = [bx[i] for bs in boxes.values() for bx in bs for i in (1, 3)]
    return Metrics(
        len(pos),
        len(segs),
        crossings,
        overlaps,
        through,
        round(sum(lens) / len(lens), 2),
        round(max(lens), 2),
        max(allx) - min(allx),
        max(ally) - min(ally),
        points,
        sorted(bad),
    )


def _meet(p1, p2, q1, q2) -> tuple[float, float]:
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if d == 0:  # collinear overlap: mark the middle of the shared part
        xs = sorted([p1, p2, q1, q2])
        return ((xs[1][0] + xs[2][0]) / 2, (xs[1][1] + xs[2][1]) / 2)
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
