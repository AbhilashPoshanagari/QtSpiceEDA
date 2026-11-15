"""Glyph positions for a grid design. Qt-free, presentation only (rule 20).

Every graph question (spanning forest, hop distance, neighbours) is asked of
``core.grid.topology.GridGraph`` (rule 25); this module only orders and places.
Decisions from spike S3 (scratch/phase5/spike3_layout/README.md):

- ``from_bus_coordinates(grid)``: the file's bus coordinates (Bus.x / Bus.y, y up)
  scaled so the median branch is TARGET_LEN units, snapped to whole units, y down.
  Buses that land on the same point: the one nearest the source keeps it, the others
  are left out (IEEE 123 150r/9r/25r/160r) and get placed by ``auto_layout``.
- ``auto_layout(grid, fixed)``: positions of every bus. With nothing fixed, the
  "packed-mid" layered tree from the source. Otherwise every fixed bus keeps its
  position and each missing bus is put next to a placed neighbour, at the free grid
  point that adds the fewest overlaps and crossings (``place_missing`` in S3). Parts
  with no branch to a placed bus get the tree layout below everything.
- ``place_shunts``: sources, loads, capacitors and PV in one row SHUNT_DY under their
  bus (to the right of a vertical bus), SHUNT_PITCH apart, centred; a bus bar is
  ``bus_length`` long so its row fits on it.
- ``place_branches``: the symbol anchor of a line, switch or transformer at the middle
  of its buses.
- ``build_diagram(grid)``: a whole DiagramModel; file coordinates when at least
  MIN_COORDINATE_SHARE of the buses have them, else the tree layout for everything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.grid.diagram.model import (
    BRANCH_CLASSES,
    GLYPH_CLASSES,
    SHUNT_CLASSES,
    BusGlyph,
    DiagramModel,
    ElementGlyph,
)
from core.grid.model import GridModel
from core.grid.topology import TRANSFORMER, Edge, GridGraph, SpanningForest

# Glyph geometry, in diagram units (shared with the S1 canvas glyph sizes).
BUS_MIN = 2.0
BUS_HALF_H = 0.25
SHUNT_PITCH = 1.2
SHUNT_HALF = 0.5
SHUNT_DY = 2.0
ROW = 4
"""Row pitch of the tree layout."""
COL_GAP = 3
"""Free space between the bus bars of two neighbouring columns."""
TARGET_LEN = 6.0
"""Wanted branch length when scaling file coordinates or placing a bus."""
CLEARANCE = 1.0
"""Free space a placed bus keeps around its glyph."""
MIN_LEN = 3.0
"""Shortest branch a placed bus may get."""
RADIUS = 12
"""Grid points within this many units of the neighbour are tried for a missing bus."""
LABEL_DY = -1.0
"""Default label offset of a glyph (above it)."""
MIN_COORDINATE_SHARE = 0.5
"""build_diagram uses file coordinates only when at least this share of buses has them."""

Point = tuple[float, float]
Pos = dict[str, Point]
Box = tuple[float, float, float, float]


# ------------------------------------------------------------------ shunt counts


def shunt_counts(grid: GridModel) -> dict[str, int]:
    """Bus -> number of shunt glyphs (sources, loads, capacitors, PV) on it."""
    out: dict[str, int] = {}
    for name in SHUNT_CLASSES:
        for el in getattr(grid, name).values():
            bus = el.bus1.bus if name == "sources" else el.terminal.bus
            out[bus] = out.get(bus, 0) + 1
    return out


def bus_length(shunts: int) -> float:
    return max(BUS_MIN, shunts * SHUNT_PITCH)


# ------------------------------------------------------------------ file coordinates


def from_bus_coordinates(grid: GridModel, graph: GridGraph | None = None) -> Pos:
    """Bus coordinates from the file -> diagram units (see the module docstring).
    Buses without coordinates, and all but one of buses on the same point, are not in
    the result."""
    graph = graph or GridGraph.build(grid)
    coords = {
        b.name: (float(b.x), float(b.y))
        for b in grid.buses.values()
        if b.x is not None and b.y is not None and math.isfinite(b.x) and math.isfinite(b.y)
    }
    lengths = sorted(
        math.dist(coords[e.bus_a], coords[e.bus_b])
        for e in graph.edges
        if e.bus_a in coords and e.bus_b in coords and coords[e.bus_a] != coords[e.bus_b]
    )
    scale = TARGET_LEN / lengths[len(lengths) // 2] if lengths else 1.0
    scaled = {
        b: (float(round(x * scale)), float(round(-y * scale))) for b, (x, y) in coords.items()
    }

    depth = graph.hop_distance(_roots(graph))
    groups: dict[Point, list[str]] = {}
    for b in sorted(scaled):
        groups.setdefault(scaled[b], []).append(b)
    keep = {min(g, key=lambda b: (depth.get(b, 1 << 30), b)) for g in groups.values()}
    return {b: p for b, p in scaled.items() if b in keep}


def _roots(graph: GridGraph) -> list[str]:
    roots = list(dict.fromkeys(b for b, _, _ in graph.source_buses))
    return roots or graph.buses[:1]


# ------------------------------------------------------------------ tree layout


def _span(e: Edge) -> int:
    """Columns between parent and child: a transformer adds one extra column."""
    return 2 if e.kind == TRANSFORMER else 1


@dataclass
class _Tree:
    roots: list[str]
    children: dict[str, list[str]]
    edge: dict[str, Edge]
    col: dict[str, int]


def _tree(forest: SpanningForest) -> _Tree:
    children: dict[str, list[str]] = {b: [] for b in forest.order}
    col: dict[str, int] = {}
    for b in forest.order:
        p = forest.parent[b]
        if p is None:
            col[b] = 0
        else:
            children[p].append(b)
            col[b] = col[p] + _span(forest.edge[b])
    height: dict[str, int] = {}
    size: dict[str, int] = {}
    for b in reversed(forest.order):
        kids = children[b]
        height[b] = 1 + max((height[k] for k in kids), default=0)
        size[b] = 1 + sum(size[k] for k in kids)
    for kids in children.values():  # deepest subtree first: the longest path is one row
        kids.sort(key=lambda k: (-height[k], -size[k], k))
    return _Tree(list(forest.roots), children, dict(forest.edge), col)


def _rows_packed_mid(tree: _Tree) -> dict[str, float]:
    """Rows of the "packed-mid" variant: each child subtree moves up into the rows free
    in the columns it uses (per-column contours); a bus sits midway between its first
    and last child."""
    rel: dict[str, float] = {}

    def contour(b: str) -> dict[int, tuple[float, float]]:
        acc: dict[int, tuple[float, float]] = {tree.col[b]: (0, 0)}
        offsets: list[tuple[str, float]] = []
        for i, k in enumerate(tree.children[b]):
            ck = contour(k)
            skips = _span(tree.edge[k]) == 2
            if skips:  # the skipped column, at the child's row
                ck[tree.col[b] + 1] = (0, 0)
            if i == 0:
                off: float = 0
            else:
                off = offsets[-1][1] + 1
                for c, (top, _bot) in ck.items():
                    if c in acc:
                        off = max(off, acc[c][1] - top + 1)
                if skips:
                    between = [o for s, o in offsets if tree.col[s] == tree.col[b] + 1]
                    while any(abs(r - off / 2) < 0.75 for r in between):
                        off += 1
            offsets.append((k, off))
            rel[k] = off
            for c, (top, bot) in ck.items():
                t, u = top + off, bot + off
                acc[c] = (min(acc[c][0], t), max(acc[c][1], u)) if c in acc else (t, u)
        if offsets:
            m = (offsets[0][1] + offsets[-1][1]) / 2
            for k, _ in offsets:
                rel[k] -= m
            acc = {c: (t - m, u - m) for c, (t, u) in acc.items()}
            acc[tree.col[b]] = (0, 0)
        return acc

    rows: dict[str, float] = {}
    base: float = 0
    for r in tree.roots:
        cont = contour(r)
        rows[r] = base - min(t for t, _ in cont.values())
        stack = [r]
        while stack:
            u = stack.pop()
            for k in tree.children[u]:
                rows[k] = rows[u] + rel[k]
                stack.append(k)
        base = math.ceil(max(rows.values())) + 2
    return rows


def _tree_positions(tree: _Tree, shunts: dict[str, int]) -> Pos:
    width: dict[int, float] = {}
    for b, c in tree.col.items():
        width[c] = max(width.get(c, BUS_MIN), bus_length(shunts.get(b, 0)))
    xs: dict[int, float] = {0: 0.0}
    for c in range(1, max(tree.col.values(), default=0) + 1):
        prev, cur = width.get(c - 1, BUS_MIN), width.get(c, BUS_MIN)
        xs[c] = xs[c - 1] + math.ceil(prev / 2 + COL_GAP + cur / 2)
    rows = _rows_packed_mid(tree)
    return {b: (xs[tree.col[b]], rows[b] * ROW) for b in tree.col}


# ------------------------------------------------------------------ geometry


def _glyph_box(shunts: dict[str, int], bus: str, p: Point) -> Box:
    """Bounding box of a bus bar plus its shunt row."""
    n = shunts.get(bus, 0)
    half = bus_length(n) / 2
    bottom = p[1] + (SHUNT_DY + SHUNT_HALF if n else BUS_HALF_H)
    return p[0] - half, p[1] - BUS_HALF_H, p[0] + half, bottom


def _boxes_overlap(p: Box, q: Box, margin: float) -> bool:
    return (
        p[0] - margin < q[2]
        and q[0] - margin < p[2]
        and p[1] - margin < q[3]
        and q[1] - margin < p[3]
    )


def seg_cross(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    """Proper intersection or collinear overlap of two segments."""

    def orient(a: Point, b: Point, c: Point) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on(a: Point, b: Point, c: Point) -> bool:
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(
            a[1], b[1]
        )

    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return (
        (d1 == 0 and on(q1, q2, p1))
        or (d2 == 0 and on(q1, q2, p2))
        or (d3 == 0 and on(p1, p2, q1))
        or (d4 == 0 and on(p1, p2, q2))
    )


def seg_hits_box(p: Point, q: Point, box: Box) -> bool:
    """Liang-Barsky: does segment p-q pass through the (open) box?"""
    x0, y0, x1, y1 = box
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for pk, qk in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if pk == 0:
            if qk <= 0:
                return False
        else:
            t = qk / pk
            if pk < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 >= t1:
                return False
    return True


# ------------------------------------------------------------------ auto layout


def auto_layout(grid: GridModel, fixed: Pos | None = None, graph: GridGraph | None = None) -> Pos:
    """Positions of every bus of the graph (model buses and buses only named by an
    element). ``fixed`` positions are kept exactly; see the module docstring."""
    graph = graph or GridGraph.build(grid)
    shunts = shunt_counts(grid)
    fixed = {b: p for b, p in (fixed or {}).items() if b in set(graph.buses)}
    if not fixed:
        return _tree_positions(_tree(graph.spanning_forest()), shunts)

    pos: Pos = dict(fixed)
    found = graph.bfs_tree(sorted(pos), include_open=True)
    offsets = [
        (dx, dy)
        for dx in range(-RADIUS, RADIUS + 1)
        for dy in range(-RADIUS, RADIUS + 1)
        if (dx, dy) != (0, 0)
    ]
    for bus, nb, _ in found:
        pos[bus] = _best_spot(graph, shunts, pos, bus, nb, offsets)

    missing = [b for b in graph.buses if b not in pos]
    if missing:  # parts with no branch to a placed bus: tree layout, below everything
        tree = _tree(graph.spanning_forest())
        lost = set(missing)
        tree.roots = [r for r in tree.roots if r in lost]
        tree.col = {b: c for b, c in tree.col.items() if b in lost}
        lay = _tree_positions(tree, shunts)
        bottom = max(p[1] for p in pos.values()) + 2 * ROW
        left = min(p[0] for p in pos.values())
        for b in missing:
            x, y = lay[b]
            pos[b] = (left + x, bottom + y)
    return pos


def _best_spot(
    graph: GridGraph,
    shunts: dict[str, int],
    pos: Pos,
    bus: str,
    nb: str,
    offsets: list[tuple[int, int]],
) -> Point:
    nx, ny = pos[nb]
    others = [pos[v] for v, _ in graph.connections(nb) if v in pos and v != bus]
    if others:  # away from the neighbour's other branches
        cx = sum(p[0] for p in others) / len(others)
        cy = sum(p[1] for p in others) / len(others)
        dx, dy = nx - cx, ny - cy
        n = math.hypot(dx, dy) or 1.0
        want = (nx + dx / n * TARGET_LEN, ny + dy / n * TARGET_LEN)
    else:
        want = (nx + TARGET_LEN, ny)
    links = [v for v, _ in graph.connections(bus) if v in pos]
    segs = sorted(
        {
            tuple(sorted((e.bus_a, e.bus_b)))
            for e in graph.edges
            if e.bus_a != e.bus_b and e.bus_a in pos and e.bus_b in pos
        }
    )
    boxes = {v: _glyph_box(shunts, v, p) for v, p in pos.items()}
    best: tuple[tuple[float, ...], Point] | None = None
    cands = sorted(offsets, key=lambda d: (round(math.dist((nx + d[0], ny + d[1]), want), 6), d))
    for ox, oy in cands:
        c = (nx + ox, ny + oy)
        box = _glyph_box(shunts, bus, c)
        overlaps = sum(1 for bx in boxes.values() if _boxes_overlap(box, bx, CLEARANCE))
        short = sum(1 for v in links if math.dist(c, pos[v]) < MIN_LEN)
        crossings = through = 0
        for v in links:
            for s, t in segs:
                if v not in (s, t) and seg_cross(c, pos[v], pos[s], pos[t]):
                    crossings += 1
            for w, bx in boxes.items():
                if w != v and seg_hits_box(c, pos[v], bx):
                    through += 1
        for s, t in segs:
            if seg_hits_box(pos[s], pos[t], box):
                through += 1
        score = (overlaps, crossings, through, short, round(math.dist(c, want), 6), ox, oy)
        if best is None or score < best[0]:
            best = (score, c)
            if score[:4] == (0, 0, 0, 0):
                break
    assert best is not None  # offsets is never empty
    return best[1]


# ------------------------------------------------------------------ element glyphs


def place_shunts(grid: GridModel, bus_glyphs: dict[str, BusGlyph]) -> dict[str, ElementGlyph]:
    """Default glyphs for sources, loads, capacitors and PV: one row SHUNT_DY under a
    horizontal bus (right of a vertical one), SHUNT_PITCH apart and centred on the bus,
    in GridModel order (sources first). Glyphs of one bus never overlap, and the row is
    no longer than ``bus_length``. A shunt on a bus without a glyph is left out."""
    per_bus: dict[str, list[str]] = {}
    for name in SHUNT_CLASSES:
        for el in getattr(grid, name).values():
            bus = el.bus1.bus if name == "sources" else el.terminal.bus
            per_bus.setdefault(bus, []).append(el.id)
    out: dict[str, ElementGlyph] = {}
    for bus, ids in per_bus.items():
        g = bus_glyphs.get(bus)
        if g is None:
            continue
        for i, el_id in enumerate(ids):
            along = (i - (len(ids) - 1) / 2) * SHUNT_PITCH
            if g.orientation == 90:
                out[el_id] = ElementGlyph(g.x + SHUNT_DY, g.y + along, 90, 0.0, LABEL_DY)
            else:
                out[el_id] = ElementGlyph(g.x + along, g.y + SHUNT_DY, 0, 0.0, LABEL_DY)
    return out


def branch_buses(grid: GridModel, class_name: str, el: object) -> list[str]:
    if class_name == "transformers":
        return [w.terminal.bus for w in el.windings]  # type: ignore[attr-defined]
    return [el.bus1.bus, el.bus2.bus]  # type: ignore[attr-defined]


def place_branches(grid: GridModel, bus_glyphs: dict[str, BusGlyph]) -> dict[str, ElementGlyph]:
    """Default glyphs for lines, switches and transformers: the symbol anchor at the
    middle of its buses' glyphs, rotated along the branch (0 = mostly horizontal,
    90 = mostly vertical). A branch with no bus glyph at all sits at (0, 0)."""
    out: dict[str, ElementGlyph] = {}
    for name in BRANCH_CLASSES:
        for el in getattr(grid, name).values():
            pts = [(g.x, g.y) for b in branch_buses(grid, name, el) if (g := bus_glyphs.get(b))]
            if not pts:
                out.setdefault(el.id, ElementGlyph(0.0, 0.0, 0, 0.0, LABEL_DY))
                continue
            x = sum(p[0] for p in pts) / len(pts)
            y = sum(p[1] for p in pts) / len(pts)
            dx = max(p[0] for p in pts) - min(p[0] for p in pts)
            dy = max(p[1] for p in pts) - min(p[1] for p in pts)
            rot = 90 if dy > dx else 0
            label = (1.0, 0.0) if rot == 90 else (0.0, LABEL_DY)
            out.setdefault(el.id, ElementGlyph(x, y, rot, *label))
    return out


def build_diagram(grid: GridModel) -> DiagramModel:
    """A complete DiagramModel for ``grid`` (one glyph per bus and drawn element)."""
    graph = GridGraph.build(grid)
    coords = from_bus_coordinates(grid, graph)
    use = grid.buses and len(coords) >= MIN_COORDINATE_SHARE * len(grid.buses)
    pos = auto_layout(grid, coords if use else None, graph)
    shunts = shunt_counts(grid)
    # every graph bus gets a glyph for placing, so an element on a bus that is only
    # named (a draft error validate.py reports) still gets its glyph
    every = {b: BusGlyph(x, y, 0, bus_length(shunts.get(b, 0))) for b, (x, y) in pos.items()}
    buses = {name: every[name] for name in grid.buses}
    elements = place_shunts(grid, every) | place_branches(grid, every)
    ordered = {
        el.id: elements[el.id]
        for name in GLYPH_CLASSES
        for el in getattr(grid, name).values()
        if el.id in elements
    }
    return DiagramModel(buses, ordered)
