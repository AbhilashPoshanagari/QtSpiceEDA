"""LTspice ``.asy`` symbol files (also QtSpice's own symbol format). Qt-free.

``AsyParser.parse(path)`` returns ``(Symbol, diagnostics)`` and never raises. Files are
read as Windows-1252 (the micro sign is byte 0xB5), with CR+LF or LF line ends.

Keywords: Version, SymbolType, LINE, RECTANGLE, CIRCLE, ARC, TEXT, WINDOW, SYMATTR,
PIN, PINATTR. An unknown keyword or a malformed line is a warning and is skipped.

``Symbol.pins`` is in netlist order: sorted by ``PINATTR SpiceOrder``, never by the
order the pins are drawn in (``Pin.index`` keeps the drawing order).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.netlist.model import ERROR, WARNING, Diagnostic

ENCODING = "cp1252"
SYMBOL_TYPES = frozenset({"CELL", "BLOCK"})


@dataclass(frozen=True)
class Line:
    x1: int
    y1: int
    x2: int
    y2: int
    style: int = 0
    source_line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Rect:
    """RECTANGLE; the two corners as written."""

    x1: int
    y1: int
    x2: int
    y2: int
    style: int = 0
    source_line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Circle:
    """CIRCLE (an ellipse) given by its bounding box."""

    x1: int
    y1: int
    x2: int
    y2: int
    style: int = 0
    source_line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Arc:
    """ARC: bounding box of the ellipse, then the start and end ray points."""

    x1: int
    y1: int
    x2: int
    y2: int
    x3: int
    y3: int
    x4: int
    y4: int
    style: int = 0
    source_line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Text:
    x: int
    y: int
    align: str
    size: int
    text: str
    source_line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Window:
    """Where attribute ``index`` (0 = InstName, 3 = Value, ...) is shown."""

    index: int
    x: int
    y: int
    align: str
    size: int
    source_line: int = field(default=0, compare=False)


@dataclass
class Pin:
    """A pin. ``index`` is the drawing order; ``spice_order`` the netlist order."""

    x: int
    y: int
    align: str = "NONE"
    offset: int = 0
    index: int = 0
    attrs: dict[str, str] = field(default_factory=dict)
    source_line: int = field(default=0, compare=False)

    @property
    def name(self) -> str:
        return _get(self.attrs, "PinName")

    @property
    def spice_order(self) -> int | None:
        value = _get(self.attrs, "SpiceOrder")
        try:
            return int(value)
        except ValueError:
            return None


@dataclass
class Symbol:
    name: str = ""
    path: Path | None = None
    version: int = 4
    type: str = "CELL"
    lines: list[Line] = field(default_factory=list)
    rects: list[Rect] = field(default_factory=list)
    circles: list[Circle] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)
    pins: list[Pin] = field(default_factory=list)

    def attr(self, key: str, default: str = "") -> str:
        """SYMATTR value; keys match case-insensitively."""
        return _get(self.attrs, key, default)

    @property
    def prefix(self) -> str:
        return self.attr("Prefix")

    def bbox(self) -> tuple[int, int, int, int]:
        """(min x, min y, max x, max y) over shapes and pins; (0, 0, 0, 0) if empty."""
        xs: list[int] = []
        ys: list[int] = []
        for s in [*self.lines, *self.rects, *self.circles, *self.arcs]:
            xs += [s.x1, s.x2]
            ys += [s.y1, s.y2]
        for p in self.pins:
            xs.append(p.x)
            ys.append(p.y)
        if not xs:
            return (0, 0, 0, 0)
        return (min(xs), min(ys), max(xs), max(ys))


def _get(attrs: dict[str, str], key: str, default: str = "") -> str:
    folded = key.casefold()
    for name, value in attrs.items():
        if name.casefold() == folded:
            return value
    return default


def _set(attrs: dict[str, str], key: str, value: str) -> bool:
    """Set ``key`` (case-insensitive); True when it replaced an existing value."""
    folded = key.casefold()
    for name in list(attrs):
        if name.casefold() == folded:
            attrs[name] = value
            return True
    attrs[key] = value
    return False


class AsyParser:
    """Reads ``.asy`` files. Stateless: use the static methods."""

    @staticmethod
    def parse(path: str | Path) -> tuple[Symbol, list[Diagnostic]]:
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            symbol = Symbol(name=path.stem, path=path)
            return symbol, [Diagnostic(0, 0, ERROR, f"cannot read {path.name}: {reason}")]
        text = raw.decode(ENCODING, errors="replace")
        symbol, diagnostics = AsyParser.parse_text(text, name=path.stem)
        symbol.path = path
        if "�" in text:
            diagnostics.insert(
                0, Diagnostic(0, 0, WARNING, "bytes that are not Windows-1252 text were replaced")
            )
        return symbol, diagnostics

    @staticmethod
    def parse_text(text: str, name: str = "") -> tuple[Symbol, list[Diagnostic]]:
        return _Reader(name).read(text)


class _Reader:
    def __init__(self, name: str) -> None:
        self.symbol = Symbol(name=name)
        self.diagnostics: list[Diagnostic] = []
        self.number = 0
        self.drawn: list[Pin] = []

    def warn(self, message: str, col: int = 1) -> None:
        self.diagnostics.append(Diagnostic(self.number, col, WARNING, message))

    def error(self, message: str, col: int = 1) -> None:
        self.diagnostics.append(Diagnostic(self.number, col, ERROR, message))

    def read(self, text: str) -> tuple[Symbol, list[Diagnostic]]:
        handlers = {
            "version": self.version,
            "symboltype": self.symbol_type,
            "line": self.line,
            "rectangle": self.rectangle,
            "circle": self.circle,
            "arc": self.arc,
            "text": self.text,
            "window": self.window,
            "symattr": self.symattr,
            "pin": self.pin,
            "pinattr": self.pinattr,
        }
        for self.number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            keyword = line.split(maxsplit=1)[0]
            handler = handlers.get(keyword.casefold())
            if handler is None:
                self.warn(f"unknown keyword {keyword!r} ignored")
                continue
            handler(line)
        self.finish()
        return self.symbol, self.diagnostics

    # ---- helpers

    def ints(self, words: list[str], count: int, what: str) -> list[int] | None:
        if len(words) < count:
            self.warn(f"{what} needs {count} numbers")
            return None
        try:
            return [int(w) for w in words[:count]]
        except ValueError:
            self.warn(f"{what}: expected whole numbers")
            return None

    def shape(self, line: str, count: int) -> tuple[list[int], int] | None:
        """Numbers of LINE/RECTANGLE/CIRCLE/ARC (after the pen name) and the style."""
        words = line.split()
        what = words[0].upper()
        args = words[2:] if len(words) > 1 and not _is_int(words[1]) else words[1:]
        numbers = self.ints(args, count, what)
        if numbers is None:
            return None
        style = int(args[count]) if len(args) > count and _is_int(args[count]) else 0
        return numbers, style

    # ---- keywords

    def version(self, line: str) -> None:
        words = line.split()
        if len(words) < 2 or not _is_int(words[1]):
            self.warn("Version needs a number")
            return
        self.symbol.version = int(words[1])
        if self.symbol.version != 4:
            self.warn(f"Version {self.symbol.version} (expected 4); reading anyway")

    def symbol_type(self, line: str) -> None:
        words = line.split()
        kind = words[1].upper() if len(words) > 1 else ""
        if kind not in SYMBOL_TYPES:
            self.warn(f"SymbolType {kind or '(none)'} unknown; using CELL")
            return
        self.symbol.type = kind

    def line(self, line: str) -> None:
        if (got := self.shape(line, 4)) is not None:
            n, style = got
            self.symbol.lines.append(Line(*n, style=style, source_line=self.number))

    def rectangle(self, line: str) -> None:
        if (got := self.shape(line, 4)) is not None:
            n, style = got
            self.symbol.rects.append(Rect(*n, style=style, source_line=self.number))

    def circle(self, line: str) -> None:
        if (got := self.shape(line, 4)) is not None:
            n, style = got
            self.symbol.circles.append(Circle(*n, style=style, source_line=self.number))

    def arc(self, line: str) -> None:
        if (got := self.shape(line, 8)) is not None:
            n, style = got
            self.symbol.arcs.append(Arc(*n, style=style, source_line=self.number))

    def text(self, line: str) -> None:
        words = line.split(maxsplit=5)
        n = self.ints(words[1:3], 2, "TEXT")
        if n is None or len(words) < 6 or not _is_int(words[4]):
            if n is not None:
                self.warn("TEXT needs x y alignment size text")
            return
        self.symbol.texts.append(
            Text(n[0], n[1], words[3], int(words[4]), words[5], source_line=self.number)
        )

    def window(self, line: str) -> None:
        words = line.split()
        n = self.ints(words[1:4], 3, "WINDOW")
        if n is None:
            return
        align = words[4] if len(words) > 4 else "Left"
        size = int(words[5]) if len(words) > 5 and _is_int(words[5]) else 2
        self.symbol.windows.append(Window(n[0], n[1], n[2], align, size, self.number))

    def symattr(self, line: str) -> None:
        words = line.split(maxsplit=2)
        if len(words) < 2:
            self.warn("SYMATTR needs a name")
            return
        value = words[2] if len(words) > 2 else ""
        if _set(self.symbol.attrs, words[1], value):
            self.warn(f"SYMATTR {words[1]} given twice; the last one is used")

    def pin(self, line: str) -> None:
        words = line.split()
        n = self.ints(words[1:3], 2, "PIN")
        if n is None:
            return
        align = words[3] if len(words) > 3 else "NONE"
        offset = int(words[4]) if len(words) > 4 and _is_int(words[4]) else 0
        self.drawn.append(
            Pin(n[0], n[1], align, offset, index=len(self.drawn), source_line=self.number)
        )

    def pinattr(self, line: str) -> None:
        words = line.split(maxsplit=2)
        if len(words) < 2:
            self.warn("PINATTR needs a name")
            return
        if not self.drawn:
            self.warn("PINATTR before any PIN ignored")
            return
        value = words[2] if len(words) > 2 else ""
        if words[1].casefold() == "spiceorder" and not _is_int(value):
            self.warn(f"SpiceOrder {value!r} is not a whole number")
        _set(self.drawn[-1].attrs, words[1], value)

    # ---- end of file

    def finish(self) -> None:
        seen: dict[int, Pin] = {}
        for pin in self.drawn:
            self.number = pin.source_line
            order = pin.spice_order
            label = pin.name or f"#{pin.index + 1}"
            if order is None:
                self.warn(f"pin {label} has no SpiceOrder; it is netlisted last")
            elif order in seen:
                self.error(f"pin {label}: SpiceOrder {order} is also used by another pin")
            else:
                seen[order] = pin
        self.symbol.pins = sorted(
            self.drawn, key=lambda p: (p.spice_order is None, p.spice_order or 0, p.index)
        )


def _is_int(word: str) -> bool:
    try:
        int(word)
    except ValueError:
        return False
    return True
