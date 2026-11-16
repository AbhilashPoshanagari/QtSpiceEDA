"""S2 spike: the state that designer commands edit (scratch only; Qt-free).

``DesignState`` = a GridModel (a copy of the real v0.4 class) plus a toy DiagramModel
(presentation only, rule 20: positions, orientation, rotation, label offsets; never
which bus an element connects to). ``check_consistency`` is the rule 21 check: every
bus and every glyph-bearing element has exactly one glyph, every glyph names an
existing element, and every terminal names an existing bus.

Nothing in app/, bridge/ or core/ imports this.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.grid.model import (
    WYE,
    Bus,
    Capacitor,
    GridModel,
    Line,
    LineCode,
    Load,
    PVSystem,
    Regulator,
    Source,
    Switch,
    Terminal,
    Transformer,
    Winding,
)

ROOT = Path(__file__).resolve().parents[3]
IEEE123 = ROOT / "tests" / "fixtures" / "grid" / "ieee123"

COORD_PER_UNIT = 25.0  # IEEE 123 coordinates are multiples of 25 ft (S1)


@dataclass
class BusGlyph:
    x: float
    y: float
    orientation: int = 0
    length: float = 2.0


@dataclass
class ElementGlyph:
    x: float
    y: float
    rotation: int = 0
    label_dx: float = 0.0
    label_dy: float = 0.0


@dataclass
class DiagramModel:
    bus_glyphs: dict[str, BusGlyph] = field(default_factory=dict)
    """Keyed by bus name."""
    element_glyphs: dict[str, ElementGlyph] = field(default_factory=dict)
    """Keyed by element id ("load.l1")."""


@dataclass
class DesignState:
    grid: GridModel
    diagram: DiagramModel


# GridModel dicts whose elements have a glyph, and their element-id prefix. Switch shares
# "line." with Line (OpenDSS class), so an id needs both dicts to resolve.
GLYPHED: tuple[tuple[str, str], ...] = (
    ("sources", "vsource"),
    ("lines", "line"),
    ("switches", "line"),
    ("transformers", "transformer"),
    ("loads", "load"),
    ("capacitors", "capacitor"),
    ("pvsystems", "pvsystem"),
)
# Library entries / controls without a glyph of their own in this toy (open for D3).
UNGLYPHED: tuple[tuple[str, str], ...] = (("linecodes", "linecode"), ("regulators", "regcontrol"))
ALL_DICTS = ("buses",) + tuple(a for a, _ in GLYPHED + UNGLYPHED)


def resolve(grid: GridModel, element_id: str) -> tuple[str, str]:
    """Element id -> (GridModel dict attribute, name). Raises KeyError if unknown."""
    if element_id.startswith("bus."):
        name = element_id[4:]
        if name in grid.buses:
            return "buses", name
        raise KeyError(element_id)
    prefix, _, name = element_id.partition(".")
    for attr, cls in GLYPHED + UNGLYPHED:
        if cls == prefix and name in getattr(grid, attr):
            return attr, name
    raise KeyError(element_id)


def terminal_buses(element: Any) -> list[str]:
    if isinstance(element, (Source, Line, Switch)):
        return [element.bus1.bus, element.bus2.bus]
    if isinstance(element, Transformer):
        return [w.terminal.bus for w in element.windings]
    if isinstance(element, (Load, Capacitor, PVSystem)):
        return [element.terminal.bus]
    return []


def glyphed_ids(grid: GridModel) -> list[str]:
    return [e.id for attr, _ in GLYPHED for e in getattr(grid, attr).values()]


def check_consistency(state: DesignState) -> list[str]:
    """Rule 21 plus dangling references. Empty list = consistent."""
    g, d = state.grid, state.diagram
    errors: list[str] = []
    if set(d.bus_glyphs) != set(g.buses):
        errors.append(f"bus glyphs differ: {sorted(set(d.bus_glyphs) ^ set(g.buses))}")
    ids = glyphed_ids(g)
    if len(ids) != len(set(ids)):
        errors.append("duplicate element id")
    if set(d.element_glyphs) != set(ids):
        errors.append(f"element glyphs differ: {sorted(set(d.element_glyphs) ^ set(ids))}")
    for attr, _ in GLYPHED:
        for e in getattr(g, attr).values():
            for b in terminal_buses(e):
                if b not in g.buses:
                    errors.append(f"{e.id}: bus {b} missing")
    for ln in g.lines.values():
        if ln.linecode not in g.linecodes:
            errors.append(f"{ln.id}: linecode {ln.linecode} missing")
    for rg in g.regulators.values():
        if rg.transformer not in g.transformers:
            errors.append(f"{rg.id}: transformer {rg.transformer} missing")
    return errors


def order(state: DesignState) -> tuple[tuple[str, ...], ...]:
    """Key order of every dict. == on dataclasses ignores dict order; exact undo should
    also keep it (it decides the order of the written DSS and of the .qgrid)."""
    g, d = state.grid, state.diagram
    return tuple(tuple(getattr(g, a)) for a in ALL_DICTS) + (
        tuple(d.bus_glyphs),
        tuple(d.element_glyphs),
    )


def diagram_from_grid(grid: GridModel) -> DiagramModel:
    """Bus glyphs from bus coordinates (S1 scaling), element glyphs next to their bus."""
    d = DiagramModel()
    for i, (name, bus) in enumerate(grid.buses.items()):
        if bus.x is not None and bus.y is not None:
            x, y = round(bus.x / COORD_PER_UNIT), -round(bus.y / COORD_PER_UNIT)
            d.bus_glyphs[name] = BusGlyph(x, y)
        else:
            d.bus_glyphs[name] = BusGlyph(float(i), 0.0)  # S3 does real layout
    for attr, _ in GLYPHED:
        for e in getattr(grid, attr).values():
            b = d.bus_glyphs[terminal_buses(e)[0]]
            d.element_glyphs[e.id] = ElementGlyph(b.x, b.y + 2.0)
    return d


def load_ieee123() -> tuple[DesignState, float]:
    """IEEE 123 extracted through OpenDSSEngine (in-process: spike only, rule 5)."""
    from core.grid.engines.opendss.engine import OpenDSSEngine

    t0 = time.perf_counter()
    engine = OpenDSSEngine()
    diags = engine.load_native(IEEE123 / "IEEE123Master.dss")
    if diags:
        raise RuntimeError(diags)
    engine.context.Text.Command = f'BusCoords "{(IEEE123 / "BusCoords.dat").resolve()}"'
    engine.run()
    model, diags = engine.extract_model()
    engine.close()
    if model is None:
        raise RuntimeError(diags)
    return DesignState(model, diagram_from_grid(model)), time.perf_counter() - t0


def mini_state() -> DesignState:
    """A small hand-made feeder for tests without dss-python. The values are arbitrary
    spike test data, not a reference of any kind."""
    t3 = (1, 2, 3)
    g = GridModel(name="mini", base_frequency=50.0, voltage_bases=[11.0, 0.4])
    for i, n in enumerate(["src", "b1", "b2", "b3", "b4", "lv"]):
        g.buses[n] = Bus(n, 6.35 if n != "lv" else 0.23, x=25.0 * i, y=0.0)
    g.sources["source"] = Source(
        "source", Terminal("src", t3), Terminal("src", (0, 0, 0)), 3, 11.0, 1.0, 0.0,
        0.1, 1.0, 0.3, 3.0,
    )  # fmt: skip
    z = tuple(0.3 if i % 4 == 0 else 0.1 for i in range(9))
    g.linecodes["lc1"] = LineCode("lc1", 3, z, z, tuple(10.0 for _ in range(9)), 300.0, 400.0)
    for name, a, b in [("l1", "src", "b1"), ("l2", "b1", "b2"), ("l3", "b2", "b3")]:
        g.lines[name] = Line(name, Terminal(a, t3), Terminal(b, t3), 3, 500.0, "lc1")
    g.switches["sw1"] = Switch("sw1", Terminal("b3", t3), Terminal("b4", t3), 3, True, 1e-4)
    g.transformers["tx1"] = Transformer(
        "tx1", 3,
        [Winding(Terminal("b2", t3), "delta", 11.0, 500.0, 0.5),
         Winding(Terminal("lv", t3), WYE, 0.4, 500.0, 0.5)],
        4.0, 4.0, 4.0,
    )  # fmt: skip
    g.regulators["reg1"] = Regulator("reg1", "tx1", 2, 120.0, 2.0, 20.0, 700.0, 3.0, 9.0)
    g.loads["ld1"] = Load("ld1", Terminal("b1", t3), 3, WYE, 11.0, 100.0, 30.0, "pq")
    g.loads["ld2"] = Load("ld2", Terminal("lv", (1,)), 1, WYE, 0.23, 10.0, 3.0, "pq")
    g.capacitors["c1"] = Capacitor("c1", Terminal("b3", t3), 3, WYE, 11.0, 300.0)
    g.pvsystems["pv1"] = PVSystem("pv1", Terminal("b4", t3), 3, WYE, 11.0, 100.0, 90.0, 1.0, 1.0)
    return DesignState(g, diagram_from_grid(g))
