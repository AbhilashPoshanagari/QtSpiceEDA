"""Schematic connectivity: which pins, wires and flags form one net. Qt-free.

``connect(schematic, lookup)`` runs a union-find over everything that has a position:

* things at the same point connect (wire ends, pins after rotation/mirror, net labels,
  ground flags, port flags);
* a T-join connects: a wire end, pin or flag on another wire's middle;
* two wires that only cross (neither has an end at the crossing) stay separate;
* net labels and port flags with the same name connect (case-insensitive, as SPICE
  node names are), and all ground flags are one net.

Net names: a net with a ground flag is ``0``; otherwise the name of its label or port
flag; otherwise ``N001``, ``N002``... numbered in a stable order (instances in sheet
order with their pins in SpiceOrder, then wires in sheet order). Numbers already used
as a label name are skipped. A pin that touches nothing gets the net ``""`` (the
netlister reports it as not connected).

Two different names on one net give a warning; ground wins over a label, and
otherwise the first label (then port flag) in sheet order wins.

``junctions`` are the points where a dot belongs: three or more connections meet
(wire ends and pins count one, a wire passing through counts two) and at least one
is a wire.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from core.netlist.model import ERROR, WARNING, Diagnostic
from core.schematic.model import Point, Schematic, pin_points
from core.symbols.asy import Symbol

Item = tuple  # ("w", i) wire, ("p", inst, pin), ("l", i), ("g", i), ("o", i)


@dataclass
class NetMap:
    """The result of ``connect``.

    ``pins[i]`` holds the nets of ``schematic.instances[i]`` in SpiceOrder (empty when
    its symbol was not found). ``wires[i]`` is the net of ``schematic.wires[i]``.
    ``ports[i]`` is the net of ``schematic.ports[i]`` ("" when the flag touches
    nothing and no other flag has its name).
    """

    nets: list[str] = field(default_factory=list)
    pins: list[list[str]] = field(default_factory=list)
    wires: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[Item, Item] = {}

    def add(self, item: Item) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: Item) -> Item:
        parent = self.parent
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    def union(self, a: Item, b: Item) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def connect(schematic: Schematic, lookup: Callable[[str], Symbol | None]) -> NetMap:
    """Nets of every pin and wire. ``lookup`` gives the Symbol for a symbol name."""
    out = NetMap()
    uf = _UnionFind()
    at: dict[Point, list[Item]] = defaultdict(list)  # point -> things that end there
    degree: dict[Point, int] = defaultdict(int)
    wire_at: dict[Point, int] = defaultdict(int)

    def place(item: Item, point: Point, counts: bool) -> None:
        uf.add(item)
        at[point].append(item)
        if counts:
            degree[point] += 1

    pin_items: list[list[Item]] = []
    for i, inst in enumerate(schematic.instances):
        symbol = lookup(inst.symbol)
        if symbol is None:
            out.diagnostics.append(
                Diagnostic(0, 0, ERROR, f"{inst.name}: symbol not found: {inst.symbol}")
            )
            pin_items.append([])
            continue
        items = []
        for k, point in enumerate(pin_points(inst, symbol)):
            item = ("p", i, k)
            place(item, point, True)
            items.append(item)
        pin_items.append(items)
    for i, wire in enumerate(schematic.wires):
        item = ("w", i)
        for end in wire.ends:
            place(item, end, True)
            wire_at[end] += 1
    for i, label in enumerate(schematic.labels):
        place(("l", i), (label.x, label.y), False)
    for i, ground in enumerate(schematic.grounds):
        place(("g", i), (ground.x, ground.y), False)
    for i, port in enumerate(schematic.ports):
        place(("o", i), (port.x, port.y), False)

    # Same point.
    for items in at.values():
        for other in items[1:]:
            uf.union(items[0], other)

    # T-joins: a point on a wire's middle.
    for i, point in _points_inside_wires(schematic, at.keys()):
        uf.union(("w", i), at[point][0])
        degree[point] += 2
        wire_at[point] += 1

    # Names connect too.
    by_name: dict[str, Item] = {}
    named = [(("l", i), n.name) for i, n in enumerate(schematic.labels)]
    named += [(("o", i), p.name) for i, p in enumerate(schematic.ports)]
    for item, name in named:
        first = by_name.setdefault(name.casefold(), item)
        uf.union(first, item)
    for i in range(1, len(schematic.grounds)):
        uf.union(("g", 0), ("g", i))

    names = _name_roots(schematic, uf, named, out.diagnostics)

    # Stable numbering for unnamed nets; a lone pin is not connected.
    used = {n.casefold() for n in names.values()}
    size: dict[Item, int] = defaultdict(int)
    for item in uf.parent:
        size[uf.find(item)] += 1
    counter = 0

    def net_of(item: Item, alone_is_empty: bool) -> str:
        nonlocal counter
        root = uf.find(item)
        if root in names:
            return names[root]
        if alone_is_empty and size[root] == 1:
            return ""
        while True:
            counter += 1
            candidate = f"N{counter:03d}"
            if candidate.casefold() not in used:
                break
        names[root] = candidate
        return candidate

    for items in pin_items:
        out.pins.append([net_of(item, True) for item in items])
    out.wires = [net_of(("w", i), False) for i in range(len(schematic.wires))]
    out.ports = [
        "" if size[uf.find(("o", i))] == 1 else net_of(("o", i), False)
        for i in range(len(schematic.ports))
    ]

    seen: dict[str, None] = {}
    for net in [n for nets in out.pins for n in nets] + out.wires:
        if net:
            seen.setdefault(net, None)
    out.nets = list(seen)
    out.junctions = sorted(p for p, d in degree.items() if d >= 3 and wire_at[p] > 0)
    return out


def _name_roots(
    schematic: Schematic,
    uf: _UnionFind,
    named: list[tuple[Item, str]],
    diagnostics: list[Diagnostic],
) -> dict[Item, str]:
    candidates: dict[Item, list[str]] = defaultdict(list)
    for item, name in named:
        root = uf.find(item)
        if all(n.casefold() != name.casefold() for n in candidates[root]):
            candidates[root].append(name)
    grounded = {uf.find(("g", i)) for i in range(len(schematic.grounds))}
    names: dict[Item, str] = {}
    for root in grounded:
        names[root] = "0"
        for name in candidates.get(root, []):
            if name != "0":
                diagnostics.append(
                    Diagnostic(0, 0, WARNING, f"net label {name} is on the ground net; it is 0")
                )
    for root, found in candidates.items():
        if root in names:
            continue
        names[root] = found[0]
        if len(found) > 1:
            others = ", ".join(found[1:])
            diagnostics.append(
                Diagnostic(
                    0,
                    0,
                    WARNING,
                    f"one net has the names {found[0]} and {others}; it is {found[0]}",
                )
            )
    return names


def _points_inside_wires(schematic: Schematic, points: Iterable[Point]) -> list[tuple[int, Point]]:
    """(wire index, point) for every point strictly inside a wire."""
    rows: dict[int, list[int]] = defaultdict(list)  # y -> xs
    cols: dict[int, list[int]] = defaultdict(list)  # x -> ys
    pts = list(points)
    for x, y in pts:
        rows[y].append(x)
        cols[x].append(y)
    found: list[tuple[int, Point]] = []
    for i, w in enumerate(schematic.wires):
        if w.y1 == w.y2:
            lo, hi = sorted((w.x1, w.x2))
            found += [(i, (x, w.y1)) for x in rows.get(w.y1, []) if lo < x < hi]
        elif w.x1 == w.x2:
            lo, hi = sorted((w.y1, w.y2))
            found += [(i, (w.x1, y)) for y in cols.get(w.x1, []) if lo < y < hi]
        else:
            for x, y in pts:
                if _inside(w.x1, w.y1, w.x2, w.y2, x, y):
                    found.append((i, (x, y)))
    return found


def _inside(x1: int, y1: int, x2: int, y2: int, x: int, y: int) -> bool:
    if (x2 - x1) * (y - y1) != (y2 - y1) * (x - x1):
        return False
    if (x, y) in ((x1, y1), (x2, y2)):
        return False
    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)
