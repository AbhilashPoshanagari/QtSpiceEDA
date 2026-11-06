"""Circuit symbols (H2): a schematic or netlist becomes ``<name>.sub`` + ``<name>.asy``.
Qt-free.

``SymbolGenerator.from_schematic`` netlists the sheet with the existing netlister (rule
8) and takes its port flags as the pins; ``SymbolGenerator.from_netlist`` takes a
netlist text and the nodes the user ticked. Both return a ``GeneratedSymbol`` with the
two file texts and Diagnostics, and never raise; ``write(folder)`` saves the files.

``<name>.sub``::

    * QtSpice circuit symbol <name> ...      (header; marks the file as generated)
    .include / .lib lines and .subckt blocks the circuit already uses
    .subckt <name> <port nodes in pin order>
    <part lines, .param and .model cards>
    .ends <name>

Includes and existing ``.subckt`` blocks go before ours because nested ``.subckt`` is
not supported. The title, analysis cards and ``.end`` are left out; other cards are
left out with a warning. One level only: a circuit that already uses a generated
circuit symbol cannot become one.

``<name>.asy`` (LTspice format, Windows-1252, CR+LF): ``SymbolType BLOCK``, one
rectangle on the 16-unit grid, inputs on the left, outputs on the right,
bidirectional ports on top, 32 units apart, each with a short stub. ``Prefix X``,
``SpiceModel <name>``, ``Value <name>``, ``ModelFile <name>.sub``; every pin has
``PinName`` and ``SpiceOrder`` (the ``.subckt`` order: inputs, outputs, bidir).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.hierarchy.ports import Port, netlist_ports, schematic_ports
from core.netlist.model import ERROR, WARNING, Diagnostic
from core.netlist.parser import FIX_INSERT_TITLE, parse
from core.netlist.validate import validate
from core.schematic.model import Schematic
from core.schematic.netlister import netlist_schematic
from core.symbols.asy import ENCODING, Symbol

SUB_SUFFIX = ".sub"
ASY_SUFFIX = ".asy"
SUB_HEADER = "* QtSpice circuit symbol"
DESCRIPTION_MARK = "(made by QtSpice)"
GRID = 16
PIN_PITCH = 32
STUB = 16
CHAR = 8  # approximate width of a pin-name character, sheet units

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*\Z")
_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)
_ANALYSES = frozenset({".op", ".dc", ".ac", ".tran"})
_HEAD_CARDS = frozenset({".include", ".inc", ".lib"})
_BODY_CARDS = frozenset({".param", ".model"})


def is_generated(symbol: Symbol) -> bool:
    """True for a circuit symbol made by ``SymbolGenerator``."""
    return symbol.attr("Description").endswith(DESCRIPTION_MARK)


def symbol_name_problem(name: str) -> str:
    """Why ``name`` cannot name a circuit symbol ("" when it can). It becomes a file
    name, a ``.subckt`` name and a symbol key, so it is kept to letters, digits, ``_``
    and ``-``, starting with a letter or ``_``."""
    if not name:
        return "give the symbol a name"
    if not _NAME_RE.match(name):
        return "use letters, digits, _ and -, starting with a letter"
    if len(name) > 64:
        return "the name is longer than 64 characters"
    if name.casefold() in _RESERVED:
        return f"{name} is reserved by Windows"
    return ""


@dataclass
class GeneratedSymbol:
    name: str
    ports: list[Port] = field(default_factory=list)  # pin / .subckt order
    sub_text: str = ""
    asy_text: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.sub_text) and not any(d.severity == ERROR for d in self.diagnostics)

    def paths(self, folder: str | Path) -> tuple[Path, Path]:
        folder = Path(folder)
        return folder / f"{self.name}{SUB_SUFFIX}", folder / f"{self.name}{ASY_SUFFIX}"

    def write(self, folder: str | Path) -> list[Diagnostic]:
        """Save both files into ``folder``; problems are returned, never raised."""
        if not self.ok:
            return [Diagnostic(0, 0, ERROR, "the symbol has errors; nothing was written")]
        sub, asy = self.paths(folder)
        try:
            sub.write_bytes(_crlf(self.sub_text).encode("utf-8"))
            asy.write_bytes(_crlf(self.asy_text).encode(ENCODING))
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            return [Diagnostic(0, 0, ERROR, f"cannot write {exc.filename or folder}: {reason}")]
        return []


class SymbolGenerator:
    """Makes circuit symbols. Stateless: use the static methods."""

    @staticmethod
    def from_schematic(
        name: str,
        schematic: Schematic,
        lookup: Callable[[str], Symbol | None],
        base_dir: str | Path | None = None,
    ) -> GeneratedSymbol:
        out = GeneratedSymbol(name)
        try:
            _from_schematic(out, schematic, lookup, base_dir)
        except Exception as exc:  # never raise on a bad sheet
            _fail(out, f"internal symbol generator error: {exc!r}")
        return out

    @staticmethod
    def from_netlist(
        name: str,
        text: str,
        chosen: list[tuple[str, str]],
        base_dir: str | Path | None = None,
    ) -> GeneratedSymbol:
        """``chosen`` is (node, direction) for each ticked node."""
        out = GeneratedSymbol(name)
        try:
            _from_netlist(out, text, chosen, base_dir)
        except Exception as exc:
            _fail(out, f"internal symbol generator error: {exc!r}")
        return out


# ---------------------------------------------------------------- sources


def _fail(out: GeneratedSymbol, message: str) -> None:
    out.diagnostics.append(Diagnostic(0, 0, ERROR, message))
    out.sub_text = out.asy_text = ""


def _from_schematic(
    out: GeneratedSymbol,
    schematic: Schematic,
    lookup: Callable[[str], Symbol | None],
    base_dir: str | Path | None,
) -> None:
    if problem := symbol_name_problem(out.name):
        return _fail(out, problem)
    for inst in schematic.instances:
        symbol = lookup(inst.symbol)
        if symbol is not None and is_generated(symbol):
            return _fail(
                out,
                f"{inst.name} is the circuit symbol {inst.symbol}; a circuit that uses a "
                "circuit symbol cannot become one (one level only)",
            )
    netlisted = netlist_schematic(schematic, lookup, base_dir)
    # Connectivity, part and syntax problems count; the whole-circuit checks
    # (validate) do not, because a port node is connected outside the symbol and the
    # body need not have a ground. The .sub is validated as a subcircuit in _finish.
    found_here = netlisted.nets.diagnostics + [d for p in netlisted.parts for d in p.diagnostics]
    part_lines = {p.line for p in netlisted.parts if p.line}
    text_lines = netlisted.text.splitlines()
    for d in parse(netlisted.text, base_dir).diagnostics:
        line = text_lines[d.line - 1].strip() if 0 < d.line <= len(text_lines) else ""
        if d.fix != FIX_INSERT_TITLE and line not in part_lines:
            found_here.append(Diagnostic(0, 0, d.severity, f"{line or 'schematic'}: {d.message}"))
    out.diagnostics += found_here
    if any(d.severity == ERROR for d in found_here):
        return _fail(out, "fix the schematic's errors first")
    found = schematic_ports(schematic, netlisted.nets)
    out.diagnostics += found.diagnostics
    if not found.ok:
        return _fail(out, "fix the ports first")
    if not found.ports:
        return _fail(
            out, "no ports: place port flags (in, out or bidir) on the nets that become pins"
        )
    title = " ".join(schematic.title.split()) or "a schematic"
    _finish(out, found.ordered(), netlisted.text, f"from the schematic {title}", base_dir)


def _from_netlist(
    out: GeneratedSymbol,
    text: str,
    chosen: list[tuple[str, str]],
    base_dir: str | Path | None,
) -> None:
    if problem := symbol_name_problem(out.name):
        return _fail(out, problem)
    result = parse(text, base_dir)
    errors = [d for d in result.diagnostics if d.severity == ERROR]
    if errors:
        out.diagnostics += [
            Diagnostic(d.line, d.col, d.severity, f"line {d.line}: {d.message}") for d in errors
        ]
        return _fail(out, "fix the netlist's errors first")
    if _uses_generated_sub(text, base_dir):
        return _fail(
            out,
            "the netlist includes a circuit symbol's .sub file; a circuit that uses a "
            "circuit symbol cannot become one (one level only)",
        )
    found = netlist_ports(result.circuit.nodes(), chosen)
    out.diagnostics += found.diagnostics
    if not found.ok:
        return _fail(out, "fix the ports first")
    if not found.ports:
        return _fail(out, "no ports: tick the nodes that become pins")
    title = text.splitlines()[0].strip().lstrip("*").strip() if text.strip() else ""
    _finish(out, found.ordered(), text, f"from the netlist {title or '(untitled)'}", base_dir)


def _uses_generated_sub(text: str, base_dir: str | Path | None) -> bool:
    base = Path(base_dir) if base_dir is not None else None
    for card in _cards(text)[1:]:
        words = card.split()
        if not words or words[0].casefold() not in _HEAD_CARDS or len(words) < 2:
            continue
        rest = card.split(maxsplit=1)[1].strip()
        name = rest[1:].split('"')[0] if rest.startswith('"') else rest.split()[0].strip("'")
        path = Path(name)
        if not path.is_absolute() and base is not None:
            path = base / path
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                if f.readline().startswith(SUB_HEADER):
                    return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------- the .sub file


def _cards(text: str) -> list[str]:
    """Logical lines: ``+`` continuations joined; comments and blank lines dropped.
    The first physical line (the title) is kept as card 0."""
    lines = text.splitlines()
    if not lines:
        return [""]
    cards = [lines[0]]
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+") and len(cards) > 1:
            cards[-1] += "\n" + raw.rstrip()
            continue
        cards.append(raw.rstrip())
    return cards


def _split(out: GeneratedSymbol, text: str) -> tuple[list[str], list[str]]:
    """(head, body) cards of a netlist text."""
    head: list[str] = []
    body: list[str] = []
    in_subckt = False
    for card in _cards(text)[1:]:
        keyword = card.split()[0].casefold()
        if in_subckt:
            head.append(card)
            if keyword == ".ends":
                in_subckt = False
            continue
        if keyword == ".subckt":
            in_subckt = True
            head.append(card)
        elif keyword in _HEAD_CARDS:
            head.append(card)
        elif keyword == ".end":
            break
        elif keyword in _ANALYSES:
            continue
        elif keyword.startswith(".") and keyword not in _BODY_CARDS:
            out.diagnostics.append(
                Diagnostic(0, 0, WARNING, f"{card.split()[0]} is left out of the symbol")
            )
        else:
            body.append(card)
    return head, body


def _finish(
    out: GeneratedSymbol, ports: list[Port], text: str, source: str, base_dir: str | Path | None
) -> None:
    for port in ports:
        try:
            port.name.encode(ENCODING)
        except UnicodeEncodeError:
            return _fail(out, f"port {port.name}: the name has characters a .asy file cannot hold")
    head, body = _split(out, text)
    pins = " ".join(f"{p.name} ({p.direction})" for p in ports)
    lines = [f"{SUB_HEADER} {out.name}: made {source}", f"* Pins: {pins}"]
    lines += head
    lines.append(f".subckt {out.name} {' '.join(p.node for p in ports)}")
    lines += body
    lines.append(f".ends {out.name}")
    out.ports = ports
    out.sub_text = "\n".join(lines) + "\n"
    # Validate the .sub as a subcircuit: only problems inside our .subckt block count
    # (whole-netlist checks such as "no ground" do not apply to a symbol's body).
    check_text = f"* check\n{out.sub_text}.end\n"
    check = parse(check_text, base_dir)
    numbered = check_text.splitlines()
    first = numbered.index(f".subckt {out.name} {' '.join(p.node for p in ports)}") + 1
    last = len(numbered) - 1  # the .ends line; .end follows it
    inside = [d for d in validate(check.circuit, require_analysis=False) if first <= d.line <= last]
    for d in check.diagnostics + inside:
        if d.severity == ERROR:
            out.diagnostics.append(Diagnostic(0, 0, ERROR, f"{out.name}.sub: {d.message}"))
    if any(d.severity == ERROR for d in out.diagnostics):
        out.sub_text = ""
        return
    out.asy_text = _asy(out.name, ports)


# ---------------------------------------------------------------- the .asy file


def _up(value: int, step: int = GRID) -> int:
    return -(-value // step) * step


def _asy(name: str, ports: list[Port]) -> str:
    left = [p for p in ports if p.direction == "in"]
    right = [p for p in ports if p.direction == "out"]
    top = [p for p in ports if p.direction not in ("in", "out")]
    rows = max(len(left), len(right), 1)
    height = PIN_PITCH * (rows + 1)
    longest = max([len(p.name) for p in left] or [0]) + max([len(p.name) for p in right] or [0])
    width = max(
        64,
        _up(longest * CHAR + 2 * GRID),
        PIN_PITCH * (len(top) + 1),
        _up(len(name) * CHAR + 2 * GRID),
    )
    lines = ["Version 4", "SymbolType BLOCK", f"RECTANGLE Normal 0 0 {width} {height}"]
    pins: list[tuple[Port, int, int, str]] = []
    for k, p in enumerate(left, start=1):
        y = PIN_PITCH * k
        lines.append(f"LINE Normal {-STUB} {y} 0 {y}")
        pins.append((p, -STUB, y, "LEFT"))
    for k, p in enumerate(right, start=1):
        y = PIN_PITCH * k
        lines.append(f"LINE Normal {width} {y} {width + STUB} {y}")
        pins.append((p, width + STUB, y, "RIGHT"))
    span = PIN_PITCH * (len(top) - 1)
    start = (width - span) // 2 // GRID * GRID
    for k, p in enumerate(top):
        x = start + PIN_PITCH * k
        lines.append(f"LINE Normal {x} {-STUB} {x} 0")
        pins.append((p, x, -STUB, "TOP"))
    lines += [
        f"WINDOW 0 0 {height + 16} Left 2",
        f"WINDOW 3 0 {height + 40} Left 2",
        f"SYMATTR Value {name}",
        "SYMATTR Prefix X",
        f"SYMATTR SpiceModel {name}",
        f"SYMATTR ModelFile {name}{SUB_SUFFIX}",
        f"SYMATTR Description Circuit symbol {name}: {len(ports)} pins {DESCRIPTION_MARK}",
    ]
    # ``ports`` is in/out/bidir order and so is ``pins``: SpiceOrder is the position.
    for order, (p, x, y, align) in enumerate(pins, start=1):
        lines += [
            f"PIN {x} {y} {align} {STUB + 4}",
            f"PINATTR PinName {p.name}",
            f"PINATTR SpiceOrder {order}",
        ]
    return "\n".join(lines) + "\n"


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")
