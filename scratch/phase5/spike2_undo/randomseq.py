"""S2 spike: a seeded random edit sequence over a DesignState (scratch only).

Each step is built from the *current* state (like the UI: you can only move or delete
what exists), so the generator and the command application run together.
A step is one user action: add load, drag (one MoveGlyph per mouse-move event, all with
the same gesture id), set property, or delete bus.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

from commands import Command, MoveGlyph, SetProperty, add_load, delete_bus
from state import DesignState

WEIGHTS = {"add_load": 0.25, "drag": 0.35, "set_property": 0.33, "delete_bus": 0.07}


@dataclass
class Step:
    kind: str
    commands: list[Command]
    """One command, or the mouse-move events of a drag (they merge into one)."""


def _set_property(state: DesignState, rng: random.Random) -> Command | None:
    g = state.grid
    options: list[tuple[str, str, object]] = []
    if g.loads:
        ld = g.loads[rng.choice(list(g.loads))]
        options += [(ld.id, "kw", round(rng.uniform(1, 500), 3)), (ld.id, "kvar", rng.random())]
    if g.lines:
        ln = g.lines[rng.choice(list(g.lines))]
        options.append((ln.id, "length_m", round(rng.uniform(10, 2000), 1)))
    if g.switches:
        sw = g.switches[rng.choice(list(g.switches))]
        options.append((sw.id, "closed", not sw.closed))
    if g.capacitors:
        c = g.capacitors[rng.choice(list(g.capacitors))]
        options.append((c.id, "kvar", rng.choice([50.0, 100.0, 200.0])))
    if g.transformers:
        tx = g.transformers[rng.choice(list(g.transformers))]
        options.append((tx.id, "windings.1.tap", 1.0 + rng.randint(-16, 16) * 0.00625))
    if not options:
        return None
    eid, path, value = rng.choice(options)
    return SetProperty(eid, path, value)


def steps(state: DesignState, seed: int, n: int, max_drag_events: int = 1) -> Iterator[Step]:
    """Yield ``n`` steps; the caller applies each step before asking for the next.
    ``max_drag_events`` = 1 gives one MoveGlyph per drag (core-only runs)."""
    rng = random.Random(seed)
    kinds, weights = list(WEIGHTS), list(WEIGHTS.values())
    gesture = 0
    made = 0
    while made < n:
        kind = rng.choices(kinds, weights)[0]
        g, d = state.grid, state.diagram
        cmds: list[Command] = []
        if kind == "add_load" and g.loads and g.buses:
            template = g.loads[rng.choice(list(g.loads))]
            cmds = [add_load(state, rng.choice(list(g.buses)), template)]
        elif kind == "drag" and (d.bus_glyphs or d.element_glyphs):
            keys = [f"bus.{b}" for b in d.bus_glyphs] + list(d.element_glyphs)
            gid = rng.choice(keys)
            gesture += 1
            events = rng.randint(1, max_drag_events)
            dx, dy = rng.randint(-20, 20), rng.randint(-20, 20)
            glyph = d.bus_glyphs[gid[4:]] if gid.startswith("bus.") else d.element_glyphs[gid]
            x0, y0 = glyph.x, glyph.y
            cmds = [
                MoveGlyph(gid, x0 + dx * (k + 1) / events, y0 + dy * (k + 1) / events, gesture)
                for k in range(events)
            ]
        elif kind == "set_property":
            c = _set_property(state, rng)
            cmds = [c] if c is not None else []
        elif kind == "delete_bus":
            buses = [b for b in g.buses if all(s.bus1.bus != b for s in g.sources.values())]
            if buses:
                cmds = [delete_bus(state, rng.choice(buses))]
        if cmds:
            made += 1
            yield Step(kind, cmds)
