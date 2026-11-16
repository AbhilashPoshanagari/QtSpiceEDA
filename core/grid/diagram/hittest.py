"""What is drawn where on the designer canvas, and what a click or a rubber band hits.
Qt-free, presentation only (rule 20). Decisions from spike S1
(scratch/phase5/spike1_canvas/README.md):

- every line drawn comes from GridModel terminals at render time (rule 20): a branch
  from bus bar to bus bar (a transformer from winding 1 to each other winding), a
  shunt's stub from its glyph to its bus bar; a terminal on a bus without a glyph
  draws nothing (``element_segments``);
- hit-testing: the nearest shape within the tolerance wins (6 px / zoom in the
  canvas); the kind only breaks ties (symbol before bus bar before branch or stub),
  never a fixed kind priority (a load would otherwise win over its own bus at fit
  zoom);
- a uniform-cell spatial index (``HitIndex``), so a click or hover costs microseconds
  on IEEE 123 and its copies;
- rubber band: an id is selected when all of its band shapes lie inside the rectangle
  (a shunt's stub does not count: it ends on the bus bar).

Coordinates are diagram units (1 = one grid step), y down, as in DiagramModel. Reading
terminals is not walking the grid graph (rule 25): nothing here follows a branch.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from core.grid.design_io import GridDesign
from core.grid.diagram.layout import BUS_HALF_H, SHUNT_HALF
from core.grid.diagram.model import BusGlyph, ElementGlyph

Point = tuple[float, float]
Segment = tuple[Point, Point]

SYMBOL = "symbol"
BUS = "bus"
EDGE = "edge"
_RANK = {SYMBOL: 0, BUS: 1, EDGE: 2}
"""Tie-break only: at equal distance a symbol wins over a bus bar over a line."""

SYMBOL_HALF = SHUNT_HALF
"""Half size of an element symbol (shunts, switch and transformer symbols)."""
SYMBOL_KINDS = ("sources", "loads", "capacitors", "pvsystems", "switches", "transformers")
"""Element classes drawn with a symbol at their glyph (a line has only its label)."""
CELL = 4.0


def on_bar(g: BusGlyph, toward_x: float, toward_y: float) -> Point:
    """The point of a bus bar nearest to (toward_x, toward_y): the bar is ``length``
    long, centred on the glyph position, horizontal (0) or vertical (90)."""
    half = float(g.length) / 2.0
    if g.orientation == 90:
        return float(g.x), min(max(float(toward_y), g.y - half), g.y + half)
    return min(max(float(toward_x), g.x - half), g.x + half), float(g.y)


def bar_segment(g: BusGlyph) -> Segment:
    half = float(g.length) / 2.0
    if g.orientation == 90:
        return (float(g.x), g.y - half), (float(g.x), g.y + half)
    return (g.x - half, float(g.y)), (g.x + half, float(g.y))


def element_segments(
    attr: str, el: object, glyph: ElementGlyph, bus_glyphs: dict[str, BusGlyph]
) -> list[Segment]:
    """The segments drawn for one element of GridModel class dict ``attr``, from its
    terminals (rule 20)."""
    out: list[Segment] = []
    if attr in ("sources", "loads", "capacitors", "pvsystems"):
        term = el.bus1 if attr == "sources" else el.terminal  # type: ignore[attr-defined]
        bg = bus_glyphs.get(term.bus)
        if bg is not None:
            out.append(((float(glyph.x), float(glyph.y)), on_bar(bg, glyph.x, glyph.y)))
        return out
    if attr == "transformers":
        ends = [w.terminal.bus for w in el.windings]  # type: ignore[attr-defined]
        pairs = [(ends[0], e) for e in ends[1:]] if ends else []
    else:
        pairs = [(el.bus1.bus, el.bus2.bus)]  # type: ignore[attr-defined]
    for a, b in pairs:
        ga, gb = bus_glyphs.get(a), bus_glyphs.get(b)
        if ga is None or gb is None:
            continue
        out.append((on_bar(ga, gb.x, gb.y), on_bar(gb, ga.x, ga.y)))
    return out


@dataclass(frozen=True)
class Shape:
    """One pickable shape: a segment (a bus bar, a branch, a stub) with a half
    thickness, or a square symbol (a zero-length segment with ``half`` = half size)."""

    id: str
    kind: str  # SYMBOL, BUS or EDGE
    a: Point
    b: Point
    half: float = 0.0
    band: bool = True
    """Counts for the rubber band (a stub does not)."""

    def box(self) -> tuple[float, float, float, float]:
        h = self.half
        return (min(self.a[0], self.b[0]) - h, min(self.a[1], self.b[1]) - h,
                max(self.a[0], self.b[0]) + h, max(self.a[1], self.b[1]) + h)  # fmt: skip

    def distance(self, x: float, y: float) -> float:
        """Distance from (x, y) to the shape (0 inside it)."""
        if self.kind == SYMBOL:
            x0, y0, x1, y1 = self.box()
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            return math.hypot(dx, dy)
        (ax, ay), (bx, by) = self.a, self.b
        vx, vy = bx - ax, by - ay
        n = vx * vx + vy * vy
        t = 0.0 if n == 0 else max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / n))
        return max(0.0, math.hypot(x - (ax + t * vx), y - (ay + t * vy)) - self.half)


def design_shapes(design: GridDesign) -> list[Shape]:
    """Every pickable shape of a design: bus bars, element symbols (a line's is its
    label anchor, which is not pickable), branches and stubs."""
    grid, diagram = design.grid, design.diagram
    out: list[Shape] = []
    for name, g in diagram.bus_glyphs.items():
        a, b = bar_segment(g)
        out.append(Shape(f"bus.{name}", BUS, a, b, BUS_HALF_H))
    for attr in ("sources", "lines", "switches", "transformers", "loads", "capacitors",
                 "pvsystems"):  # fmt: skip
        for el in getattr(grid, attr).values():
            if attr == "lines" and el.name in grid.switches:
                continue  # a shared id; check_consistency reports it
            g = diagram.element_glyphs.get(el.id)
            if g is None:
                continue
            branch = attr in ("lines", "switches", "transformers")
            if attr in SYMBOL_KINDS:
                p = (float(g.x), float(g.y))
                out.append(Shape(el.id, SYMBOL, p, p, SYMBOL_HALF))
            for seg in element_segments(attr, el, g, diagram.bus_glyphs):
                out.append(Shape(el.id, EDGE, seg[0], seg[1], 0.0, band=branch))
    return out


class HitIndex:
    """Uniform grid of ``cell``-unit squares; each shape is listed in every cell its
    box touches."""

    def __init__(self, shapes: Iterable[Shape], cell: float = CELL) -> None:
        self.cell = float(cell)
        self.shapes = list(shapes)
        self._grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, s in enumerate(self.shapes):
            for c in self._cells(*s.box()):
                self._grid[c].append(i)

    def _cells(self, x0: float, y0: float, x1: float, y1: float) -> list[tuple[int, int]]:
        s = self.cell
        return [
            (i, j)
            for i in range(math.floor(x0 / s), math.floor(x1 / s) + 1)
            for j in range(math.floor(y0 / s), math.floor(y1 / s) + 1)
        ]

    def hit(self, x: float, y: float, tolerance: float) -> str | None:
        """The id of the nearest shape within ``tolerance`` of (x, y); at equal
        distance the kind decides (symbol, bus, edge), then the drawing order."""
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        tol = max(0.0, float(tolerance)) if math.isfinite(tolerance) else 0.0
        seen: set[int] = set()
        best: tuple[float, int, int] | None = None
        for c in self._cells(x - tol, y - tol, x + tol, y + tol):
            for i in self._grid.get(c, ()):
                if i in seen:
                    continue
                seen.add(i)
                s = self.shapes[i]
                d = s.distance(x, y)
                if d > tol:
                    continue
                key = (d, _RANK[s.kind], i)
                if best is None or key < best:
                    best = key
        return None if best is None else self.shapes[best[2]].id

    def hit_linear(self, x: float, y: float, tolerance: float) -> str | None:
        """The same answer as ``hit`` by looking at every shape (for tests)."""
        best: tuple[float, int, int] | None = None
        for i, s in enumerate(self.shapes):
            d = s.distance(x, y)
            if d <= tolerance and (best is None or (d, _RANK[s.kind], i) < best):
                best = (d, _RANK[s.kind], i)
        return None if best is None else self.shapes[best[2]].id

    def in_rect(self, x0: float, y0: float, x1: float, y1: float) -> list[str]:
        """Ids whose band shapes all lie inside the rectangle, in drawing order."""
        lx, hx = min(x0, x1), max(x0, x1)
        ly, hy = min(y0, y1), max(y0, y1)
        inside: dict[str, bool] = {}
        for s in self.shapes:
            if not s.band:
                continue
            bx0, by0, bx1, by1 = s.box()
            ok = lx <= bx0 and bx1 <= hx and ly <= by0 and by1 <= hy
            inside[s.id] = inside.get(s.id, True) and ok
        return [i for i, ok in inside.items() if ok]

    def bounds(self) -> tuple[float, float, float, float] | None:
        """The box around every shape; None for an empty design."""
        if not self.shapes:
            return None
        boxes = [s.box() for s in self.shapes]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))  # fmt: skip
