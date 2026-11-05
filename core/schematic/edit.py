"""Editing operations on a selection of a schematic (S8). Qt-free.

A selection is a set of ``(kind, index)`` pairs (kinds from core.schematic.geometry:
instance, wire, label, ground, port). Every function returns a new Schematic and
leaves its input alone, so the editor can keep the "before" state for undo.

* ``rotate`` / ``mirror`` turn the selection about its centre (snapped to the grid),
  90 degrees clockwise on screen or left-right, as Ctrl+R / Ctrl+E in LTspice.
* ``move`` shifts the selection. With ``follow`` (Drag) the ends of unselected wires
  that touch what moves go with it, so connections stay; without it (Move) wires
  detach. Wires may end up diagonal, as in LTspice.
* ``delete`` removes the selection.
* ``copy`` / ``paste``: a clipboard is a Schematic of the selected items; pasted parts
  get the next free names (R1 -> R3 when R1 and R2 exist).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable

from core.schematic.geometry import (
    GROUND,
    INSTANCE,
    LABEL,
    PORT,
    WIRE,
    Lookup,
    instance_box,
    items_of,
    snap,
    turn,
)
from core.schematic.model import Instance, Point, Schematic, pin_points

Selection = set[tuple[str, int]]
KINDS = (INSTANCE, WIRE, LABEL, GROUND, PORT)


def valid(schematic: Schematic, selection: Iterable[tuple[str, int]]) -> Selection:
    """The pairs of ``selection`` that name something on the sheet."""
    return {(k, i) for k, i in selection if k in KINDS and 0 <= i < len(items_of(schematic, k))}


def centre(schematic: Schematic, lookup: Lookup, selection: Selection) -> Point | None:
    """The middle of the selection's box, snapped to the grid."""
    boxes = []
    for kind, i in valid(schematic, selection):
        if kind == INSTANCE:
            inst = schematic.instances[i]
            boxes.append(instance_box(inst, lookup(inst.symbol)))
        elif kind == WIRE:
            w = schematic.wires[i]
            boxes.append((min(w.x1, w.x2), min(w.y1, w.y2), max(w.x1, w.x2), max(w.y1, w.y2)))
        else:
            f = items_of(schematic, kind)[i]
            boxes.append((f.x, f.y, f.x, f.y))
    if not boxes:
        return None
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    return snap((x1 + x2) / 2), snap((y1 + y2) / 2)


def _map_points(
    schematic: Schematic,
    selection: Selection,
    fn: Callable[[int, int], Point],
    orient: Callable[[int, bool], tuple[int, bool]] | None = None,
) -> Schematic:
    out = copy.deepcopy(schematic)
    for kind, i in valid(schematic, selection):
        item = items_of(out, kind)[i]
        if kind == WIRE:
            item.x1, item.y1 = fn(item.x1, item.y1)
            item.x2, item.y2 = fn(item.x2, item.y2)
        else:
            item.x, item.y = fn(item.x, item.y)
            if kind == INSTANCE and orient is not None:
                item.rotation, item.mirror = orient(item.rotation, item.mirror)
    return out


def rotate(schematic: Schematic, lookup: Lookup, selection: Selection) -> Schematic:
    c = centre(schematic, lookup, selection)
    if c is None:
        return copy.deepcopy(schematic)
    cx, cy = c
    return _map_points(
        schematic,
        selection,
        lambda x, y: (cx - (y - cy), cy + (x - cx)),
        lambda r, m: turn(r, m, 90, False),
    )


def mirror(schematic: Schematic, lookup: Lookup, selection: Selection) -> Schematic:
    c = centre(schematic, lookup, selection)
    if c is None:
        return copy.deepcopy(schematic)
    cx, _ = c
    return _map_points(
        schematic, selection, lambda x, y: (2 * cx - x, y), lambda r, m: turn(r, m, 0, True)
    )


def connection_points(schematic: Schematic, lookup: Lookup, selection: Selection) -> set[Point]:
    """Where the selection can touch a wire: its pins, wire ends and flag points."""
    points: set[Point] = set()
    for kind, i in valid(schematic, selection):
        if kind == INSTANCE:
            inst = schematic.instances[i]
            symbol = lookup(inst.symbol)
            if symbol is not None:
                points.update(pin_points(inst, symbol))
        elif kind == WIRE:
            points.update(schematic.wires[i].ends)
        else:
            f = items_of(schematic, kind)[i]
            points.add((f.x, f.y))
    return points


def move(
    schematic: Schematic,
    lookup: Lookup,
    selection: Selection,
    dx: int,
    dy: int,
    follow: bool,
) -> Schematic:
    """The selection shifted by (dx, dy); with ``follow`` attached wire ends go along."""
    out = _map_points(schematic, selection, lambda x, y: (x + dx, y + dy))
    if not follow or (dx, dy) == (0, 0):
        return out
    touching = connection_points(schematic, lookup, selection)
    chosen = {i for k, i in valid(schematic, selection) if k == WIRE}
    for i, w in enumerate(schematic.wires):
        if i in chosen:
            continue
        new = out.wires[i]
        if (w.x1, w.y1) in touching:
            new.x1, new.y1 = w.x1 + dx, w.y1 + dy
        if (w.x2, w.y2) in touching:
            new.x2, new.y2 = w.x2 + dx, w.y2 + dy
    out.wires = [w for w in out.wires if (w.x1, w.y1) != (w.x2, w.y2)]
    return out


def delete(schematic: Schematic, selection: Selection) -> Schematic:
    out = copy.deepcopy(schematic)
    gone = valid(schematic, selection)
    for kind in KINDS:
        items = items_of(out, kind)
        items[:] = [item for i, item in enumerate(items) if (kind, i) not in gone]
    return out


def copy_selection(schematic: Schematic, selection: Selection) -> Schematic:
    """A clipboard: just the selected items (title and texts are not copied)."""
    clip = Schematic()
    for kind in KINDS:
        chosen = sorted(i for k, i in valid(schematic, selection) if k == kind)
        items_of(clip, kind).extend(copy.deepcopy(items_of(schematic, kind)[i]) for i in chosen)
    return clip


def paste(schematic: Schematic, clip: Schematic, dx: int, dy: int) -> tuple[Schematic, Selection]:
    """``schematic`` with the clipboard added, shifted by (dx, dy), and the selection
    of the pasted items. Pasted parts are renamed to free names."""
    out = copy.deepcopy(schematic)
    shifted = move(clip, lambda _: None, all_items(clip), dx, dy, False)
    selection: Selection = set()
    for inst in shifted.instances:
        inst.name = free_name(inst.name, out.instances)
    for kind in KINDS:
        start = len(items_of(out, kind))
        new = items_of(shifted, kind)
        items_of(out, kind).extend(new)
        selection |= {(kind, start + k) for k in range(len(new))}
    return out, selection


def all_items(schematic: Schematic) -> Selection:
    return {(k, i) for k in KINDS for i in range(len(items_of(schematic, k)))}


def free_name(name: str, instances: list[Instance]) -> str:
    """``name`` if no instance uses it, else its letters plus the lowest free number."""
    used = {i.name.casefold() for i in instances}
    if name.casefold() not in used:
        return name
    base = name.rstrip("0123456789") or "U"
    n = 1
    while f"{base}{n}".casefold() in used:
        n += 1
    return f"{base}{n}"
