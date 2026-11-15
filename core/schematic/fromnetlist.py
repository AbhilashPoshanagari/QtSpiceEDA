"""Netlist -> Schematic (N1). Qt-free.

``schematic_from_netlist(text, library, base_dir, search_dirs)`` draws a sheet for a
netlist, so it can be seen and edited in the schematic editor. Never raises: problems
are Diagnostics, and ``schematic`` is None when nothing can be drawn.

Only a valid netlist is drawn: it goes through the same ``parse`` and ``validate`` as
the netlist editor's Check (include files found the same way), and any error stops it.

Each top-level element becomes a placed symbol, chosen in this order:

1. a symbol whose name is the element's model or subcircuit (``cmosn`` for a MOSFET
   using ``.model CMOSN``; ``opamp2`` for ``X1 ... opamp2``), in any symbol folder;
2. a symbol in the user's or the project's folders whose ``SpiceModel`` (else
   ``Value``) is that model or subcircuit;
3. a built-in symbol by kind: R res, C cap, L ind, V voltage, I current, D diode,
   Q npn/pnp and M nmos/pmos by the ``.model`` type.

A symbol is only used when its Prefix starts with the element's letter and it has one
pin per node. Elements with no symbol (E F G H, an X without a symbol, a Q with a
substrate node) stay as SPICE text on the sheet, with a warning: the circuit is
unchanged.

Every pin gets a short wire stub pointing away from the symbol, ending in a net label
named after the node (a ground flag for node 0 / gnd). Net labels with the same name
are one net, so no wires have to be routed. Parts are placed in columns by signal flow:
sources first, then each part one column right of the parts it shares a signal node
with (supply and ground nodes do not count).

Every other line is copied unchanged into one directive text below the parts
(``.include``, ``.lib``, ``.model``, ``.param``, ``.subckt`` blocks, analysis cards),
and top-level comments into one comment text under it. The title becomes the sheet's
title.

Self-check: the sheet is netlisted again with the existing netlister; its elements,
models, subcircuits and analyses must equal the original's, and every pin must be on
its node. If not, an error is reported and no sheet is returned.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.netlist.model import ERROR, GROUND_NODES, WARNING, Circuit, Diagnostic, Element
from core.netlist.parser import parse
from core.netlist.validate import validate
from core.schematic.connectivity import connect
from core.schematic.model import GroundFlag, Instance, NetLabel, Schematic, TextItem, Wire
from core.schematic.netlister import netlist_schematic
from core.symbols.asy import Symbol
from core.symbols.library import PROJECT, USER, SymbolLibrary

GRID = 16
STUB = 32  # wire from a pin to its label
MARGIN_X = 160  # room for label text between columns
MARGIN_Y = 64
MAX_ROWS = 4  # a longer column is split into several next to each other
_BUILTIN_BY_KIND = {
    "R": "res",
    "C": "cap",
    "L": "ind",
    "V": "voltage",
    "I": "current",
    "D": "diode",
}
_BUILTIN_BY_MODEL_TYPE = {
    "npn": "npn",
    "pnp": "pnp",
    "lpnp": "pnp",
    "nmos": "nmos",
    "pmos": "pmos",
}
_MODEL_KINDS = "DQMX"
_DC_VALUE = re.compile(r"^(dc\s+)?\S+$", re.IGNORECASE)


@dataclass
class FromNetlist:
    """``schematic`` is None when the netlist has errors or the self-check failed.
    ``placed`` are the elements drawn as symbols, ``kept`` the ones kept as text."""

    schematic: Schematic | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    placed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.schematic is not None


def schematic_from_netlist(
    text: str,
    library: SymbolLibrary,
    base_dir: str | Path | None = None,
    search_dirs: list[str | Path] | tuple[str | Path, ...] = (),
    require_analysis: bool = True,
) -> FromNetlist:
    """Draw ``text`` as a Schematic. See the module docstring. Never raises."""
    out = FromNetlist()
    try:
        _convert(text, library, base_dir, tuple(search_dirs), require_analysis, out)
    except Exception as exc:  # never raise on any input
        out.schematic = None
        out.diagnostics.append(Diagnostic(0, 0, ERROR, f"internal converter error: {exc!r}"))
    return out


# ---------------------------------------------------------------- conversion


@dataclass
class _Part:
    element: Element
    key: str
    symbol: Symbol


def _convert(
    text: str,
    library: SymbolLibrary,
    base_dir: str | Path | None,
    search_dirs: tuple[str | Path, ...],
    require_analysis: bool,
    out: FromNetlist,
) -> None:
    if not text.strip():
        out.diagnostics.append(Diagnostic(0, 0, ERROR, "the netlist is empty"))
        return
    parsed = parse(text, base_dir, search_dirs)
    found = parsed.diagnostics + validate(parsed.circuit, require_analysis=require_analysis)
    errors = [d for d in found if d.severity == ERROR]
    if errors:
        out.diagnostics += sorted(errors, key=lambda d: (d.line, d.col))
        out.diagnostics.append(
            Diagnostic(0, 0, ERROR, "fix the netlist errors before making a schematic")
        )
        return
    circuit = parsed.circuit
    lines = text.splitlines()

    parts: list[_Part] = []
    for element in _own_elements(circuit, lines):
        chosen = _choose_symbol(element, circuit, library)
        if chosen is None:
            out.kept.append(element.name)
            out.diagnostics.append(
                Diagnostic(element.source_line, 1, WARNING, _no_symbol_message(element))
            )
            continue
        key, symbol = chosen
        parts.append(_Part(element, key, symbol))
        out.placed.append(element.name)

    sheet = Schematic(title=" ".join(lines[0].split()) if lines else "")
    _place(parts, sheet)
    directives, comments = _other_lines(lines, {p.element.source_line for p in parts})
    bottom = _bottom(sheet, library) + 2 * MARGIN_Y
    if directives:
        sheet.texts.append(TextItem(0, bottom, "\n".join(directives), True))
        bottom += _snap_up(GRID * (len(directives) + 2))
    if comments:
        sheet.texts.append(TextItem(0, bottom, "\n".join(comments), False))

    problems = _self_check(sheet, parts, circuit, library, base_dir, search_dirs)
    if problems:
        out.diagnostics += [Diagnostic(0, 0, ERROR, p) for p in problems]
        return
    out.schematic = sheet


def _own_elements(circuit: Circuit, lines: list[str]) -> list[Element]:
    """The top-level elements written in this text (not those from an include file:
    their ``source_line`` is the include line, which does not start with their name)."""
    own: list[Element] = []
    for element in circuit.elements:
        index = element.source_line - 1
        if 0 <= index < len(lines):
            words = lines[index].split()
            if words and words[0].casefold() == element.name.casefold():
                own.append(element)
    return own


def _no_symbol_message(element: Element) -> str:
    if element.kind == "X":
        return (
            f"no symbol for {element.name} (subcircuit {element.value.split()[0]}); kept as "
            "a SPICE line. Add a symbol for it to the library folder to draw it"
        )
    return f"no symbol for {element.name}; kept as a SPICE line on the sheet"


# ---------------------------------------------------------------- symbols


def _choose_symbol(
    element: Element, circuit: Circuit, library: SymbolLibrary
) -> tuple[str, Symbol] | None:
    kind, pins = element.kind, len(element.nodes)

    def usable(key: str) -> tuple[str, Symbol] | None:
        symbol = library.lookup(key)[0]
        if symbol is None or len(symbol.pins) != pins:
            return None
        prefix = symbol.attr("Prefix").strip()
        return (key, symbol) if prefix and prefix[0].upper() == kind else None

    model = element.reference() if kind in _MODEL_KINDS else None
    if model:
        entry = library.find(model)
        if entry is not None and (hit := usable(entry.key)):
            return hit
        wanted = model.casefold()
        for entry in library.entries():
            if entry.source not in (USER, PROJECT):
                continue
            symbol = library.lookup(entry.key)[0]
            if symbol is None:
                continue
            name = symbol.attr("SpiceModel").strip() or symbol.attr("Value").strip()
            if name.casefold() == wanted and (hit := usable(entry.key)):
                return hit
    builtin = _BUILTIN_BY_KIND.get(kind)
    if kind in "QM" and model:
        types = {m.name.casefold(): m.type.casefold() for m in circuit.models}
        builtin = _BUILTIN_BY_MODEL_TYPE.get(types.get(model.casefold(), ""))
    return usable(builtin) if builtin else None


# ---------------------------------------------------------------- layout


def _snap_up(value: int) -> int:
    return -(-value // GRID) * GRID


def _snap_down(value: int) -> int:
    return (value // GRID) * GRID


def _box(symbol: Symbol) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = symbol.bbox()
    if (x1, y1, x2, y2) == (0, 0, 0, 0):
        xs = [p.x for p in symbol.pins] or [0]
        ys = [p.y for p in symbol.pins] or [0]
        return min(xs), min(ys), max(xs), max(ys)
    return x1, y1, x2, y2


def _stub_ends(symbol: Symbol) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """(pin, label point) per pin, in pin (SpiceOrder) order: the stub points away
    from the middle of the symbol, along the axis the pin is furthest out on."""
    x1, y1, x2, y2 = _box(symbol)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    ends = []
    for pin in symbol.pins:
        dx, dy = pin.x - cx, pin.y - cy
        if abs(dx) >= abs(dy) and dx != 0:
            step = (STUB if dx > 0 else -STUB, 0)
        elif dy != 0:
            step = (0, STUB if dy > 0 else -STUB)
        else:
            step = (-STUB, 0)
        ends.append(((pin.x, pin.y), (pin.x + step[0], pin.y + step[1])))
    return ends


def _extent(symbol: Symbol) -> tuple[int, int, int, int]:
    """The symbol's box grown by its stubs, on the grid."""
    x1, y1, x2, y2 = _box(symbol)
    for _, (ex, ey) in _stub_ends(symbol):
        x1, y1, x2, y2 = min(x1, ex), min(y1, ey), max(x2, ex), max(y2, ey)
    return _snap_down(x1), _snap_down(y1), _snap_up(x2), _snap_up(y2)


def _columns(parts: list[_Part]) -> list[list[_Part]]:
    """Sources in column 0; every other part one column right of the nearest part it
    shares a signal node with. Ground and DC-supply nodes are not signal nodes."""
    ground = {n.casefold() for n in GROUND_NODES}
    supply: set[str] = set()
    for p in parts:
        e = p.element
        if e.kind == "V" and len(e.nodes) == 2 and _DC_VALUE.match(e.value.strip() or "0"):
            a, b = (n.casefold() for n in e.nodes)
            if b in ground and a not in ground:
                supply.add(a)
            elif a in ground and b not in ground:
                supply.add(b)
    skip = ground | supply

    def signal_nodes(p: _Part) -> set[str]:
        return {n.casefold() for n in p.element.nodes} - skip

    column: dict[int, int] = {}
    frontier = [i for i, p in enumerate(parts) if p.element.kind in "VI"]
    for i in frontier:
        column[i] = 0
    depth = 0
    while frontier:
        depth += 1
        nodes = set().union(*(signal_nodes(parts[i]) for i in frontier))
        frontier = [i for i, p in enumerate(parts) if i not in column and signal_nodes(p) & nodes]
        for i in frontier:
            column[i] = depth
    last = max(column.values(), default=-1)
    for i in range(len(parts)):
        column.setdefault(i, last + 1)
    count = max(column.values(), default=-1) + 1
    cols: list[list[_Part]] = [[] for _ in range(count)]
    for i, p in enumerate(parts):
        cols[column[i]].append(p)
    if cols:  # supply sources at the top of column 0
        cols[0].sort(key=lambda p: (not _is_supply(p, supply), parts.index(p)))
    split: list[list[_Part]] = []
    for c in cols:
        split += [c[i : i + MAX_ROWS] for i in range(0, len(c), MAX_ROWS)]
    return split


def _is_supply(part: _Part, supply: set[str]) -> bool:
    return part.element.kind == "V" and any(n.casefold() in supply for n in part.element.nodes)


def _place(parts: list[_Part], sheet: Schematic) -> None:
    if not parts:
        return
    extents = {id(p): _extent(p.symbol) for p in parts}
    cell_w = max(x2 - x1 for x1, _, x2, _ in extents.values()) + MARGIN_X
    cell_h = max(y2 - y1 for _, y1, _, y2 in extents.values()) + MARGIN_Y
    for col, column in enumerate(_columns(parts)):
        for row, part in enumerate(column):
            x1, y1, _, _ = extents[id(part)]
            ox, oy = col * cell_w - x1, row * cell_h - y1
            inst = Instance(part.key, part.element.name, ox, oy, attrs=_attrs(part.element))
            sheet.instances.append(inst)
            for (px, py), (ex, ey) in _stub_ends(part.symbol):
                sheet.wires.append(Wire(ox + px, oy + py, ox + ex, oy + ey))
            for ((_, _), (ex, ey)), node in zip(
                _stub_ends(part.symbol), part.element.nodes, strict=True
            ):
                if node.casefold() in {n.casefold() for n in GROUND_NODES}:
                    sheet.grounds.append(GroundFlag(ox + ex, oy + ey))
                else:
                    sheet.labels.append(NetLabel(ox + ex, oy + ey, node))


def _attrs(element: Element) -> dict[str, str]:
    """Attributes that make the netlister write ``element`` back as it was. Every key
    the netlister reads is set, so a symbol's own defaults never leak in."""
    params = " ".join(f"{k}={v}" for k, v in element.params.items())
    return {
        "Value": element.value,
        "SpiceModel": "",
        "Value2": "",
        "SpiceLine": params,
        "SpiceLine2": "",
        "ModelFile": "",  # the netlist's own .include lines are kept as directives
    }


def _bottom(sheet: Schematic, library: SymbolLibrary) -> int:
    ys = [w.y1 for w in sheet.wires] + [w.y2 for w in sheet.wires]
    for inst in sheet.instances:
        symbol = library.lookup(inst.symbol)[0]
        if symbol is not None:
            ys.append(inst.y + _box(symbol)[3])
    return _snap_up(max(ys, default=0))


# ---------------------------------------------------------------- other lines


def _other_lines(lines: list[str], placed_lines: set[int]) -> tuple[list[str], list[str]]:
    """(directive lines, comment lines) of the text, without the title, ``.end`` and
    the lines of the placed elements (with their ``+`` continuations)."""
    directives: list[str] = []
    comments: list[str] = []
    depth = 0
    dropping = False
    for number, raw in enumerate(lines[1:], start=2):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("+"):
            if not dropping:
                directives.append(stripped)
            continue
        if stripped.startswith("*"):
            (directives if depth else comments).append(stripped)
            continue
        keyword = stripped.split()[0].casefold()
        if depth == 0 and keyword == ".end":
            break
        dropping = depth == 0 and number in placed_lines
        if dropping:
            continue
        if keyword == ".subckt":
            depth += 1
        elif keyword == ".ends":
            depth = max(0, depth - 1)
        directives.append(stripped)
    return directives, comments


# ---------------------------------------------------------------- self-check


def _norm_node(node: str) -> str:
    folded = node.casefold()
    return "0" if folded in {n.casefold() for n in GROUND_NODES} else folded


def _element_key(e: Element) -> tuple:
    return (
        e.kind,
        tuple(_norm_node(n) for n in e.nodes),
        " ".join(e.value.split()).casefold(),
        tuple(sorted((k.casefold(), v.casefold()) for k, v in e.params.items())),
    )


def _self_check(
    sheet: Schematic,
    parts: list[_Part],
    circuit: Circuit,
    library: SymbolLibrary,
    base_dir: str | Path | None,
    search_dirs: tuple[str | Path, ...],
) -> list[str]:
    lookup: Callable[[str], Symbol | None] = lambda name: library.lookup(name)[0]  # noqa: E731
    problems: list[str] = []
    nets = connect(sheet, lookup)
    by_name = {p.element.name.casefold(): p for p in parts}
    for inst, pins in zip(sheet.instances, nets.pins, strict=True):
        part = by_name[inst.name.casefold()]
        want = [_norm_node(n) for n in part.element.nodes]
        got = [_norm_node(n) for n in pins]
        if got != want:
            problems.append(f"{inst.name}: pins drawn on {got}, netlist has {want}")
    made = netlist_schematic(sheet, lookup, base_dir, search_dirs)
    if not made.ok:
        problems += [d.message for d in made.diagnostics if d.severity == ERROR]
        return [f"the drawn schematic does not netlist: {p}" for p in problems]
    before = {e.name.casefold(): _element_key(e) for e in circuit.elements}
    after = {e.name.casefold(): _element_key(e) for e in made.circuit.elements}
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) != after.get(name):
            problems.append(f"{name}: {before.get(name)} became {after.get(name)}")

    def models(c: Circuit) -> set[tuple[str, str]]:
        return {(m.name.casefold(), m.type.casefold()) for m in c.models}

    if models(circuit) != models(made.circuit):
        problems.append("the .model cards differ")
    if {s.name.casefold() for s in circuit.subcircuits} != {
        s.name.casefold() for s in made.circuit.subcircuits
    }:
        problems.append("the .subckt blocks differ")
    if [(a.kind, a.args) for a in circuit.analyses] != [
        (a.kind, a.args) for a in made.circuit.analyses
    ]:
        problems.append("the analysis cards differ")
    return [f"the drawn schematic does not match the netlist: {p}" for p in problems]
