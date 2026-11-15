"""Schematic -> SPICE. Qt-free.

``netlist_schematic`` (S3) turns a whole Schematic into a Circuit and a deck:

1. ``connect`` (S2) finds the net of every pin;
2. each instance becomes one SPICE line through ``netlist_part`` (L7, below);
3. ModelFile includes (each once), the part lines and the directive texts are put
   into one netlist text, title first (the schematic title, or "QtSpice schematic");
4. that text goes through the same ``parse`` and ``validate`` as the netlist editor,
   and the deck is made by the same ``build``.

So a schematic is only another way to write a netlist (rule 8): ``text`` can be handed
to the Simulator unchanged. Diagnostics name the part or directive they come from.

One placed symbol -> one SPICE element line (L7):

    <letter><InstName> <nets in SpiceOrder> <model or value> [Value2] [SpiceLine] [SpiceLine2]

* The device letter is the first letter of the symbol's Prefix (``MN`` -> ``M``). It
  is put in front of the InstName unless the name already starts with it, so ``R1``
  stays ``R1`` and ``load`` becomes ``Rload``.
* X, D, Q, M: the model or subcircuit name is ``SpiceModel`` when set, else ``Value``.
  Other devices use ``Value``.
* A ``ModelFile`` becomes an ``.include``; a relative one that exists next to the
  symbol file is made absolute.
* The micro sign in values is written as ``u`` (as LTspice does).
* ``ground`` and ``label`` symbols (no Prefix) only name nets: no line.
* Prefix A (LTspice special-function devices) and unknown prefixes give a clear
  "not supported by ngspice" error, never a line.

The line is read back through the netlist parser, so the Element (and the checks on
values and node counts) are exactly the ones the netlist editor uses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.netlist.builder import build
from core.netlist.model import ERROR, Circuit, Diagnostic, Element
from core.netlist.parser import ELEMENT_KINDS, FIX_INSERT_TITLE, parse
from core.netlist.validate import validate
from core.schematic.connectivity import NetMap, connect
from core.schematic.model import PartInstance, Schematic
from core.symbols.asy import Symbol

NET_FLAG_SYMBOLS = frozenset({"ground", "label"})
DEFAULT_TITLE = "QtSpice schematic"
# Valid SPICE letters that ngspice has but QtSpice's netlist does not handle yet.
_QTSPICE_UNSUPPORTED = "BJKNOPSTUWYZ"
_MODEL_KINDS = "XDQM"
_MICRO = str.maketrans({"\u00b5": "u", "\u03bc": "u"})


@dataclass
class NetlistedPart:
    """Result for one part. ``element`` is None when no line is made."""

    part: str
    line: str = ""
    element: Element | None = None
    includes: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)


def netlist_part(part: PartInstance) -> NetlistedPart:
    """The SPICE line, Element and includes for ``part``. Never raises."""
    out = NetlistedPart(part.name)

    def error(message: str) -> NetlistedPart:
        out.diagnostics.append(Diagnostic(0, 0, ERROR, f"{part.name}: {message}"))
        return out

    symbol = part.symbol
    prefix = part.attr("Prefix").strip()
    if not prefix:
        if symbol.name.casefold() in NET_FLAG_SYMBOLS:
            return out
        return error(f"symbol {symbol.name!r} has no Prefix, so it cannot be netlisted")
    letter = prefix[0].upper()
    if letter == "A":
        return error(
            f"Prefix {prefix}: LTspice special-function (A) devices are not supported by ngspice"
        )
    if letter in _QTSPICE_UNSUPPORTED:
        return error(f"Prefix {prefix}: {letter} devices are not supported by QtSpice yet")
    if letter not in ELEMENT_KINDS:
        return error(f"unknown Prefix {prefix!r}: not supported by ngspice")

    name = part.name.strip()
    if not name or any(c.isspace() for c in name):
        return error("InstName must be one word")
    if name[0].upper() != letter:
        name = letter + name

    pins = symbol.pins
    if len(part.nets) != len(pins):
        return error(f"has {len(part.nets)} nets for {len(pins)} pins")
    for pin, net in zip(pins, part.nets, strict=True):
        if not net.strip():
            return error(f"pin {pin.name or pin.index + 1} is not connected")

    if letter in _MODEL_KINDS:
        model = part.attr("SpiceModel").strip() or part.attr("Value").strip()
        if not model:
            what = "subcircuit" if letter == "X" else "model"
            return error(f"no {what} name: set SpiceModel or Value")
        words = [model]
    else:
        words = [part.attr("Value")]
    words += [part.attr(key) for key in ("Value2", "SpiceLine", "SpiceLine2")]
    tail = " ".join(w.strip() for w in words if w.strip()).translate(_MICRO)
    out.line = " ".join([name, *(n.strip() for n in part.nets), tail]).strip()

    model_file = part.attr("ModelFile").strip()
    if model_file:
        out.includes.append(_include(model_file, symbol.path))

    result = parse(f"* {part.name}\n{out.line}\n")
    out.diagnostics += [
        Diagnostic(0, 0, d.severity, f"{part.name}: {d.message}") for d in result.diagnostics
    ]
    if result.circuit.elements:
        out.element = result.circuit.elements[0]
        out.element.source_line = 0
    return out


def _include(model_file: str, symbol_path: Path | None) -> str:
    path = Path(model_file.strip("\"'"))
    if not path.is_absolute() and symbol_path is not None:
        beside = symbol_path.parent / path
        if beside.is_file():
            path = beside
    text = str(path)
    return f'.include "{text}"' if any(c.isspace() for c in text) else f".include {text}"


@dataclass
class NetlistedSchematic:
    """``text`` is the netlist (title first, ``.end`` last); ``deck`` is ``build(circuit)``
    when there are no errors, else ""."""

    text: str = ""
    circuit: Circuit = field(default_factory=Circuit)
    deck: str = ""
    nets: NetMap = field(default_factory=NetMap)
    parts: list[NetlistedPart] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)


def netlist_schematic(
    schematic: Schematic,
    lookup: Callable[[str], Symbol | None],
    base_dir: str | Path | None = None,
    search_dirs: list[str | Path] | tuple[str | Path, ...] = (),
) -> NetlistedSchematic:
    """Netlist ``schematic``. ``lookup`` gives the Symbol for a symbol name; ``base_dir``
    resolves relative include paths, then the library folders ``search_dirs`` (N0, as
    in ``parse``). Never raises."""
    out = NetlistedSchematic()
    try:
        _netlist(schematic, lookup, base_dir, out, tuple(search_dirs))
    except Exception as exc:  # never raise on a bad schematic
        out.diagnostics.append(Diagnostic(0, 0, ERROR, f"internal netlister error: {exc!r}"))
        out.deck = ""
    return out


def _netlist(
    schematic: Schematic,
    lookup: Callable[[str], Symbol | None],
    base_dir: str | Path | None,
    out: NetlistedSchematic,
    search_dirs: tuple[str | Path, ...] = (),
) -> None:
    out.nets = connect(schematic, lookup)
    out.diagnostics += out.nets.diagnostics

    title = " ".join(schematic.title.split()) or DEFAULT_TITLE
    lines: list[str] = [title]
    origin: list[str] = ["schematic"]  # what each line of ``text`` comes from
    part_lines: set[int] = set()
    includes: dict[str, list[str]] = {}
    body: list[tuple[str, str]] = []
    for inst, nets in zip(schematic.instances, out.nets.pins, strict=True):
        symbol = lookup(inst.symbol)
        if symbol is None:
            continue  # connect() already reported it
        part = netlist_part(PartInstance(inst.name, symbol, list(nets), dict(inst.attrs)))
        out.parts.append(part)
        out.diagnostics += part.diagnostics
        for include in part.includes:
            includes.setdefault(include, []).append(inst.name)
        if part.line and part.ok:
            body.append((part.line, inst.name))
    for include, users in includes.items():
        lines.append(include)
        origin.append(", ".join(users))
    for line, name in body:
        part_lines.add(len(lines))
        lines.append(line)
        origin.append(name)
    for i, text in enumerate(t for t in schematic.texts if t.directive):
        for line in text.text.splitlines():
            if line.strip():
                lines.append(line.strip())
                origin.append(f"directive {i + 1}")
    lines.append(".end")
    origin.append("end")
    out.text = "\n".join(lines) + "\n"

    result = parse(out.text, base_dir, search_dirs)
    out.circuit = result.circuit
    found = result.diagnostics + validate(result.circuit, require_analysis=False)
    for d in sorted(found, key=lambda d: (d.line, d.col)):
        index = d.line - 1
        if d.fix == FIX_INSERT_TITLE:
            continue  # the title is generated, not typed
        if index in part_lines and d in result.diagnostics:
            continue  # netlist_part already reported what parse says about its line
        where = origin[index] if 0 <= index < len(origin) else "schematic"
        out.diagnostics.append(Diagnostic(0, 0, d.severity, f"{where}: {d.message}"))
    out.deck = build(out.circuit) if out.ok else ""
