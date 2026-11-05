"""Schematic -> SPICE. Qt-free.

First part (L7): one placed symbol -> one SPICE element line.

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

from dataclasses import dataclass, field
from pathlib import Path

from core.netlist.model import ERROR, Diagnostic, Element
from core.netlist.parser import ELEMENT_KINDS, parse
from core.schematic.model import PartInstance

NET_FLAG_SYMBOLS = frozenset({"ground", "label"})
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
