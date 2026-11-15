"""DiagramModel: where a grid design's glyphs are drawn. Qt-free, presentation only.

Rule 20: GridModel is the single source of electrical truth, connectivity included.
A DiagramModel holds positions, rotation, bus-bar length and label offsets, and never
which bus an element connects to; every line on the canvas is derived from GridModel
terminals at render time.

Rule 21: ``bus_glyphs`` is keyed by bus name ("671", CLAUDE.md "Diagram model"),
``element_glyphs`` by element id ("load.671", "line.650632"). Every bus and every drawn
element has exactly one glyph, and every glyph names an existing bus or element
(``check_consistency``).

Drawn elements are the classes in ``GLYPH_CLASSES``: sources, lines, switches,
transformers, loads, capacitors and PV systems. LineCodes (data that lines refer to)
and RegControls (a control drawn with its transformer) get no glyph of their own.
A line and a switch share the id "line.<name>", so they must not share a name.

Canvas units are diagram units (1 = one grid step), y down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from core.grid.model import GridModel
from core.netlist.model import ERROR
from core.physical.component import NetworkDiagnostic

ORIENTATIONS = (0, 90)
ROTATIONS = (0, 90, 180, 270)

GLYPH_CLASSES = ("sources", "lines", "switches", "transformers", "loads", "capacitors",
                 "pvsystems")  # fmt: skip
"""GridModel class dicts whose elements have an ElementGlyph."""

SHUNT_CLASSES = ("sources", "loads", "capacitors", "pvsystems")
BRANCH_CLASSES = ("lines", "switches", "transformers")

# stable diagnostic codes
MISSING_GLYPH = "missing_glyph"
ORPHAN_GLYPH = "orphan_glyph"
SHARED_ID = "shared_id"
BAD_GLYPH = "bad_glyph"

CODES = (MISSING_GLYPH, ORPHAN_GLYPH, SHARED_ID, BAD_GLYPH)


@dataclass
class BusGlyph:
    x: float
    y: float
    orientation: int = 0
    """0 = horizontal bus bar, 90 = vertical."""
    length: float = 2.0
    """Bus-bar length in diagram units."""


@dataclass
class ElementGlyph:
    x: float
    y: float
    """Shunts: the symbol position. Branches: only the symbol/label anchor; the branch
    itself is drawn between its bus glyphs."""
    rotation: int = 0
    """0 / 90 / 180 / 270."""
    label_dx: float = 0.0
    label_dy: float = 0.0


@dataclass
class DiagramModel:
    bus_glyphs: dict[str, BusGlyph] = field(default_factory=dict)
    """Bus name -> glyph."""
    element_glyphs: dict[str, ElementGlyph] = field(default_factory=dict)
    """Element id -> glyph."""


def drawn_element_ids(grid: GridModel) -> list[str]:
    """Ids of every element that needs a glyph, in GridModel order (a line and a switch
    with the same name give the id twice)."""
    return [el.id for name in GLYPH_CLASSES for el in getattr(grid, name).values()]


def _finite(*values: object) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values
    )


def check_consistency(grid: GridModel, diagram: DiagramModel) -> list[NetworkDiagnostic]:
    """Rule 21: every bus and drawn element has exactly one glyph, every glyph names an
    existing bus or element, and glyph values are usable. [] = consistent. Never
    raises."""
    out: list[NetworkDiagnostic] = []

    def err(loc: str, msg: str, code: str) -> None:
        out.append(NetworkDiagnostic(loc, ERROR, msg, code=code))

    for name in grid.buses:
        if name not in diagram.bus_glyphs:
            err(f"bus.{name}", "bus has no glyph", MISSING_GLYPH)
    for name, g in diagram.bus_glyphs.items():
        loc = f"bus.{name}"
        if name not in grid.buses:
            err(loc, "glyph names a bus that does not exist", ORPHAN_GLYPH)
        if not _finite(g.x, g.y, g.length) or g.length <= 0:
            err(loc, "glyph position or length is not a finite number > 0", BAD_GLYPH)
        if g.orientation not in ORIENTATIONS:
            err(loc, f"bus orientation {g.orientation!r} is not 0 or 90", BAD_GLYPH)

    ids = drawn_element_ids(grid)
    seen: set[str] = set()
    for el_id in ids:
        if el_id in seen:
            err(el_id, "a line and a switch share this id; one of them needs a new name",
                SHARED_ID)  # fmt: skip
            continue
        seen.add(el_id)
        if el_id not in diagram.element_glyphs:
            err(el_id, "element has no glyph", MISSING_GLYPH)
    for el_id, g in diagram.element_glyphs.items():
        if el_id not in seen:
            err(el_id, "glyph names an element that does not exist or is not drawn",
                ORPHAN_GLYPH)  # fmt: skip
        if not _finite(g.x, g.y, g.label_dx, g.label_dy):
            err(el_id, "glyph position or label offset is not a finite number", BAD_GLYPH)
        if g.rotation not in ROTATIONS:
            err(el_id, f"rotation {g.rotation!r} is not 0, 90, 180 or 270", BAD_GLYPH)
    return out
