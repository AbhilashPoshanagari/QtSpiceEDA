"""The electrical graph of a GridModel. Qt-free, engine-free (rule 25: the only place that
walks the grid graph; validate.py, commands.py, layout.py, analysis.py and the bridge
ask it).

``GridGraph.build(model)``: nodes are buses, edges are the enabled branches:

- a line or switch is one edge between its two terminals (a switch carries its open
  state in ``Edge.closed``);
- a transformer is one edge per winding pair (winding 1, winding k), so a 3-winding
  unit is a star from winding 1, never a triangle.

Disabled elements are not part of the graph. Shunts (sources, loads, capacitors, PV)
are not edges; the graph remembers which buses have one.

What it answers:

- ``islands()``: connected parts over closed branches;
- ``energized_buses()``: buses reached from an enabled Source over closed branches
  (``include_open=True``: also through open switches);
- ``voltage_zones()``: {bus: kV LL}. The source and every transformer winding fix the
  kV at their bus; lines and switches (open or closed) carry it on, never crossing a
  transformer. Every bus takes the kV of the nearest such anchor (sources first), so a
  line that joins two zones shows up as a line with a different kV at each end. A
  1-phase wye winding to neutral gives kV * sqrt(3), and every value is snapped to the
  derived voltage base within 1 % (so a 2.4 kV regulator winding sits in the 4.16 kV
  zone, as OpenDSS's CalcVoltageBases puts it);
- ``available_nodes(bus)``: the phase nodes present at a bus, propagated from the
  source: through lines and switches (open or closed) conductor by conductor (node k of
  bus1 to node k of bus2), and through a transformer from any winding whose phase nodes
  are all available to every node of its other windings;
- ``loops()``: cycles over closed branches. Parallel branches between the same two
  buses form a loop only when they share a node at both ends (the three 1-phase
  regulators 650-rg60 of IEEE 13 do not);
- ``path(bus_a, bus_b)``: the shortest chain of edges, or None;
- for the diagram layout (D3): ``connections``, ``bfs_tree``, ``spanning_forest`` and
  ``hop_distance``, all deterministic (neighbours in name order).

A terminal whose bus is ``DISCONNECTED`` ("", set by the designer's Disconnect command)
is not part of the graph: a branch with such an end is no edge, a shunt on it is on no
bus. ``connected_elements(model, bus)`` lists every element with a terminal at a bus
(the designer's Remove bus and Rename, D4).

``derived_voltage_bases(model)``: the voltage bases of a design (CLAUDE.md "Grid model
changes for designs", S4 rule "ll_3ph_first").
"""

from __future__ import annotations

import heapq
import math
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from core.grid.model import DELTA, GridModel, Terminal, Transformer, Winding

LINE = "line"
SWITCH = "switch"
TRANSFORMER = "transformer"

DISCONNECTED = ""
"""The bus of a terminal that is connected to nothing (a draft; validate.py reports it)."""

BASE_MATCH = 0.01
"""A 1-phase winding kV within 1 % of an existing base belongs to that base."""

_SQRT3 = math.sqrt(3.0)


# ---- node helpers (shared with validate.py)


def terminal_nodes(term: Terminal, phases: int) -> tuple[int, ...]:
    """The nodes as OpenDSS uses them: the explicit ones, else 1..phases."""
    return term.nodes if term.nodes else tuple(range(1, phases + 1))


def required_nodes(term: Terminal, phases: int, conn: str) -> frozenset[int]:
    """Phase nodes an element needs at its bus: every node except ground (0) and, for a
    wye (or line) terminal that names one node more than it has phases, that last
    node (the neutral, which OpenDSS creates at the bus)."""
    nodes = terminal_nodes(term, phases)
    if conn != DELTA and len(nodes) == phases + 1:
        nodes = nodes[:-1]
    return frozenset(n for n in nodes if n != 0)


def winding_kv_ll(t: Transformer, w: Winding) -> float:
    """A winding's rated kV as line-to-line: as given for 3-phase, delta and 1-phase
    windings between two phases; kV * sqrt(3) for a 1-phase wye winding to neutral."""
    phase_nodes = [n for n in w.terminal.nodes if n != 0]
    if t.phases > 1 or w.conn == DELTA or len(phase_nodes) >= 2:
        return w.kv
    return w.kv * _SQRT3


def connected_elements(model: GridModel, bus: str) -> list[str]:
    """Ids of every element with a terminal at ``bus``, enabled or not, each once, in
    model order: sources (bus1 or bus2), lines, switches, transformers (any winding),
    loads, capacitors, PV systems."""
    out: list[str] = []

    def at(element_id: str, *terms: Terminal) -> None:
        if any(t.bus == bus for t in terms) and element_id not in out:
            out.append(element_id)

    if bus == DISCONNECTED:
        return out
    for s in model.sources.values():
        at(s.id, s.bus1, s.bus2)
    for br in (*model.lines.values(), *model.switches.values()):
        at(br.id, br.bus1, br.bus2)
    for t in model.transformers.values():
        at(t.id, *(w.terminal for w in t.windings))
    for group in (model.loads, model.capacitors, model.pvsystems):
        for el in group.values():
            at(el.id, el.terminal)
    return out


def _usable_kv(kv: object) -> bool:
    return isinstance(kv, (int, float)) and math.isfinite(kv) and kv > 0


def derived_voltage_bases(model: GridModel) -> list[float]:
    """Sorted unique kV LL: every enabled source and 3-phase transformer winding, then a
    1-phase winding (LL as in ``winding_kv_ll``) only if no base is within 1 % of it.
    Missing or non-positive kV values (draft elements) are left out."""
    bases: list[float] = []

    def add(kv: float) -> None:
        if kv not in bases:
            bases.append(kv)

    for s in model.sources.values():
        if s.enabled and _usable_kv(s.kv):
            add(float(s.kv))
    single: list[float] = []
    for t in model.transformers.values():
        if not t.enabled:
            continue
        for w in t.windings:
            if not _usable_kv(w.kv):
                continue
            kv = float(winding_kv_ll(t, w))
            if t.phases >= 3:
                add(kv)
            else:
                single.append(kv)
    for kv in single:
        if not any(abs(b - kv) <= BASE_MATCH * b for b in bases):
            add(kv)
    return sorted(bases)


def snap_kv(kv: float, bases: Iterable[float]) -> float:
    """The base nearest to ``kv`` if one is within 1 %, else ``kv`` itself."""
    near = [b for b in bases if abs(b - kv) <= BASE_MATCH * b]
    return min(near, key=lambda b: abs(b - kv)) if near else kv


# ---- the graph


@dataclass(frozen=True)
class Edge:
    """One branch between two buses. For a transformer, one winding pair."""

    element_id: str
    kind: str
    """LINE, SWITCH or TRANSFORMER."""
    bus_a: str
    bus_b: str
    nodes_a: tuple[int, ...]
    nodes_b: tuple[int, ...]
    closed: bool
    """False only for an open switch."""
    winding_a: int = 0
    winding_b: int = 0
    """1-based winding numbers of a transformer edge; 0 for lines and switches."""

    def other(self, bus: str) -> str:
        return self.bus_b if bus == self.bus_a else self.bus_a


@dataclass(frozen=True)
class Loop:
    """A cycle over closed branches. ``element_ids`` follow the loop; the last one is
    the branch that closed it."""

    element_ids: tuple[str, ...]
    buses: tuple[str, ...]


@dataclass(frozen=True)
class SpanningForest:
    """``GridGraph.spanning_forest()``: one tree per root, buses in breadth-first
    ``order`` (a parent always comes before its children)."""

    roots: list[str]
    order: list[str]
    parent: dict[str, str | None]
    edge: dict[str, Edge]
    """Child bus -> the edge from its parent."""


@dataclass(frozen=True)
class _TransformerNodes:
    element_id: str
    windings: tuple[tuple[str, frozenset[int], frozenset[int]], ...]
    """(bus, required phase nodes, every non-ground node) per winding."""


@dataclass
class GridGraph:
    buses: list[str]
    """Model buses in model order, then buses only named by an element."""
    edges: list[Edge]
    source_buses: list[tuple[str, frozenset[int], float]]
    """(bus, phase nodes, kV LL) of every enabled source."""
    shunt_buses: set[str]
    """Buses with an enabled source, load, capacitor or PV system."""
    voltage_bases: list[float]
    """derived_voltage_bases of the model."""
    _anchors: list[tuple[str, float]] = field(default_factory=list, repr=False)
    _transformers: list[_TransformerNodes] = field(default_factory=list, repr=False)
    _adj: dict[str, list[Edge]] = field(default_factory=dict, repr=False)
    _available: dict[str, frozenset[int]] | None = field(default=None, repr=False)
    _zones: dict[str, float] | None = field(default=None, repr=False)

    # ---- construction

    @classmethod
    def build(cls, model: GridModel) -> GridGraph:
        buses: list[str] = list(model.buses)
        known = set(buses)

        def note(bus: str) -> None:
            if bus not in known:
                known.add(bus)
                buses.append(bus)

        bases = derived_voltage_bases(model)
        edges: list[Edge] = []
        sources: list[tuple[str, frozenset[int], float]] = []
        anchors: list[tuple[str, float]] = []
        shunts: set[str] = set()
        transformers: list[_TransformerNodes] = []

        for s in model.sources.values():
            if not s.enabled or s.bus1.bus == DISCONNECTED:
                continue
            note(s.bus1.bus)
            shunts.add(s.bus1.bus)
            nodes = frozenset(n for n in terminal_nodes(s.bus1, s.phases) if n != 0)
            kv = snap_kv(float(s.kv), bases) if _usable_kv(s.kv) else 0.0
            sources.append((s.bus1.bus, nodes, kv))
            if kv > 0:
                anchors.append((s.bus1.bus, kv))
        for ln in model.lines.values():
            if ln.enabled and _connected(ln.bus1, ln.bus2):
                edges.append(_branch(ln.id, LINE, ln.bus1, ln.bus2, ln.phases, True))
        for sw in model.switches.values():
            if sw.enabled and _connected(sw.bus1, sw.bus2):
                edges.append(_branch(sw.id, SWITCH, sw.bus1, sw.bus2, sw.phases, sw.closed))
        for t in model.transformers.values():
            if not t.enabled:
                continue
            ws = list(t.windings)
            for k in range(1, len(ws)):
                a, b = ws[0].terminal, ws[k].terminal
                if not _connected(a, b):
                    continue
                edges.append(
                    Edge(
                        t.id,
                        TRANSFORMER,
                        a.bus,
                        b.bus,
                        terminal_nodes(a, t.phases),
                        terminal_nodes(b, t.phases),
                        True,
                        1,
                        k + 1,
                    )
                )
            for w in ws:
                if w.terminal.bus == DISCONNECTED:
                    continue
                note(w.terminal.bus)
                if _usable_kv(w.kv):
                    anchors.append((w.terminal.bus, snap_kv(float(winding_kv_ll(t, w)), bases)))
            transformers.append(
                _TransformerNodes(
                    t.id,
                    tuple(
                        (
                            w.terminal.bus,
                            required_nodes(w.terminal, t.phases, w.conn),
                            frozenset(n for n in terminal_nodes(w.terminal, t.phases) if n),
                        )
                        for w in ws
                        if w.terminal.bus != DISCONNECTED
                    ),
                )
            )
        for group in (model.loads, model.capacitors, model.pvsystems):
            for el in group.values():
                if el.terminal.bus == DISCONNECTED:
                    continue
                note(el.terminal.bus)
                if el.enabled:
                    shunts.add(el.terminal.bus)

        adj: dict[str, list[Edge]] = {b: [] for b in buses}
        for e in edges:
            note(e.bus_a)
            note(e.bus_b)
            adj.setdefault(e.bus_a, []).append(e)
            if e.bus_b != e.bus_a:
                adj.setdefault(e.bus_b, []).append(e)
        return cls(buses, edges, sources, shunts, bases, anchors, transformers, adj)

    # ---- walks

    def neighbours(self, bus: str, include_open: bool = False) -> Iterator[tuple[Edge, str]]:
        for e in self._adj.get(bus, ()):
            if e.closed or include_open:
                yield e, e.other(bus)

    def reachable(self, start: Iterable[str], include_open: bool = False) -> set[str]:
        seen = set(start)
        queue = deque(seen)
        while queue:
            for _, nxt in self.neighbours(queue.popleft(), include_open):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def energized_buses(self, include_open: bool = False) -> set[str]:
        """Buses reached from an enabled source over closed branches."""
        return self.reachable((b for b, _, _ in self.source_buses), include_open)

    def source_distances(self, weight: Callable[[Edge], float]) -> dict[str, float]:
        """Shortest weighted distance from any enabled source bus to every bus it
        reaches over closed branches (Dijkstra). ``weight`` gives an edge's length and
        must not be negative; open switches are not paths."""
        dist: dict[str, float] = {}
        heap = [(0.0, bus) for bus, _, _ in self.source_buses]
        heapq.heapify(heap)
        while heap:
            d, bus = heapq.heappop(heap)
            if bus in dist:
                continue
            dist[bus] = d
            for e, nxt in self.neighbours(bus):
                if nxt not in dist:
                    heapq.heappush(heap, (d + weight(e), nxt))
        return dist

    def islands(self) -> list[tuple[str, ...]]:
        """Connected parts over closed branches, each in bus order, ordered by their
        first bus. A bus with no branch is an island of its own."""
        out: list[tuple[str, ...]] = []
        seen: set[str] = set()
        order = {b: i for i, b in enumerate(self.buses)}
        for bus in self.buses:
            if bus in seen:
                continue
            part = self.reachable([bus])
            seen |= part
            out.append(tuple(sorted(part, key=order.__getitem__)))
        return out

    def path(self, bus_a: str, bus_b: str, include_open: bool = False) -> list[Edge] | None:
        """Shortest chain of edges from ``bus_a`` to ``bus_b`` (empty if they are the
        same bus), or None when they are not connected."""
        if bus_a == bus_b:
            return [] if bus_a in self._adj else None
        back: dict[str, tuple[Edge, str]] = {}
        seen = {bus_a}
        queue = deque([bus_a])
        while queue:
            bus = queue.popleft()
            for e, nxt in self.neighbours(bus, include_open):
                if nxt in seen:
                    continue
                seen.add(nxt)
                back[nxt] = (e, bus)
                if nxt == bus_b:
                    chain: list[Edge] = []
                    cur = bus_b
                    while cur != bus_a:
                        e_prev, cur = back[cur]
                        chain.append(e_prev)
                    return chain[::-1]
                queue.append(nxt)
        return None

    # ---- deterministic walks for the diagram layout (D3)

    def connections(self, bus: str, include_open: bool = True) -> list[tuple[str, Edge]]:
        """One (neighbour, edge) per neighbouring bus, sorted by neighbour name.
        Parallel branches (the three 1-phase regulators 650-rg60) are one connection;
        its edge is a closed one if there is one, then a transformer edge."""
        best: dict[str, Edge] = {}
        for e in self._adj.get(bus, ()):
            if e.bus_a == e.bus_b or not (e.closed or include_open):
                continue
            nxt = e.other(bus)
            old = best.get(nxt)
            rank = (e.closed, e.kind == TRANSFORMER)
            if old is None or rank > (old.closed, old.kind == TRANSFORMER):
                best[nxt] = e
        return sorted(best.items())

    def bfs_tree(
        self, starts: Iterable[str], include_open: bool = True, seen: set[str] | None = None
    ) -> list[tuple[str, str, Edge]]:
        """Breadth-first from ``starts`` (in the given order) with neighbours in name
        order: (bus, parent, edge) for every bus reached that is not a start and not in
        ``seen``. ``seen`` is updated."""
        seen = set() if seen is None else seen
        queue = deque(starts)
        seen.update(queue)
        out: list[tuple[str, str, Edge]] = []
        while queue:
            bus = queue.popleft()
            for nxt, e in self.connections(bus, include_open):
                if nxt not in seen:
                    seen.add(nxt)
                    out.append((nxt, bus, e))
                    queue.append(nxt)
        return out

    def spanning_forest(self) -> SpanningForest:
        """A spanning forest of every bus, deterministic. From the first source bus:
        breadth-first over closed branches, then from everything reached over any
        branch (buses behind an open switch hang off the bus that reaches them). Other
        source buses not reached yet, then every other bus, start a tree of their own
        (over any branch)."""
        parent: dict[str, str | None] = {}
        edge: dict[str, Edge] = {}
        order: list[str] = []
        roots: list[str] = []
        seen: set[str] = set()

        def grow(found: list[tuple[str, str, Edge]]) -> None:
            for bus, par, e in found:
                parent[bus], edge[bus] = par, e
                order.append(bus)

        starts = [b for b, _, _ in self.source_buses]
        for root in [*dict.fromkeys(starts), *self.buses]:
            if root in seen:
                continue
            roots.append(root)
            parent[root] = None
            order.append(root)
            before = len(order) - 1
            grow(self.bfs_tree([root], include_open=False, seen=seen))
            grow(self.bfs_tree(order[before:], include_open=True, seen=seen))
        return SpanningForest(roots, order, parent, edge)

    def hop_distance(self, starts: Iterable[str], include_open: bool = True) -> dict[str, int]:
        """Number of branches from the nearest of ``starts`` to every bus reached."""
        starts = list(starts)
        dist = dict.fromkeys(starts, 0)
        for bus, par, _ in self.bfs_tree(starts, include_open):
            dist[bus] = dist[par] + 1
        return dist

    # ---- voltage zones

    def voltage_zones(self) -> dict[str, float]:
        """{bus: kV LL} for every bus that a source or winding reaches over lines and
        switches; buses with no such anchor are missing."""
        if self._zones is None:
            zones: dict[str, float] = {}
            queue: deque[str] = deque()
            for bus, kv in self._anchors:
                if bus not in zones:
                    zones[bus] = kv
                    queue.append(bus)
            while queue:
                bus = queue.popleft()
                for e in self._adj.get(bus, ()):
                    if e.kind == TRANSFORMER:
                        continue
                    nxt = e.other(bus)
                    if nxt not in zones:
                        zones[nxt] = zones[bus]
                        queue.append(nxt)
            self._zones = zones
        return dict(self._zones)

    def zone_conflicts(self) -> list[tuple[Edge, float, float]]:
        """(edge, kV at bus_a, kV at bus_b) of every line or switch whose two buses are
        in different voltage zones; one entry per element."""
        zones = self.voltage_zones()
        out: list[tuple[Edge, float, float]] = []
        for e in self.edges:
            if e.kind == TRANSFORMER:
                continue
            ka, kb = zones.get(e.bus_a), zones.get(e.bus_b)
            if ka is not None and kb is not None and not math.isclose(ka, kb, rel_tol=1e-9):
                out.append((e, ka, kb))
        return out

    # ---- phases

    def available_nodes(self, bus: str) -> tuple[int, ...]:
        """Sorted phase nodes present at ``bus``; empty when no source reaches it."""
        return tuple(sorted(self._all_available().get(bus, ())))

    def _all_available(self) -> dict[str, frozenset[int]]:
        if self._available is not None:
            return self._available
        avail: dict[str, set[int]] = {}
        by_bus: dict[str, list[_TransformerNodes]] = {}
        for t in self._transformers:
            for bus, _, _ in t.windings:
                by_bus.setdefault(bus, []).append(t)
        queue: deque[str] = deque()

        def add(bus: str, nodes: Iterable[int]) -> None:
            have = avail.setdefault(bus, set())
            new = set(nodes) - have - {0}
            if new:
                have |= new
                queue.append(bus)

        for bus, nodes, _ in self.source_buses:
            add(bus, nodes)
        while queue:
            bus = queue.popleft()
            have = avail[bus]
            for e in self._adj.get(bus, ()):
                if e.kind == TRANSFORMER:
                    continue
                pairs = list(zip(e.nodes_a, e.nodes_b, strict=False))  # conductor k to k
                if e.bus_a == bus:
                    add(e.bus_b, [m for n, m in pairs if n in have])
                if e.bus_b == bus:
                    add(e.bus_a, [n for n, m in pairs if m in have])
            for t in by_bus.get(bus, ()):
                if any(req and req <= avail.get(b, set()) for b, req, _ in t.windings):
                    for b, _, nodes in t.windings:
                        add(b, nodes)
        self._available = {b: frozenset(n) for b, n in avail.items()}
        return self._available

    # ---- loops

    def loops(self) -> list[Loop]:
        """Independent cycles over closed branches (one per branch that closes one)."""
        out: list[Loop] = []
        bundles: dict[frozenset[str], list[Edge]] = {}
        parent: dict[str, str] = {}
        tree: dict[str, list[tuple[Edge, str]]] = {}

        def root(b: str) -> str:
            parent.setdefault(b, b)
            while parent[b] != b:
                parent[b] = parent[parent[b]]
                b = parent[b]
            return b

        for e in self.edges:
            if not e.closed or e.bus_a == e.bus_b:
                continue
            key = frozenset((e.bus_a, e.bus_b))
            bundle = bundles.get(key)
            if bundle is not None:
                for prev in bundle:
                    if prev.element_id != e.element_id and _share_nodes(prev, e):
                        out.append(Loop((prev.element_id, e.element_id), (e.bus_a, e.bus_b)))
                        break
                bundle.append(e)
                continue
            bundles[key] = [e]
            ra, rb = root(e.bus_a), root(e.bus_b)
            if ra != rb:
                parent[ra] = rb
                tree.setdefault(e.bus_a, []).append((e, e.bus_b))
                tree.setdefault(e.bus_b, []).append((e, e.bus_a))
                continue
            chain, buses = _tree_path(tree, e.bus_b, e.bus_a)
            out.append(Loop((*(c.element_id for c in chain), e.element_id), tuple(buses)))
        return out


def _connected(*terms: Terminal) -> bool:
    return all(t.bus != DISCONNECTED for t in terms)


def _branch(
    element_id: str, kind: str, bus1: Terminal, bus2: Terminal, phases: int, closed: bool
) -> Edge:
    return Edge(
        element_id,
        kind,
        bus1.bus,
        bus2.bus,
        terminal_nodes(bus1, phases),
        terminal_nodes(bus2, phases),
        closed,
    )


def _share_nodes(a: Edge, b: Edge) -> bool:
    """Two parallel edges share a node at both of their buses."""

    def at(e: Edge, bus: str) -> set[int]:
        return {n for n in (e.nodes_a if e.bus_a == bus else e.nodes_b) if n != 0}

    return all(at(a, bus) & at(b, bus) for bus in (a.bus_a, a.bus_b))


def _tree_path(
    tree: dict[str, list[tuple[Edge, str]]], start: str, goal: str
) -> tuple[list[Edge], list[str]]:
    back: dict[str, tuple[Edge, str]] = {}
    seen = {start}
    queue = deque([start])
    while queue and goal not in seen:
        bus = queue.popleft()
        for e, nxt in tree.get(bus, ()):
            if nxt not in seen:
                seen.add(nxt)
                back[nxt] = (e, bus)
                queue.append(nxt)
    chain: list[Edge] = []
    buses = [goal]
    cur = goal
    while cur != start:
        e, cur = back[cur]
        chain.append(e)
        buses.append(cur)
    return chain, buses
