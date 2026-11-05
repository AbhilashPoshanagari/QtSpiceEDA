"""Schematic data model. Qt-free.

One sheet (S1): placed symbols, wire segments, net labels, ground flags, port flags
and texts (SPICE directives or comments). Coordinates are LTspice units (integers;
the editor grid is 16), y grows downwards.

``Schematic.to_dict()`` gives plain JSON data; ``Schematic.from_dict()`` reads it back
and never raises: problems become Diagnostics and bad items are skipped.

``PartInstance`` (L7) is what the netlister turns into one SPICE line: a symbol with
its nets already resolved. The S3 netlister builds one from each ``Instance``.

Orientation follows LTspice: ``rotation`` is clockwise on screen, and the mirror is
applied after the rotation (mirror in X), so ``M90`` maps (x, y) to (y, x).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.netlist.model import ERROR, WARNING, Diagnostic
from core.symbols.asy import Symbol

ROTATIONS = (0, 90, 180, 270)
PORT_DIRECTIONS = ("in", "out", "bidir")

Point = tuple[int, int]


@dataclass
class PartInstance:
    """A placed symbol.

    ``name`` is the InstName as the user sees it (``R1``). ``nets`` holds one net name
    per pin, in the order of ``symbol.pins`` (SpiceOrder). ``attrs`` are this part's
    own attribute values; they override the symbol's SYMATTR defaults (case-insensitive
    keys).
    """

    name: str
    symbol: Symbol
    nets: list[str] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)

    def attr(self, key: str, default: str = "") -> str:
        folded = key.casefold()
        for name, value in self.attrs.items():
            if name.casefold() == folded:
                return value
        return self.symbol.attr(key, default)


@dataclass
class Instance:
    """A symbol placed on the sheet. ``symbol`` is its SymbolLibrary name (``res``)."""

    symbol: str
    name: str
    x: int = 0
    y: int = 0
    rotation: int = 0
    mirror: bool = False
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def orientation(self) -> str:
        """The LTspice orientation word: R0 ... R270, M0 ... M270."""
        return f"{'M' if self.mirror else 'R'}{self.rotation}"


@dataclass
class Wire:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def ends(self) -> tuple[Point, Point]:
        return (self.x1, self.y1), (self.x2, self.y2)


@dataclass
class NetLabel:
    x: int
    y: int
    name: str


@dataclass
class GroundFlag:
    x: int
    y: int


@dataclass
class PortFlag:
    x: int
    y: int
    name: str
    direction: str = "bidir"


@dataclass
class TextItem:
    """Text on the sheet. A directive (``.tran 1m``) becomes SPICE cards; a comment does not.
    ``text`` may hold several lines."""

    x: int
    y: int
    text: str
    directive: bool = True


@dataclass
class Schematic:
    title: str = ""
    instances: list[Instance] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    grounds: list[GroundFlag] = field(default_factory=list)
    ports: list[PortFlag] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)

    def instance(self, name: str) -> Instance | None:
        folded = name.casefold()
        return next((i for i in self.instances if i.name.casefold() == folded), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "instances": [
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "x": i.x,
                    "y": i.y,
                    "rotation": i.rotation,
                    "mirror": i.mirror,
                    "attrs": dict(i.attrs),
                }
                for i in self.instances
            ],
            "wires": [[w.x1, w.y1, w.x2, w.y2] for w in self.wires],
            "labels": [{"x": n.x, "y": n.y, "name": n.name} for n in self.labels],
            "grounds": [{"x": g.x, "y": g.y} for g in self.grounds],
            "ports": [
                {"x": p.x, "y": p.y, "name": p.name, "direction": p.direction} for p in self.ports
            ],
            "texts": [
                {"x": t.x, "y": t.y, "text": t.text, "directive": t.directive} for t in self.texts
            ],
        }

    @staticmethod
    def from_dict(data: object) -> tuple[Schematic, list[Diagnostic]]:
        """Read ``to_dict()`` data. Never raises; bad items are skipped with an error."""
        reader = _Reader()
        try:
            schematic = reader.read(data)
        except Exception as exc:  # from_dict must never raise
            reader.error(f"internal error reading the schematic: {exc!r}")
            schematic = Schematic()
        return schematic, reader.diagnostics


# ---------------------------------------------------------------- geometry


def transform(x: int, y: int, rotation: int, mirror: bool) -> Point:
    """A symbol-local point turned by ``rotation`` (clockwise on screen), then mirrored."""
    turns = (rotation // 90) % 4
    for _ in range(turns):
        x, y = -y, x
    return (-x, y) if mirror else (x, y)


def pin_points(instance: Instance, symbol: Symbol) -> list[Point]:
    """Sheet positions of the symbol's pins, in ``symbol.pins`` (SpiceOrder) order."""
    out: list[Point] = []
    for pin in symbol.pins:
        dx, dy = transform(pin.x, pin.y, instance.rotation, instance.mirror)
        out.append((instance.x + dx, instance.y + dy))
    return out


# ---------------------------------------------------------------- from_dict


_KEYS = ("title", "instances", "wires", "labels", "grounds", "ports", "texts")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _Reader:
    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []

    def error(self, message: str) -> None:
        self.diagnostics.append(Diagnostic(0, 0, ERROR, f"schematic: {message}"))

    def warning(self, message: str) -> None:
        self.diagnostics.append(Diagnostic(0, 0, WARNING, f"schematic: {message}"))

    def read(self, data: object) -> Schematic:
        out = Schematic()
        if not isinstance(data, dict):
            self.error("must be an object")
            return out
        for key in data:
            if key not in _KEYS:
                self.warning(f"unknown field {key!r} ignored")
        title = data.get("title", "")
        if isinstance(title, str):
            out.title = title
        else:
            self.error("'title' must be text")
        out.instances = self.items(data, "instances", self.instance)
        out.wires = self.items(data, "wires", self.wire)
        out.labels = self.items(data, "labels", self.label)
        out.grounds = self.items(data, "grounds", self.ground)
        out.ports = self.items(data, "ports", self.port)
        out.texts = self.items(data, "texts", self.text)
        names: set[str] = set()
        for inst in out.instances:
            folded = inst.name.casefold()
            if folded in names:
                self.warning(f"two instances are called {inst.name}")
            names.add(folded)
        return out

    def items(self, data: dict[str, Any], key: str, read: Any) -> list[Any]:
        value = data.get(key, [])
        if not isinstance(value, list):
            self.error(f"{key!r} must be a list")
            return []
        out = []
        for i, item in enumerate(value):
            made = read(item, f"{key}[{i}]")
            if made is not None:
                out.append(made)
        return out

    def fields(
        self, item: object, where: str, spec: dict[str, Any], optional: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Check an object's fields. ``spec``: required name -> checker; ``optional``:
        name -> (checker, default)."""
        if not isinstance(item, dict):
            self.error(f"{where} must be an object")
            return None
        got: dict[str, Any] = {}
        for name, check in spec.items():
            if name not in item:
                self.error(f"{where}.{name} is missing")
                return None
            if not check(item[name]):
                self.error(f"{where}.{name} has the wrong type")
                return None
            got[name] = item[name]
        for name, (check, default) in optional.items():
            value = item.get(name, default)
            if not check(value):
                self.error(f"{where}.{name} has the wrong type")
                return None
            got[name] = value
        for name in item:
            if name not in spec and name not in optional:
                self.warning(f"unknown field {where}.{name} ignored")
        return got

    def instance(self, item: object, where: str) -> Instance | None:
        def text(v: object) -> bool:
            return isinstance(v, str)

        def attrs(v: object) -> bool:
            return isinstance(v, dict) and all(
                isinstance(k, str) and isinstance(s, str) for k, s in v.items()
            )

        got = self.fields(
            item,
            where,
            {"symbol": text, "name": text, "x": _is_int, "y": _is_int},
            {
                "rotation": (_is_int, 0),
                "mirror": (lambda v: isinstance(v, bool), False),
                "attrs": (attrs, {}),
            },
        )
        if got is None:
            return None
        if got["rotation"] not in ROTATIONS:
            self.error(f"{where}.rotation must be 0, 90, 180 or 270, not {got['rotation']}")
            return None
        if not got["symbol"].strip() or not got["name"].strip():
            self.error(f"{where} needs a symbol and a name")
            return None
        return Instance(
            got["symbol"],
            got["name"],
            got["x"],
            got["y"],
            got["rotation"],
            got["mirror"],
            dict(got["attrs"]),
        )

    def wire(self, item: object, where: str) -> Wire | None:
        if not (isinstance(item, list) and len(item) == 4 and all(_is_int(v) for v in item)):
            self.error(f"{where} must be [x1, y1, x2, y2] (whole numbers)")
            return None
        if item[0] == item[2] and item[1] == item[3]:
            self.warning(f"{where} has zero length; ignored")
            return None
        return Wire(*item)

    def label(self, item: object, where: str) -> NetLabel | None:
        got = self.fields(
            item, where, {"x": _is_int, "y": _is_int, "name": lambda v: isinstance(v, str)}, {}
        )
        if got is None:
            return None
        name = got["name"].strip()
        if not name or any(c.isspace() for c in name):
            self.error(f"{where}.name must be one word, not {got['name']!r}")
            return None
        return NetLabel(got["x"], got["y"], name)

    def ground(self, item: object, where: str) -> GroundFlag | None:
        got = self.fields(item, where, {"x": _is_int, "y": _is_int}, {})
        return None if got is None else GroundFlag(got["x"], got["y"])

    def port(self, item: object, where: str) -> PortFlag | None:
        got = self.fields(
            item,
            where,
            {"x": _is_int, "y": _is_int, "name": lambda v: isinstance(v, str)},
            {"direction": (lambda v: isinstance(v, str), "bidir")},
        )
        if got is None:
            return None
        name = got["name"].strip()
        if not name or any(c.isspace() for c in name):
            self.error(f"{where}.name must be one word, not {got['name']!r}")
            return None
        if got["direction"] not in PORT_DIRECTIONS:
            self.error(f"{where}.direction must be in, out or bidir, not {got['direction']!r}")
            return None
        return PortFlag(got["x"], got["y"], name, got["direction"])

    def text(self, item: object, where: str) -> TextItem | None:
        got = self.fields(
            item,
            where,
            {"x": _is_int, "y": _is_int, "text": lambda v: isinstance(v, str)},
            {"directive": (lambda v: isinstance(v, bool), True)},
        )
        return None if got is None else TextItem(got["x"], got["y"], got["text"], got["directive"])
