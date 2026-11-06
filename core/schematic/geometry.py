"""Schematic geometry for the editor: drawing paths, boxes, text anchors, hit-testing.
Qt-free.

All coordinates are sheet units (LTspice units, y down). The editor draws and
hit-tests with these same numbers, so what is drawn is what is clicked.

* ``symbol_svg`` turns a parsed .asy into one SVG path (LINE, RECTANGLE, CIRCLE, ARC),
  in the symbol's own coordinates; the editor applies ``orientation_matrix``.
* ``instance_texts`` places the InstName (WINDOW 0) and Value (WINDOW 3) of a part, and
  the names of pins whose PIN alignment is not NONE (H2: circuit symbols use them).
  Text stays upright; only its anchor and alignment follow rotation and mirror.
* ``hit_test`` / ``items_in_rect`` find flags, wires and parts under the pointer.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from core.schematic.model import Instance, Point, Schematic, Wire, pin_points, transform
from core.symbols.asy import Symbol

GRID = 16
Rect = tuple[int, int, int, int]  # min x, min y, max x, max y
Lookup = Callable[[str], Symbol | None]

# Kinds of selectable things; ``Hit.index`` indexes the matching Schematic list.
INSTANCE, WIRE, LABEL, GROUND, PORT = "instance", "wire", "label", "ground", "port"
TEXT = "text"  # a directive or comment on the sheet (S9)
_LIST_OF = {
    INSTANCE: "instances",
    WIRE: "wires",
    LABEL: "labels",
    GROUND: "grounds",
    PORT: "ports",
    TEXT: "texts",
}
# Size of a part whose symbol is missing (drawn as a box).
MISSING_BOX: Rect = (-16, -16, 16, 16)
LABEL_CHAR = 8  # approximate width of a label character, for hit-testing
TEXT_CHAR = 7  # ... and of a sheet-text character
TEXT_LINE = 16  # sheet-text line height


@dataclass(frozen=True)
class Hit:
    kind: str
    index: int


def items_of(schematic: Schematic, kind: str) -> list:
    return getattr(schematic, _LIST_OF[kind])


def turn(rotation: int, mirror: bool, by: int, flip: bool) -> tuple[int, bool]:
    """The orientation after turning a part ``by`` degrees clockwise on screen, then
    (``flip``) mirroring it left-right, both about its own origin."""
    for _ in range((by // 90) % 4):
        # R90 after M^m R_r: R(r+90) unmirrored; M R(r-90) when mirrored.
        rotation = (rotation + (270 if mirror else 90)) % 360
    if flip:
        mirror = not mirror
    return rotation, mirror


def snap(value: float, grid: int = GRID) -> int:
    return int(math.floor(value / grid + 0.5)) * grid


# ---------------------------------------------------------------- drawing


def symbol_svg(symbol: Symbol) -> str:
    """One SVG path for the symbol's shapes, in its own coordinates."""
    parts: list[str] = []
    for line in symbol.lines:
        parts.append(f"M {line.x1} {line.y1} L {line.x2} {line.y2}")
    for r in symbol.rects:
        parts.append(f"M {r.x1} {r.y1} L {r.x2} {r.y1} L {r.x2} {r.y2} L {r.x1} {r.y2} Z")
    for c in symbol.circles:
        cx, cy = (c.x1 + c.x2) / 2, (c.y1 + c.y2) / 2
        rx, ry = abs(c.x2 - c.x1) / 2, abs(c.y2 - c.y1) / 2
        parts.append(
            f"M {_n(cx - rx)} {_n(cy)} A {_n(rx)} {_n(ry)} 0 1 0 {_n(cx + rx)} {_n(cy)} "
            f"A {_n(rx)} {_n(ry)} 0 1 0 {_n(cx - rx)} {_n(cy)}"
        )
    for a in symbol.arcs:
        parts.append(_arc(a.x1, a.y1, a.x2, a.y2, a.x3, a.y3, a.x4, a.y4))
    return " ".join(parts)


def _n(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _arc(x1: int, y1: int, x2: int, y2: int, sx: int, sy: int, ex: int, ey: int) -> str:
    """LTspice ARC (bbox, start ray point, end ray point; counter-clockwise on screen)."""
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    rx, ry = abs(x2 - x1) / 2, abs(y2 - y1) / 2
    if rx == 0 or ry == 0:
        return ""
    a0 = math.atan2((sy - cy) / ry, (sx - cx) / rx)
    a1 = math.atan2((ey - cy) / ry, (ex - cx) / rx)
    p0 = (cx + rx * math.cos(a0), cy + ry * math.sin(a0))
    p1 = (cx + rx * math.cos(a1), cy + ry * math.sin(a1))
    span = (a0 - a1) % (2 * math.pi)  # y down: counter-clockwise = decreasing angle
    large = 1 if span > math.pi else 0
    return f"M {_n(p0[0])} {_n(p0[1])} A {_n(rx)} {_n(ry)} 0 {large} 0 {_n(p1[0])} {_n(p1[1])}"


def orientation_matrix(rotation: int, mirror: bool) -> tuple[int, int, int, int]:
    """(a, b, c, d) with x' = a*x + b*y, y' = c*x + d*y; the same map as ``transform``."""
    a, c = transform(1, 0, rotation, mirror)
    b, d = transform(0, 1, rotation, mirror)
    return a, b, c, d


def symbol_box(symbol: Symbol | None) -> Rect:
    if symbol is None:
        return MISSING_BOX
    box = symbol.bbox()
    return box if box != (0, 0, 0, 0) else MISSING_BOX


def instance_box(inst: Instance, symbol: Symbol | None) -> Rect:
    """The part's bounding box on the sheet."""
    x1, y1, x2, y2 = symbol_box(symbol)
    xs, ys = [], []
    for px, py in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
        tx, ty = transform(px, py, inst.rotation, inst.mirror)
        xs.append(inst.x + tx)
        ys.append(inst.y + ty)
    return min(xs), min(ys), max(xs), max(ys)


def part_value(inst: Instance, symbol: Symbol | None) -> str:
    """What the Value window shows: Value, else SpiceModel."""

    def attr(key: str) -> str:
        for name, value in inst.attrs.items():
            if name.casefold() == key.casefold():
                return value
        return symbol.attr(key) if symbol is not None else ""

    return attr("Value").strip() or attr("SpiceModel").strip()


def instance_texts(inst: Instance, symbol: Symbol | None) -> list[dict[str, object]]:
    """Upright texts of a part, relative to its origin: {x, y, text, align, size}.

    ``align`` is left, right, center (around the anchor), top (text below the anchor)
    or bottom (text above it).
    """
    if symbol is None:
        return [
            {"x": 0, "y": 24, "text": f"{inst.name}: {inst.symbol}?", "align": "top", "size": 2}
        ]
    if not symbol.prefix:  # ground and label symbols only name nets
        return []
    windows = {w.index: w for w in symbol.windows}
    x1, y1, x2, _ = symbol_box(symbol)
    defaults = {0: (x2 + 8, y1 + 8, "Left", 2), 3: (x2 + 8, y1 + 40, "Left", 2)}
    texts = [(0, inst.name), (3, part_value(inst, symbol))]
    value2 = next(
        (v for k, v in inst.attrs.items() if k.casefold() == "value2"), symbol.attr("Value2")
    ).strip()
    out: list[dict[str, object]] = []
    for index, text in texts:
        if not text:
            continue
        w = windows.get(index)
        wx, wy, align, size = (w.x, w.y, w.align, w.size) if w else defaults[index]
        if size == 0:
            continue  # LTspice: size 0 hides the window
        out.append(_text(inst, wx, wy, align, size, text))
        if index == 3 and value2:
            item = dict(out[-1])
            item["text"] = value2
            item["y"] = int(item["y"]) + 24  # type: ignore[call-overload]
            out.append(item)
    out += _pin_names(inst, symbol)
    return out


# PIN alignment -> (anchor offset direction, text alignment word for ``_text``). As in
# LTspice the name sits ``offset`` units from the pin: LEFT to its right, RIGHT to its
# left, TOP below it, BOTTOM above it; NONE hides it. Text stays upright.
_PIN_TEXT = {
    "left": ((1, 0), "Left"),
    "right": ((-1, 0), "Right"),
    "top": ((0, 1), "VRight"),
    "bottom": ((0, -1), "VLeft"),
}


def _pin_names(inst: Instance, symbol: Symbol) -> list[dict[str, object]]:
    out = []
    for pin in symbol.pins:
        found = _PIN_TEXT.get(pin.align.casefold())
        if found is None or not pin.name:
            continue
        (dx, dy), align = found
        out.append(
            _text(inst, pin.x + dx * pin.offset, pin.y + dy * pin.offset, align, 2, pin.name)
        )
    return out


def _text(inst: Instance, wx: int, wy: int, align: str, size: int, text: str) -> dict:
    ax, ay = transform(wx, wy, inst.rotation, inst.mirror)
    word = align.casefold()
    base = {"left": (1, 0), "right": (-1, 0), "vleft": (0, -1), "vright": (0, 1)}.get(word)
    if base is None:
        where = "center"
    else:
        dx, dy = transform(*base, inst.rotation, inst.mirror)
        where = {(1, 0): "left", (-1, 0): "right", (0, 1): "top", (0, -1): "bottom"}[(dx, dy)]
    return {"x": ax, "y": ay, "text": text, "align": where, "size": size}


# ---------------------------------------------------------------- hit-testing


def _segment_distance(px: float, py: float, x1: int, y1: int, x2: int, y2: int) -> float:
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def on_segment(point: Point, wire: Wire) -> bool:
    """True when ``point`` lies on the wire strictly between its ends."""
    (x1, y1), (x2, y2) = wire.ends
    x, y = point
    if (x2 - x1) * (y - y1) != (y2 - y1) * (x - x1) or point in wire.ends:
        return False
    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)


def flag_box(schematic: Schematic, kind: str, index: int) -> Rect:
    if kind == GROUND:
        g = schematic.grounds[index]
        return g.x - 16, g.y, g.x + 16, g.y + 32
    flag = items_of(schematic, kind)[index]
    width = 12 + LABEL_CHAR * len(flag.name)
    return flag.x, flag.y - 10, flag.x + width, flag.y + 10


def text_box(schematic: Schematic, index: int) -> Rect:
    """Box of a sheet text: (x, y) is the middle of its first line's left end."""
    t = schematic.texts[index]
    lines = t.text.splitlines() or [""]
    width = max(TEXT_CHAR * max(len(line) for line in lines), TEXT_CHAR)
    return t.x, t.y - TEXT_LINE // 2, t.x + width, t.y - TEXT_LINE // 2 + TEXT_LINE * len(lines)


def hit_test(schematic: Schematic, lookup: Lookup, x: float, y: float, tol: float) -> Hit | None:
    """The thing under (x, y): flags first, then texts, wires within ``tol``, parts."""
    for kind in (LABEL, PORT, GROUND):
        for i in range(len(items_of(schematic, kind)) - 1, -1, -1):
            if _inside(flag_box(schematic, kind, i), x, y, tol):
                return Hit(kind, i)
    for i in range(len(schematic.texts) - 1, -1, -1):
        if _inside(text_box(schematic, i), x, y, tol / 2):
            return Hit(TEXT, i)
    best: tuple[float, int] | None = None
    for i, w in enumerate(schematic.wires):
        d = _segment_distance(x, y, w.x1, w.y1, w.x2, w.y2)
        if d <= tol and (best is None or d < best[0]):
            best = (d, i)
    if best is not None:
        return Hit(WIRE, best[1])
    for i in range(len(schematic.instances) - 1, -1, -1):
        inst = schematic.instances[i]
        if _inside(instance_box(inst, lookup(inst.symbol)), x, y, tol / 2):
            return Hit(INSTANCE, i)
    return None


def _inside(box: Rect, x: float, y: float, margin: float = 0) -> bool:
    return box[0] - margin <= x <= box[2] + margin and box[1] - margin <= y <= box[3] + margin


def items_in_rect(schematic: Schematic, lookup: Lookup, rect: Rect) -> list[Hit]:
    """Everything wholly inside ``rect`` (any two opposite corners)."""
    x1, x2 = sorted((rect[0], rect[2]))
    y1, y2 = sorted((rect[1], rect[3]))
    box = (x1, y1, x2, y2)

    def contains(inner: Rect) -> bool:
        return _inside(box, inner[0], inner[1]) and _inside(box, inner[2], inner[3])

    found = [
        Hit(INSTANCE, i)
        for i, inst in enumerate(schematic.instances)
        if contains(instance_box(inst, lookup(inst.symbol)))
    ]
    found += [
        Hit(WIRE, i)
        for i, w in enumerate(schematic.wires)
        if _inside(box, w.x1, w.y1) and _inside(box, w.x2, w.y2)
    ]
    for kind in (LABEL, GROUND, PORT):
        found += [
            Hit(kind, i) for i, f in enumerate(items_of(schematic, kind)) if _inside(box, f.x, f.y)
        ]
    found += [Hit(TEXT, i) for i in range(len(schematic.texts)) if contains(text_box(schematic, i))]
    return found


def pin_near(schematic: Schematic, lookup: Lookup, x: float, y: float, tol: float) -> Point | None:
    """The closest pin within ``tol`` of (x, y)."""
    best: tuple[float, Point] | None = None
    for inst in schematic.instances:
        symbol = lookup(inst.symbol)
        if symbol is None:
            continue
        for point in pin_points(inst, symbol):
            d = math.hypot(point[0] - x, point[1] - y)
            if d <= tol and (best is None or d < best[0]):
                best = (d, point)
    return None if best is None else best[1]


def bounds(schematic: Schematic, lookup: Lookup) -> Rect | None:
    """The box around everything on the sheet, or None for an empty sheet."""
    boxes = [instance_box(i, lookup(i.symbol)) for i in schematic.instances]
    boxes += [
        (min(w.x1, w.x2), min(w.y1, w.y2), max(w.x1, w.x2), max(w.y1, w.y2))
        for w in schematic.wires
    ]
    for kind in (LABEL, GROUND, PORT):
        boxes += [flag_box(schematic, kind, i) for i in range(len(items_of(schematic, kind)))]
    boxes += [text_box(schematic, i) for i in range(len(schematic.texts))]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
