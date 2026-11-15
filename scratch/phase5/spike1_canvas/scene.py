"""S1 spike: the drawing data of a feeder, Qt-free (scratch only; nothing in app/, bridge/
or core/ imports this).

``load_ieee123`` loads IEEE 123 through OpenDSSEngine + extract (in-process: fine for a
spike, never in the app, rule 5). ``build_scene`` turns the GridModel into glyphs in
diagram units (1 = one grid step): one bus bar per bus, one branch per line, switch
and transformer winding pair, one shunt glyph per load, capacitor, PV and source.
``replicate`` puts N copies side by side for headroom. ``SpatialIndex`` is the
hit-test (uniform grid of cells), with a linear scan kept for comparison.

The two buses without coordinates (300_open, 94_open) are put next to a neighbour;
real auto-layout is S3.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

from core.grid.model import GridModel

ROOT = Path(__file__).resolve().parents[3]
IEEE123 = ROOT / "tests" / "fixtures" / "grid" / "ieee123"

COORD_PER_UNIT = 25.0  # IEEE 123 coordinates are multiples of 25 ft; shortest branch 100
BUS_HALF = 1.0  # a bus bar is 2 units long, horizontal
SHUNT_DY = 2.0  # shunt glyph centre below its bus
SHUNT_DX = 1.2  # side-by-side spacing of several shunts at one bus
SHUNT_HALF = 0.6  # half size of a shunt glyph box


@dataclass
class BusG:
    name: str
    x: float
    y: float


@dataclass
class BranchG:
    id: str
    kind: str  # line, switch, transformer
    bus1: int  # index into Scene.buses
    bus2: int
    open: bool = False


@dataclass
class ShuntG:
    id: str
    kind: str  # load, capacitor, pvsystem, vsource
    bus: int
    slot: int  # position among the shunts of its bus


@dataclass
class Scene:
    buses: list[BusG] = field(default_factory=list)
    branches: list[BranchG] = field(default_factory=list)
    shunts: list[ShuntG] = field(default_factory=list)

    def shunt_xy(self, s: ShuntG) -> tuple[float, float]:
        b = self.buses[s.bus]
        return b.x + (s.slot - 0.0) * SHUNT_DX, b.y + SHUNT_DY

    def bounds(self) -> tuple[float, float, float, float]:
        xs = [b.x for b in self.buses]
        ys = [b.y for b in self.buses]
        return min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + SHUNT_DY + 2

    def branches_of(self) -> dict[int, list[int]]:
        """Bus index -> indices of the branches that end at it (for moving a bus)."""
        out: dict[int, list[int]] = defaultdict(list)
        for i, br in enumerate(self.branches):
            out[br.bus1].append(i)
            out[br.bus2].append(i)
        return out

    def shunts_of(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for i, s in enumerate(self.shunts):
            out[s.bus].append(i)
        return out


def load_ieee123() -> tuple[GridModel, float]:
    """The extracted, solved IEEE 123 model and the time it took (s)."""
    from core.grid.engines.opendss.engine import OpenDSSEngine

    t0 = time.perf_counter()
    engine = OpenDSSEngine()
    diags = engine.load_native(IEEE123 / "IEEE123Master.dss")
    if diags:
        raise RuntimeError(diags)
    # The master file loads no coordinates (tests/core/grid/helpers.py does the same).
    engine.context.Text.Command = f'BusCoords "{(IEEE123 / "BusCoords.dat").resolve()}"'
    engine.run()
    model, diags = engine.extract_model()
    engine.close()
    if model is None:
        raise RuntimeError(diags)
    return model, time.perf_counter() - t0


def build_scene(model: GridModel) -> Scene:
    names = sorted(model.buses)
    index = {n: i for i, n in enumerate(names)}
    xy: dict[str, tuple[float, float]] = {}
    for n, b in model.buses.items():
        if b.x is not None and b.y is not None:
            # y is up in the file, down on screen; snap to the grid step
            xy[n] = (round(b.x / COORD_PER_UNIT), -round(b.y / COORD_PER_UNIT))

    pairs: list[tuple[str, str, str, str, bool]] = []
    for ln in model.lines.values():
        pairs.append((ln.id, "line", ln.bus1.bus, ln.bus2.bus, not ln.enabled))
    for sw in model.switches.values():
        pairs.append((sw.id, "switch", sw.bus1.bus, sw.bus2.bus, not (sw.closed and sw.enabled)))
    for tr in model.transformers.values():
        first = tr.windings[0].terminal.bus
        for k, w in enumerate(tr.windings[1:], start=2):
            pairs.append((f"{tr.id}#{k}", "transformer", first, w.terminal.bus, False))

    # Buses without coordinates: next to a placed neighbour (S3 does real layout).
    missing = [n for n in names if n not in xy]
    for _ in range(len(missing) + 1):
        for _id, _k, a, b, _o in pairs:
            if a in xy and b not in xy:
                xy[b] = (xy[a][0] + 3, xy[a][1] + 3)
            elif b in xy and a not in xy:
                xy[a] = (xy[b][0] + 3, xy[b][1] + 3)
    for n in names:
        xy.setdefault(n, (0, 0))

    scene = Scene(buses=[BusG(n, *xy[n]) for n in names])
    scene.branches = [
        BranchG(eid, kind, index[a], index[b], op) for eid, kind, a, b, op in pairs if a != b
    ]
    slots: dict[int, int] = defaultdict(int)

    def shunt(eid: str, kind: str, bus: str) -> None:
        i = index[bus]
        scene.shunts.append(ShuntG(eid, kind, i, slots[i]))
        slots[i] += 1

    for s in model.sources.values():
        shunt(s.id, "vsource", s.bus1.bus)
    for ld in model.loads.values():
        shunt(ld.id, "load", ld.terminal.bus)
    for c in model.capacitors.values():
        shunt(c.id, "capacitor", c.terminal.bus)
    for pv in model.pvsystems.values():
        shunt(pv.id, "pvsystem", pv.terminal.bus)
    return scene


def replicate(scene: Scene, copies: int) -> Scene:
    """``copies`` copies of the scene side by side (names get a _cN suffix)."""
    if copies <= 1:
        return scene
    x0, _y0, x1, _y1 = scene.bounds()
    dx = math.ceil(x1 - x0 + 10)
    out = Scene()
    for c in range(copies):
        sfx = "" if c == 0 else f"_c{c + 1}"
        nb = len(out.buses)
        out.buses += [BusG(b.name + sfx, b.x + c * dx, b.y) for b in scene.buses]
        out.branches += [
            replace(br, id=br.id + sfx, bus1=br.bus1 + nb, bus2=br.bus2 + nb)
            for br in scene.branches
        ]
        out.shunts += [replace(s, id=s.id + sfx, bus=s.bus + nb) for s in scene.shunts]
    return out


# ---------------------------------------------------------------- hit-testing

Hit = tuple[str, int]  # ("bus" | "branch" | "shunt", index)


def _seg_dist(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / n))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _boxes(scene: Scene) -> list[tuple[Hit, tuple[float, float, float, float]]]:
    out: list[tuple[Hit, tuple[float, float, float, float]]] = []
    for i, s in enumerate(scene.shunts):
        x, y = scene.shunt_xy(s)
        out.append((("shunt", i), (x - SHUNT_HALF, y - SHUNT_HALF, x + SHUNT_HALF, y + SHUNT_HALF)))
    for i, b in enumerate(scene.buses):
        out.append((("bus", i), (b.x - BUS_HALF, b.y - 0.25, b.x + BUS_HALF, b.y + 0.25)))
    return out


_RANK = {"shunt": 0, "bus": 1, "branch": 2}


def _box_dist(x: float, y: float, box: tuple[float, float, float, float]) -> float:
    a, b, c, d = box
    return math.hypot(max(a - x, 0.0, x - c), max(b - y, 0.0, y - d))


def hit_linear(scene: Scene, x: float, y: float, tol: float) -> Hit | None:
    """The nearest glyph within ``tol`` (box distance, 0 inside; segment distance for
    branches); on equal distance shunts beat buses beat branches, then the lower index."""
    best: tuple[float, int, int] | None = None
    for (kind, i), box in _boxes(scene):
        dist = _box_dist(x, y, box)
        if dist <= tol and (best is None or (dist, _RANK[kind], i) < best):
            best = (dist, _RANK[kind], i)
    for i, br in enumerate(scene.branches):
        p, q = scene.buses[br.bus1], scene.buses[br.bus2]
        dist = _seg_dist(x, y, p.x, p.y, q.x, q.y)
        if dist <= tol and (best is None or (dist, 2, i) < best):
            best = (dist, 2, i)
    if best is None:
        return None
    return ({0: "shunt", 1: "bus", 2: "branch"}[best[1]], best[2])


class SpatialIndex:
    """Uniform grid of ``cell``-unit squares; each glyph is listed in every cell its box
    touches. Rebuilt in full (``rebuild``) or per moved bus (``move_bus``)."""

    def __init__(self, scene: Scene, cell: float = 4.0) -> None:
        self.scene = scene
        self.cell = cell
        self.rebuild()

    def _cells(self, a: float, b: float, c: float, d: float) -> list[tuple[int, int]]:
        s = self.cell
        return [
            (i, j)
            for i in range(math.floor(a / s), math.floor(c / s) + 1)
            for j in range(math.floor(b / s), math.floor(d / s) + 1)
        ]

    def _box_of(self, hit: Hit) -> tuple[float, float, float, float]:
        kind, i = hit
        sc = self.scene
        if kind == "shunt":
            x, y = sc.shunt_xy(sc.shunts[i])
            return x - SHUNT_HALF, y - SHUNT_HALF, x + SHUNT_HALF, y + SHUNT_HALF
        if kind == "bus":
            b = sc.buses[i]
            return b.x - BUS_HALF, b.y - 0.25, b.x + BUS_HALF, b.y + 0.25
        br = sc.branches[i]
        p, q = sc.buses[br.bus1], sc.buses[br.bus2]
        return min(p.x, q.x), min(p.y, q.y), max(p.x, q.x), max(p.y, q.y)

    def _insert(self, hit: Hit) -> None:
        cells = self._cells(*self._box_of(hit))
        self._where[hit] = cells
        for c in cells:
            self._grid[c].append(hit)

    def _remove(self, hit: Hit) -> None:
        for c in self._where.pop(hit, []):
            self._grid[c].remove(hit)

    def rebuild(self) -> None:
        self._grid: dict[tuple[int, int], list[Hit]] = defaultdict(list)
        self._where: dict[Hit, list[tuple[int, int]]] = {}
        sc = self.scene
        for i in range(len(sc.shunts)):
            self._insert(("shunt", i))
        for i in range(len(sc.buses)):
            self._insert(("bus", i))
        for i in range(len(sc.branches)):
            self._insert(("branch", i))

    def move_bus(self, bus: int, branches: list[int], shunts: list[int]) -> None:
        for hit in (
            [("bus", bus)] + [("branch", i) for i in branches] + [("shunt", i) for i in shunts]
        ):
            self._remove(hit)
            self._insert(hit)

    def hit(self, x: float, y: float, tol: float) -> Hit | None:
        """Same answer as ``hit_linear``, looking only at the cells around (x, y)."""
        found: set[Hit] = set()
        for c in self._cells(x - tol, y - tol, x + tol, y + tol):
            found.update(self._grid.get(c, ()))
        best: tuple[float, int, int] | None = None
        sc = self.scene
        for kind, i in found:
            if kind == "branch":
                br = sc.branches[i]
                p, q = sc.buses[br.bus1], sc.buses[br.bus2]
                dist = _seg_dist(x, y, p.x, p.y, q.x, q.y)
            else:
                dist = _box_dist(x, y, self._box_of((kind, i)))
            key = (dist, _RANK[kind], i)
            if dist <= tol and (best is None or key < best):
                best = key
        if best is None:
            return None
        return ({0: "shunt", 1: "bus", 2: "branch"}[best[1]], best[2])

    def in_rect(self, x1: float, y1: float, x2: float, y2: float) -> list[Hit]:
        """Every glyph whose box is wholly inside the rectangle."""
        found: set[Hit] = set()
        for c in self._cells(x1, y1, x2, y2):
            found.update(self._grid.get(c, ()))
        out = []
        for hit in found:
            a, b, c, d = self._box_of(hit)
            if x1 <= a and c <= x2 and y1 <= b and d <= y2:
                out.append(hit)
        return sorted(out)
