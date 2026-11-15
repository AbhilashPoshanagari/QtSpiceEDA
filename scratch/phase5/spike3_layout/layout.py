"""S3 spike: auto-layout for buses without coordinates (scratch only; nothing in app/,
bridge/ or core/ imports this). Qt-free, no new dependency: dicts, deque and math.

Input is a ``Graph`` (buses, branches, number of shunt glyphs per bus, source bus).
In the product this graph comes from core/grid/topology.py (rule 25); here
``feeders.build_graph`` makes it from a GridModel.

Two entry points:

- ``tree_layout(graph, variant)``: every bus placed. Breadth-first from the source over
  closed branches (an open switch is a non-tree edge); buses reached only through open
  branches hang off the bus that reaches them; isolated parts are stacked below. The
  depth gives the column (a transformer adds an extra column), the subtree leaf order
  gives the row. Variants:
    "leaf-trunk"  one row per leaf, a bus on the row of its first child (deepest child
                  first, so the longest path is one straight row);
    "leaf-mid"    one row per leaf, a bus midway between its first and last child;
    "packed"      like leaf-trunk, but a subtree moves up into rows that are free in
                  the columns it uses (per-column contours, Reingold-Tilford style);
    "packed-mid"  packed, with a bus midway between its first and last child.
- ``place_missing(graph, fixed)``: keep the given coordinates and put each missing bus
  next to its nearest placed neighbour, at the free grid point that adds the fewest
  overlaps and crossings. Buses that share a position with another bus are moved too
  (IEEE 123: 150r on 150, 9r on 9, ...), except the one nearest the source.

Positions are diagram units (1 = one grid step), y down, all on whole units.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

# Glyph geometry (diagram units). A bus bar is horizontal; its shunts (loads,
# capacitors, PV, source) sit in one row under it, centred, SHUNT_PITCH apart.
BUS_MIN = 2.0
BUS_HALF_H = 0.25
SHUNT_PITCH = 1.2
SHUNT_HALF = 0.5
SHUNT_DY = 2.0
ROW = 4  # row pitch of the tree layout
COL_GAP = 3  # free space between the bus bars of two neighbouring columns
TARGET_LEN = 6.0  # wanted branch length when scaling file coordinates / placing a bus
CLEARANCE = 1.0  # free space a placed bus keeps around its glyph
MIN_LEN = 3.0  # shortest branch a placed bus may get

VARIANTS = ("leaf-trunk", "leaf-mid", "packed", "packed-mid")


@dataclass(frozen=True)
class Edge:
    id: str  # element id, e.g. "line.650632", "transformer.sub"
    kind: str  # line | switch | transformer
    a: str
    b: str
    closed: bool = True

    @property
    def span(self) -> int:
        """Columns between the two ends in the tree layout."""
        return 2 if self.kind == "transformer" else 1


@dataclass
class Graph:
    buses: list[str]
    edges: list[Edge]
    source: str
    shunts: dict[str, int] = field(default_factory=dict)  # bus -> number of shunt glyphs

    def bus_length(self, bus: str) -> float:
        return max(BUS_MIN, self.shunts.get(bus, 0) * SHUNT_PITCH)

    def adjacency(self, closed_only: bool) -> dict[str, list[tuple[str, Edge]]]:
        """Bus -> [(neighbour, edge)], one entry per neighbour (parallel branches such
        as the three single-phase regulators 650-rg60 are one connection), sorted."""
        adj: dict[str, dict[str, Edge]] = {b: {} for b in self.buses}
        for e in self.edges:
            if e.a == e.b or (closed_only and not e.closed):
                continue
            for p, q in ((e.a, e.b), (e.b, e.a)):
                old = adj[p].get(q)
                # prefer a closed edge, then a transformer (it decides the column gap)
                if old is None or (e.closed, e.kind == "transformer") > (
                    old.closed,
                    old.kind == "transformer",
                ):
                    adj[p][q] = e
        return {b: sorted(n.items()) for b, n in adj.items()}


Pos = dict[str, tuple[float, float]]


# ------------------------------------------------------------------ tree layout


@dataclass
class _Tree:
    roots: list[str]
    children: dict[str, list[str]]
    edge: dict[str, Edge]  # child -> the edge from its parent
    col: dict[str, int]


def _spanning_forest(graph: Graph) -> _Tree:
    closed = graph.adjacency(closed_only=True)
    every = graph.adjacency(closed_only=False)
    parent: dict[str, str | None] = {}
    edge: dict[str, Edge] = {}
    order: list[str] = []

    def bfs(starts: list[str], adj: dict[str, list[tuple[str, Edge]]]) -> None:
        queue = deque(starts)
        while queue:
            u = queue.popleft()
            for v, e in adj[u]:
                if v not in parent:
                    parent[v], edge[v] = u, e
                    order.append(v)
                    queue.append(v)

    roots = [graph.source]
    parent[graph.source] = None
    order.append(graph.source)
    bfs([graph.source], closed)
    # Buses behind open branches: continue from everything placed so far, over any
    # branch, then over closed branches again from what that reached.
    while True:
        before = len(order)
        bfs(list(order), every)
        bfs(order[before:], closed)
        if len(order) == before:
            break
    for b in graph.buses:  # parts with no branch to the source at all
        if b not in parent:
            parent[b] = None
            roots.append(b)
            order.append(b)
            bfs([b], every)

    children: dict[str, list[str]] = {b: [] for b in graph.buses}
    for b in order:
        p = parent[b]
        if p is not None:
            children[p].append(b)
    col: dict[str, int] = {}
    for b in order:
        p = parent[b]
        col[b] = 0 if p is None else col[p] + edge[b].span

    height: dict[str, int] = {}
    size: dict[str, int] = {}
    for b in reversed(order):
        kids = children[b]
        height[b] = 1 + max((height[k] for k in kids), default=0)
        size[b] = 1 + sum(size[k] for k in kids)
    for b in graph.buses:  # deepest subtree first: the longest path is one row
        children[b].sort(key=lambda k: (-height[k], -size[k], k))
    return _Tree(roots, children, edge, col)


def _rows_leaf(tree: _Tree, mid: bool) -> dict[str, float]:
    rows: dict[str, float] = {}
    next_row = 0

    def visit(b: str) -> None:
        nonlocal next_row
        kids = tree.children[b]
        if not kids:
            rows[b] = next_row
            next_row += 1
            return
        for i, k in enumerate(kids):
            if i > 0 and tree.edge[k].span == 2:
                # a transformer edge skips a column: keep it off the earlier siblings
                # that stand in that column
                between = [rows[s] for s in kids[:i] if tree.col[s] == tree.col[b] + 1]
                y0 = rows[kids[0]]  # the parent's row in leaf-trunk; about it in leaf-mid
                while any(abs(r - (y0 + next_row) / 2) < 0.75 for r in between):
                    next_row += 1
            visit(k)
        first, last = rows[kids[0]], rows[kids[-1]]
        rows[b] = (first + last) / 2 if mid else first

    for r in tree.roots:
        visit(r)
        next_row += 1  # an empty row between separate parts
    return rows


def _rows_packed(tree: _Tree, mid: bool) -> dict[str, float]:
    rel: dict[str, float] = {}  # row relative to the parent

    def contour(b: str) -> dict[int, tuple[float, float]]:
        """Placed subtree of b: column -> (top, bottom) row, relative to b."""
        acc: dict[int, tuple[float, float]] = {tree.col[b]: (0, 0)}
        offsets: list[tuple[str, float]] = []
        for i, k in enumerate(tree.children[b]):
            ck = contour(k)
            if tree.edge[k].span == 2:  # the skipped column, at the child's row
                ck[tree.col[b] + 1] = (0, 0)
            if i == 0:
                off = 0
            else:
                off = offsets[-1][1] + 1
                for c, (top, _bot) in ck.items():
                    if c in acc:
                        off = max(off, acc[c][1] - top + 1)
                if tree.edge[k].span == 2:
                    between = [o for s, o in offsets if tree.col[s] == tree.col[b] + 1]
                    while any(abs(r - off / 2) < 0.75 for r in between):
                        off += 1
            offsets.append((k, off))
            rel[k] = off
            for c, (top, bot) in ck.items():
                t, u = top + off, bot + off
                acc[c] = (min(acc[c][0], t), max(acc[c][1], u)) if c in acc else (t, u)
        if mid and offsets:  # move b (alone in its column) to the middle of its children
            m = (offsets[0][1] + offsets[-1][1]) / 2
            for k, _off in offsets:
                rel[k] -= m
            acc = {c: (t - m, u - m) for c, (t, u) in acc.items()}
            acc[tree.col[b]] = (0, 0)
        return acc

    rows: dict[str, float] = {}
    base = 0
    for r in tree.roots:
        cont = contour(r)
        top = min(t for t, _ in cont.values())
        rows[r] = base - top
        stack = [r]
        while stack:
            u = stack.pop()
            for k in tree.children[u]:
                rows[k] = rows[u] + rel[k]
                stack.append(k)
        base = math.ceil(max(rows.values())) + 2
    return rows


def _columns_x(graph: Graph, col: dict[str, int]) -> dict[int, float]:
    width: dict[int, float] = {}
    for b, c in col.items():
        width[c] = max(width.get(c, BUS_MIN), graph.bus_length(b))
    xs: dict[int, float] = {0: 0.0}
    for c in range(1, max(col.values(), default=0) + 1):
        prev = width.get(c - 1, BUS_MIN)
        cur = width.get(c, BUS_MIN)
        xs[c] = xs[c - 1] + math.ceil(prev / 2 + COL_GAP + cur / 2)
    return xs


def tree_layout(graph: Graph, variant: str = "packed") -> Pos:
    if variant not in VARIANTS:
        raise ValueError(variant)
    tree = _spanning_forest(graph)
    if variant.startswith("packed"):
        rows = _rows_packed(tree, mid=variant == "packed-mid")
    else:
        rows = _rows_leaf(tree, mid=variant == "leaf-mid")
    xs = _columns_x(graph, tree.col)
    return {b: (xs[tree.col[b]], rows[b] * ROW) for b in graph.buses}


# ------------------------------------------------------------- scaled coordinates


def scale_coordinates(graph: Graph, coords: dict[str, tuple[float, float]]) -> Pos:
    """File coordinates (y up) -> diagram units (y down) on whole grid steps, scaled so
    the median branch is TARGET_LEN long."""
    lengths = sorted(
        math.dist(coords[e.a], coords[e.b])
        for e in graph.edges
        if e.a in coords and e.b in coords and coords[e.a] != coords[e.b]
    )
    s = TARGET_LEN / lengths[len(lengths) // 2] if lengths else 1.0
    return {b: (round(x * s), round(-y * s)) for b, (x, y) in coords.items()}


# ----------------------------------------------------------- mixed: place missing


def _glyph_box(graph: Graph, bus: str, x: float, y: float) -> tuple[float, float, float, float]:
    """Bounding box of a bus bar plus its shunt row."""
    half = graph.bus_length(bus) / 2
    n = graph.shunts.get(bus, 0)
    bottom = y + (SHUNT_DY + SHUNT_HALF if n else BUS_HALF_H)
    return x - half, y - BUS_HALF_H, x + half, bottom


def _boxes_overlap(p: tuple[float, ...], q: tuple[float, ...], margin: float) -> bool:
    return (
        p[0] - margin < q[2]
        and q[0] - margin < p[2]
        and p[1] - margin < q[3]
        and q[1] - margin < p[3]
    )


def seg_cross(
    p1: tuple[float, float],
    p2: tuple[float, float],
    q1: tuple[float, float],
    q2: tuple[float, float],
) -> bool:
    """Proper intersection or collinear overlap of two segments (shared endpoints are
    the caller's business)."""

    def orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
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


def seg_hits_box(p: tuple[float, float], q: tuple[float, float], box: tuple[float, ...]) -> bool:
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


def place_missing(
    graph: Graph, fixed: Pos, radius: int = 12, step: int = 1
) -> tuple[Pos, list[str]]:
    """Keep ``fixed`` positions; place every other bus. Returns (positions, the placed
    buses in placing order, coincident ones included)."""
    every = graph.adjacency(closed_only=False)

    # Hop distance from the source decides which of two coincident buses stays.
    depth = {graph.source: 0}
    queue = deque([graph.source])
    while queue:
        u = queue.popleft()
        for v, _e in every[u]:
            if v not in depth:
                depth[v] = depth[u] + 1
                queue.append(v)
    pos: Pos = {}
    groups: dict[tuple[float, float], list[str]] = {}
    for b in sorted(fixed):
        groups.setdefault(fixed[b], []).append(b)
    for p, group in groups.items():
        keep = min(group, key=lambda b: (depth.get(b, 1 << 30), b))
        pos[keep] = p

    # Missing buses in breadth-first order from the placed ones: each one has a placed
    # neighbour when its turn comes.
    parent: dict[str, str] = {}
    order: list[str] = []
    queue = deque(sorted(pos))
    seen = set(pos)
    while queue:
        u = queue.popleft()
        for v, _e in every[u]:
            if v not in seen:
                seen.add(v)
                parent[v] = u
                order.append(v)
                queue.append(v)

    def segments() -> list[tuple[str, str]]:
        out = set()
        for e in graph.edges:
            if e.a != e.b and e.a in pos and e.b in pos:
                out.add(tuple(sorted((e.a, e.b))))
        return sorted(out)

    offsets = sorted(
        (
            (dx, dy)
            for dx in range(-radius, radius + 1, step)
            for dy in range(-radius, radius + 1, step)
        ),
        key=lambda d: (d[0] ** 2 + d[1] ** 2, d),
    )
    offsets = [d for d in offsets if d != (0, 0)]
    for b in order:
        nb = parent[b]
        nx, ny = pos[nb]
        # wanted direction: away from the neighbour's other placed neighbours
        others = [pos[v] for v, _ in every[nb] if v in pos and v != b]
        if others:
            cx = sum(p[0] for p in others) / len(others)
            cy = sum(p[1] for p in others) / len(others)
            dx, dy = nx - cx, ny - cy
            n = math.hypot(dx, dy) or 1.0
            want = (nx + dx / n * TARGET_LEN, ny + dy / n * TARGET_LEN)
        else:
            want = (nx + TARGET_LEN, ny)
        links = [v for v, _ in every[b] if v in pos]
        segs = segments()
        boxes = {v: _glyph_box(graph, v, *pos[v]) for v in pos}
        best: tuple | None = None
        # nearest to the wanted point first: the first one without any penalty wins
        cands = sorted(
            offsets, key=lambda d: (round(math.dist((nx + d[0], ny + d[1]), want), 6), d)
        )
        for ox, oy in cands:
            c = (nx + ox, ny + oy)
            box = _glyph_box(graph, b, *c)
            overlaps = sum(1 for v, bx in boxes.items() if _boxes_overlap(box, bx, CLEARANCE))
            short = sum(1 for v in links if math.dist(c, pos[v]) < MIN_LEN)
            crossings = 0
            through = 0
            for v in links:
                for s, t in segs:
                    if v in (s, t):
                        continue
                    if seg_cross(c, pos[v], pos[s], pos[t]):
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
        assert best is not None
        pos[b] = best[1]

    for b in graph.buses:  # a part with no branch to any placed bus: below everything
        if b not in pos:
            sub = Graph(
                [x for x in graph.buses if x not in pos],
                [e for e in graph.edges if e.a not in pos and e.b not in pos],
                b,
                graph.shunts,
            )
            lay = tree_layout(sub)
            bottom = max((p[1] for p in pos.values()), default=0) + 2 * ROW
            left = min((p[0] for p in pos.values()), default=0)
            for v, (x, y) in lay.items():
                pos[v] = (left + x, bottom + y)
                order.append(v)
    return pos, order
