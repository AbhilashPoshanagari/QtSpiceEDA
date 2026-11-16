"""S2 spike: a Qt-free command protocol for designer edits (scratch only).

Every command changes GridModel and DiagramModel together and reverts exactly
(rule 22). Two storage strategies are compared:

- **delta** commands store only what they change (the old and new value, the removed
  object and its dict position);
- ``SnapshotCommand`` wraps any command and stores the whole state before and after it,
  either as deep-copied objects ("deepcopy") or as pickle bytes ("pickle").

Commands in the spike: AddElement (used for "add load"), MoveGlyph, SetProperty,
RemoveElement, and CompoundCommand (used for "delete bus with everything connected").
"""

from __future__ import annotations

import copy
import pickle
from dataclasses import dataclass, field
from typing import Any, Protocol

from state import (
    GLYPHED,
    DesignState,
    ElementGlyph,
    resolve,
    terminal_buses,
)

from core.grid.model import Load, Terminal

NO_MERGE = -1
MERGE_MOVE = 1
MERGE_SET_PROPERTY = 2


class Command(Protocol):
    description: str

    @property
    def merge_id(self) -> int:
        """NO_MERGE (-1) or an id shared by commands that may merge (QUndoCommand.id)."""
        ...

    def apply(self, state: DesignState) -> None: ...

    def revert(self, state: DesignState) -> None: ...

    def merge(self, other: Command) -> bool:
        """Absorb ``other`` (already applied, directly after self). True if merged."""
        ...

    def affected(self) -> set[str]:
        """Element ids whose model rows changed (lets the bridge update incrementally)."""
        ...


# --- dict helpers that keep key order --------------------------------------------------


def _insert_at(d: dict[str, Any], index: int, key: str, value: Any) -> None:
    if index >= len(d):
        d[key] = value
        return
    items = list(d.items())
    items.insert(index, (key, value))
    d.clear()
    d.update(items)


def _pop_with_index(d: dict[str, Any], key: str) -> tuple[int, Any]:
    index = list(d).index(key)
    return index, d.pop(key)


def _glyph_dict(state: DesignState, attr: str) -> dict[str, Any] | None:
    """The diagram dict that holds the glyphs of the elements in GridModel.<attr>."""
    if attr == "buses":
        return state.diagram.bus_glyphs
    if any(attr == a for a, _ in GLYPHED):
        return state.diagram.element_glyphs
    return None


def _glyph_key(attr: str, element: Any) -> str:
    return element.name if attr == "buses" else element.id


# --- delta commands --------------------------------------------------------------------


class _Base:
    description = ""
    merge_id = NO_MERGE

    def merge(self, other: Command) -> bool:
        return False


class AddElement(_Base):
    """Add one element (or bus) and its glyph at the end of their dicts."""

    def __init__(self, attr: str, element: Any, glyph: Any | None) -> None:
        self.attr, self.element, self.glyph = attr, element, glyph
        self.description = f"Add {element.id}"

    def apply(self, state: DesignState) -> None:
        d = getattr(state.grid, self.attr)
        assert self.element.name not in d, self.element.id
        d[self.element.name] = self.element
        gd = _glyph_dict(state, self.attr)
        if gd is not None:
            gd[_glyph_key(self.attr, self.element)] = self.glyph

    def revert(self, state: DesignState) -> None:
        del getattr(state.grid, self.attr)[self.element.name]
        gd = _glyph_dict(state, self.attr)
        if gd is not None:
            del gd[_glyph_key(self.attr, self.element)]

    def affected(self) -> set[str]:
        return {self.element.id}


class RemoveElement(_Base):
    """Remove one element (or bus) and its glyph; revert puts both objects back at
    their old dict positions (same objects, so identity is kept too)."""

    def __init__(self, element_id: str) -> None:
        self.element_id = element_id
        self.description = f"Remove {element_id}"
        self._saved: tuple[str, int, Any, int, Any] | None = None

    def apply(self, state: DesignState) -> None:
        attr, name = resolve(state.grid, self.element_id)
        idx, element = _pop_with_index(getattr(state.grid, attr), name)
        gidx, glyph = -1, None
        gd = _glyph_dict(state, attr)
        if gd is not None:
            gidx, glyph = _pop_with_index(gd, _glyph_key(attr, element))
        self._saved = (attr, idx, element, gidx, glyph)

    def revert(self, state: DesignState) -> None:
        assert self._saved is not None
        attr, idx, element, gidx, glyph = self._saved
        _insert_at(getattr(state.grid, attr), idx, element.name, element)
        gd = _glyph_dict(state, attr)
        if gd is not None:
            _insert_at(gd, gidx, _glyph_key(attr, element), glyph)

    def affected(self) -> set[str]:
        return {self.element_id}


class MoveGlyph(_Base):
    """Move a bus glyph (key "bus.<name>") or element glyph. Presentation only.

    Merges with the next MoveGlyph of the same glyph *and the same gesture* (one mouse
    press), so a 200-event drag is one undo step but two drags stay two steps."""

    merge_id = MERGE_MOVE

    def __init__(self, glyph_id: str, x: float, y: float, gesture: int) -> None:
        self.glyph_id, self.new, self.gesture = glyph_id, (x, y), gesture
        self.old: tuple[float, float] | None = None
        self.description = f"Move {glyph_id}"

    def _glyph(self, state: DesignState) -> Any:
        if self.glyph_id.startswith("bus."):
            return state.diagram.bus_glyphs[self.glyph_id[4:]]
        return state.diagram.element_glyphs[self.glyph_id]

    def apply(self, state: DesignState) -> None:
        g = self._glyph(state)
        if self.old is None:
            self.old = (g.x, g.y)
        g.x, g.y = self.new

    def revert(self, state: DesignState) -> None:
        assert self.old is not None
        g = self._glyph(state)
        g.x, g.y = self.old

    def merge(self, other: Command) -> bool:
        if not (
            isinstance(other, MoveGlyph)
            and other.glyph_id == self.glyph_id
            and other.gesture == self.gesture
        ):
            return False
        self.new = other.new
        return True

    def is_noop(self) -> bool:
        return self.old == self.new

    def affected(self) -> set[str]:
        return {self.glyph_id}


_MISSING = object()


class SetProperty(_Base):
    """Set one field of a GridModel element. ``path`` is a dotted attribute path with
    integer list indices ("kw", "windings.1.tap"). Stores the old value only."""

    def __init__(self, element_id: str, path: str, value: Any, merge_key: str = "") -> None:
        self.element_id, self.path, self.value = element_id, path, value
        self.old: Any = _MISSING
        self.merge_key = merge_key
        """Non-empty: merge with the next SetProperty of the same field and key (e.g. one
        spin-box edit session). Empty: never merge."""
        self.description = f"Set {element_id} {path}"
        if merge_key:
            self.merge_id = MERGE_SET_PROPERTY

    def _target(self, state: DesignState) -> tuple[Any, str]:
        attr, name = resolve(state.grid, self.element_id)
        obj: Any = getattr(state.grid, attr)[name]
        *walk, last = self.path.split(".")
        for part in walk:
            obj = obj[int(part)] if part.isdigit() else getattr(obj, part)
        return obj, last

    def apply(self, state: DesignState) -> None:
        obj, last = self._target(state)
        if self.old is _MISSING:
            self.old = getattr(obj, last)
        setattr(obj, last, self.value)

    def revert(self, state: DesignState) -> None:
        obj, last = self._target(state)
        setattr(obj, last, self.old)

    def merge(self, other: Command) -> bool:
        if not (
            isinstance(other, SetProperty)
            and self.merge_key
            and other.merge_key == self.merge_key
            and (other.element_id, other.path) == (self.element_id, self.path)
        ):
            return False
        self.value = other.value
        return True

    def affected(self) -> set[str]:
        return {self.element_id}


@dataclass
class CompoundCommand:
    """Children applied in order, reverted in reverse order. One undo step."""

    description: str
    children: list[Command] = field(default_factory=list)
    merge_id: int = NO_MERGE

    def apply(self, state: DesignState) -> None:
        for c in self.children:
            c.apply(state)

    def revert(self, state: DesignState) -> None:
        for c in reversed(self.children):
            c.revert(state)

    def merge(self, other: Command) -> bool:
        return False

    def affected(self) -> set[str]:
        return set().union(*(c.affected() for c in self.children))


# --- command factories (what the bridge slots would build) -----------------------------


def lowest_free(names: dict[str, Any], prefix: str) -> str:
    n = 1
    while f"{prefix}{n}" in names:
        n += 1
    return f"{prefix}{n}"


def add_load(state: DesignState, bus: str, template: Load) -> AddElement:
    """New load at ``bus``; electrical values copied from an existing load (rule 24)."""
    name = lowest_free(state.grid.loads, "ld")
    ld = Load(
        name, Terminal(bus, template.terminal.nodes), template.phases, template.conn,
        template.kv, template.kw, template.kvar, template.model,
    )  # fmt: skip
    b = state.diagram.bus_glyphs[bus]
    return AddElement("loads", ld, ElementGlyph(b.x, b.y + 2.0))


def delete_bus(state: DesignState, bus: str) -> CompoundCommand:
    """Remove a bus and every element connected to it (regulators of removed
    transformers first, then branches and shunts, then the bus). In D4 the connected
    set comes from topology.py (rule 25); the spike scans the model."""
    g = state.grid
    connected: list[str] = []
    removed_tx: set[str] = set()
    for attr, _ in GLYPHED:
        for e in getattr(g, attr).values():
            if bus in terminal_buses(e):
                connected.append(e.id)
                if attr == "transformers":
                    removed_tx.add(e.name)
    regs = [r.id for r in g.regulators.values() if r.transformer in removed_tx]
    children: list[Command] = [RemoveElement(i) for i in regs + connected]
    children.append(RemoveElement(f"bus.{bus}"))
    return CompoundCommand(f"Delete bus.{bus}", children)


# --- snapshots -------------------------------------------------------------------------


class SnapshotCommand:
    """Stores the whole state before and after ``inner``; ``inner`` runs once, undo and
    redo restore a snapshot. mode "deepcopy" keeps objects, "pickle" keeps bytes."""

    def __init__(self, inner: Command, mode: str = "pickle") -> None:
        self.inner, self.mode = inner, mode
        self.description = inner.description
        self.merge_id = inner.merge_id
        self._before: Any = None
        self._after: Any = None

    def _snap(self, state: DesignState) -> Any:
        pair = (state.grid, state.diagram)
        if self.mode == "pickle":
            return pickle.dumps(pair, protocol=pickle.HIGHEST_PROTOCOL)
        return copy.deepcopy(pair)

    def _restore(self, state: DesignState, snap: Any) -> None:
        # The stored snapshot must never be the live object, or later edits change it.
        pair = pickle.loads(snap) if self.mode == "pickle" else copy.deepcopy(snap)
        state.grid, state.diagram = pair

    def apply(self, state: DesignState) -> None:
        if self._after is None:
            self._before = self._snap(state)
            self.inner.apply(state)
            self._after = self._snap(state)
        else:
            self._restore(state, self._after)

    def revert(self, state: DesignState) -> None:
        self._restore(state, self._before)

    def merge(self, other: Command) -> bool:
        if isinstance(other, SnapshotCommand) and self.inner.merge(other.inner):
            self._after = other._after
            return True
        return False

    def affected(self) -> set[str]:
        return {"*"}  # unknown: the bridge has to reset every model
