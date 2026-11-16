"""Designer edit commands (D4). Qt-free.

Every designer edit is one of these commands (rule 22). A command changes the
GridModel and the DiagramModel of one design (``GridDesign``) together and reverts
exactly: apply then revert gives a design equal to the original, dict key order
included. The bridge wraps each command 1:1 in a QUndoCommand on
GridDiagramController's QUndoStack (rule 10); QML never changes a model directly.

Protocol (spike S2, scratch/phase5/spike2_undo/README.md):

- ``apply(design)`` is both the first "do" and every later "redo". It returns
  Diagnostics: [] = applied. A command whose precondition fails (duplicate name,
  missing bus, wrong class, negative kVA, unknown field ...) returns an error
  Diagnostic at the element id and changes nothing. Commands never raise.
- ``revert(design)`` undoes exactly one ``apply`` and assumes the design is exactly
  as that apply left it (stack discipline).
- Values are captured on the first apply, not at construction: a new element's name
  (lowest free ``<prefix><n>``), a default glyph position, old property values.
  Later applies replay what the first one decided.
- Remove keeps the removed objects and their dict positions and re-inserts the same
  objects there (key order is part of "exact").
- ``merge(other)``: ``MoveGlyph`` (``merge_id`` 1) absorbs a MoveGlyph of the same
  glyph and gesture; ``SetProperty`` (``merge_id`` 2) only with an explicit
  ``merge_key``. Others never merge. ``obsolete`` is True for a merged no-op.
- ``description`` is the Edit-menu text ("Add load ld3"); ``affected()`` the ids
  (element ids, "bus.<name>") whose rows or glyphs changed.

No invented electrical values (rule 24). A new element gets structural defaults only:
its bus(es), phases and nodes from the bus (topology available nodes), kV from the
voltage zone (``GridGraph.voltage_zones``), the name by the naming rule, wye
connection, constant-power load model, enabled. Fields a user must enter stay None
(a draft; validate.py reports them) or come from ``values`` (typed by the user) or
``copy_from`` (an element of the same kind already in the model). Fields that the
v0.4 model cannot leave unset must be given: a source's kV and R1/X1/R0/X0, a
switch's R, a regulator's Vreg, band, PT ratio and CT primary, and a shunt's kV when
its bus has no voltage zone yet. A new line has linecode "" and length 0 until they
are set; validate.py reports both.

Graph questions (voltage zones, available nodes, which elements sit at a bus) are asked
of core/grid/topology.py (rule 25).

Element ids are "<class>.<name>" (``core.grid.model``); a switch is "line.<name>", so
lines and switches share one name space. A bus is "bus.<name>"; its glyph is keyed by
the bus name in ``DiagramModel.bus_glyphs``. LineCodes and RegControls have no glyph.
A SetProperty path is a field name ("kw") or, for a transformer winding,
"windings.<index>.<field>" with a 0-based index ("windings.1.tap" = winding 2).
"""

from __future__ import annotations

import copy
import dataclasses
import math
import re
import types
import typing
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.grid.design_io import GridDesign
from core.grid.diagram.layout import BUS_MIN, auto_layout, place_branches, place_shunts
from core.grid.diagram.model import GLYPH_CLASSES, ORIENTATIONS, BusGlyph, ElementGlyph
from core.grid.model import (
    CONNECTIONS,
    DELTA,
    LOAD_MODELS,
    LOAD_PQ,
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
from core.grid.topology import DISCONNECTED, GridGraph, connected_elements
from core.netlist.model import ERROR
from core.physical.component import NetworkDiagnostic

NO_MERGE = -1
MERGE_MOVE = 1
MERGE_SET_PROPERTY = 2

# stable diagnostic codes
UNKNOWN_ELEMENT = "unknown_element"
UNKNOWN_BUS = "unknown_bus"
UNKNOWN_KIND = "unknown_kind"
DUPLICATE_NAME = "duplicate_name"
BAD_NAME = "bad_name"
WRONG_CLASS = "wrong_class"
UNKNOWN_FIELD = "unknown_field"
READ_ONLY_FIELD = "read_only_field"
BAD_VALUE = "bad_value"
BAD_TERMINAL = "bad_terminal"
IN_USE = "in_use"
VALUE_REQUIRED = "value_required"
NO_VOLTAGE_ZONE = "no_voltage_zone"
COMMAND_FAILED = "command_failed"

CODES = (UNKNOWN_ELEMENT, UNKNOWN_BUS, UNKNOWN_KIND, DUPLICATE_NAME, BAD_NAME, WRONG_CLASS,
         UNKNOWN_FIELD, READ_ONLY_FIELD, BAD_VALUE, BAD_TERMINAL, IN_USE, VALUE_REQUIRED,
         NO_VOLTAGE_ZONE, COMMAND_FAILED)  # fmt: skip

DESIGN = "design"
"""Location of a Diagnostic that belongs to no element."""

NAME_PATTERN = re.compile(r"[a-z0-9_]+")
BUS_PREFIX = "b"

_SQRT3 = math.sqrt(3.0)


# ------------------------------------------------------------------ element kinds


@dataclass(frozen=True)
class Kind:
    """One GridModel element class as the designer knows it."""

    key: str
    """What AddElement takes: "load", "switch", ..."""
    attr: str
    """The GridModel dict: "loads", "switches", ..."""
    cls: type
    prefix: str
    """New names are <prefix><n> with the lowest free n."""
    label: str
    """For descriptions: "Add <label> <name>"."""
    terminals: tuple[int, int]
    """Smallest and largest number of terminals AddElement accepts."""

    @property
    def drawn(self) -> bool:
        return self.attr in GLYPH_CLASSES


KINDS: dict[str, Kind] = {
    k.key: k
    for k in (
        Kind("vsource", "sources", Source, "src", "source", (1, 1)),
        Kind("linecode", "linecodes", LineCode, "lc", "linecode", (0, 0)),
        Kind("line", "lines", Line, "l", "line", (2, 2)),
        Kind("switch", "switches", Switch, "sw", "switch", (2, 2)),
        Kind("transformer", "transformers", Transformer, "tx", "transformer", (2, 3)),
        Kind("regcontrol", "regulators", Regulator, "reg", "regulator", (0, 0)),
        Kind("load", "loads", Load, "ld", "load", (1, 1)),
        Kind("capacitor", "capacitors", Capacitor, "c", "capacitor", (1, 1)),
        Kind("pvsystem", "pvsystems", PVSystem, "pv", "PV system", (1, 1)),
    )
}

REQUIRED_VALUES: dict[str, tuple[str, ...]] = {
    "vsource": ("kv", "r1_ohm", "x1_ohm", "r0_ohm", "x0_ohm"),
    "switch": ("r_ohm",),
    "regcontrol": ("vreg", "band", "pt_ratio", "ct_primary"),
}
"""Fields a new element of the kind needs from ``values`` (or ``copy_from``): the v0.4
model cannot leave them unset and rule 24 forbids a default."""

SHUNT_KINDS = ("vsource", "load", "capacitor", "pvsystem")
_KV_FROM_ZONE = ("load", "capacitor", "pvsystem")


@dataclass(frozen=True)
class _Ref:
    kind: Kind | None
    """None for a bus."""
    name: str
    obj: Any
    table: dict[str, Any]

    @property
    def id(self) -> str:
        return f"bus.{self.name}" if self.kind is None else self.obj.id

    @property
    def label(self) -> str:
        return "bus" if self.kind is None else self.kind.label


def _kind_of_class(cls_name: str) -> list[Kind]:
    return [k for k in KINDS.values() if k.cls.cls == cls_name]  # type: ignore[attr-defined]


def resolve(grid: GridModel, element_id: str) -> _Ref | None:
    """The bus or element with this id, or None. "line.<name>" is a line or a switch."""
    if not isinstance(element_id, str):
        return None
    cls_name, _, name = element_id.partition(".")
    if not name:
        return None
    if cls_name == "bus":
        bus = grid.buses.get(name)
        return None if bus is None else _Ref(None, name, bus, grid.buses)
    for kind in _kind_of_class(cls_name):
        table = getattr(grid, kind.attr)
        if name in table:
            return _Ref(kind, name, table[name], table)
    return None


def _name_tables(grid: GridModel, kind: Kind | None) -> list[dict[str, Any]]:
    """The dicts a name must be unique in (lines and switches share "line.<name>")."""
    if kind is None:
        return [grid.buses]
    if kind.key in ("line", "switch"):
        return [grid.lines, grid.switches]
    return [getattr(grid, kind.attr)]


def lowest_free(tables: Sequence[Mapping[str, Any]], prefix: str) -> str:
    n = 1
    while any(f"{prefix}{n}" in t for t in tables):
        n += 1
    return f"{prefix}{n}"


def _diag(location: str, code: str, message: str) -> NetworkDiagnostic:
    return NetworkDiagnostic(location, ERROR, message, code=code)


def _check_name(
    name: object, tables: Sequence[Mapping[str, Any]], loc: str, out: list[NetworkDiagnostic]
) -> str | None:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name.strip().lower()):
        out.append(_diag(loc, BAD_NAME, f"name {name!r} is not lower-case letters, digits and _"))
        return None
    name = name.strip().lower()
    if any(name in t for t in tables):
        out.append(_diag(loc, DUPLICATE_NAME, f"the name {name!r} is already used"))
        return None
    return name


def _has_error(diags: Iterable[NetworkDiagnostic]) -> bool:
    return any(d.severity == ERROR for d in diags)


# ------------------------------------------------------------------ primitive operations


class _Op:
    def do(self) -> None:
        raise NotImplementedError

    def undo(self) -> None:
        raise NotImplementedError


@dataclass
class _SetAttr(_Op):
    obj: Any
    attr: str
    old: Any
    new: Any

    def do(self) -> None:
        setattr(self.obj, self.attr, self.new)

    def undo(self) -> None:
        setattr(self.obj, self.attr, self.old)


@dataclass
class _Put(_Op):
    """Insert at the end of a dict."""

    table: dict[str, Any]
    key: str
    value: Any

    def do(self) -> None:
        self.table[self.key] = self.value

    def undo(self) -> None:
        del self.table[self.key]


@dataclass
class _Pop(_Op):
    """Remove a key; undo puts the same object back at the same position."""

    table: dict[str, Any]
    key: str
    index: int = -1
    value: Any = None

    def do(self) -> None:
        self.index = list(self.table).index(self.key)
        self.value = self.table.pop(self.key)

    def undo(self) -> None:
        _insert_at(self.table, self.index, self.key, self.value)


@dataclass
class _Rekey(_Op):
    """Rename a key in place (same position)."""

    table: dict[str, Any]
    old: str
    new: str

    def do(self) -> None:
        _rekey(self.table, self.old, self.new)

    def undo(self) -> None:
        _rekey(self.table, self.new, self.old)


def _insert_at(table: dict[str, Any], index: int, key: str, value: Any) -> None:
    if index >= len(table):
        table[key] = value
        return
    items = list(table.items())
    items.insert(index, (key, value))
    table.clear()
    table.update(items)


def _rekey(table: dict[str, Any], old: str, new: str) -> None:
    items = [(new if k == old else k, v) for k, v in table.items()]
    table.clear()
    table.update(items)


# ------------------------------------------------------------------ command base


class Command:
    """Base of every designer command (see the module docstring for the protocol)."""

    merge_id: int = NO_MERGE

    def __init__(self) -> None:
        self._ops: list[_Op] | None = None
        self._description = ""
        self._affected: set[str] = set()

    # -- protocol

    @property
    def description(self) -> str:
        return self._description

    @property
    def obsolete(self) -> bool:
        return False

    def affected(self) -> set[str]:
        return set(self._affected)

    def merge(self, other: Command) -> bool:
        return False

    def apply(self, design: GridDesign) -> list[NetworkDiagnostic]:
        try:
            if self._ops is None:
                diags: list[NetworkDiagnostic] = []
                ops = self._plan(design, diags)
                if ops is None or _has_error(diags):
                    return diags or [_diag(DESIGN, COMMAND_FAILED, "command not applied")]
                self._ops = ops
                out = self._run(ops)
                if out:
                    self._ops = None  # not applied: the next apply plans again
                return out
            return self._run(self._ops)
        except Exception as err:  # a malformed design; never raise
            return [_diag(DESIGN, COMMAND_FAILED, f"{self.description or 'edit'} failed: {err!r}")]

    def revert(self, design: GridDesign) -> list[NetworkDiagnostic]:
        if self._ops is None:
            return []
        try:
            for op in reversed(self._ops):
                op.undo()
        except Exception as err:
            return [_diag(DESIGN, COMMAND_FAILED, f"undo failed: {err!r}")]
        return []

    # -- for subclasses

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        """Check the preconditions (errors into ``diags``) and return the operations;
        must not change the design."""
        raise NotImplementedError

    def _run(self, ops: list[_Op]) -> list[NetworkDiagnostic]:
        done: list[_Op] = []
        try:
            for op in ops:
                op.do()
                done.append(op)
        except Exception as err:
            for op in reversed(done):
                op.undo()
            return [_diag(DESIGN, COMMAND_FAILED, f"{self.description} failed: {err!r}")]
        return []


class Compound(Command):
    """Children applied in order and reverted in reverse order: one undo step. If a
    child fails, the children before it are reverted and nothing is changed."""

    def __init__(self, description: str, children: Sequence[Command] = ()) -> None:
        super().__init__()
        self._description = description
        self.children: list[Command] | None = list(children)

    def _build(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[Command] | None:
        """Subclasses that decide their children on the first apply override this."""
        return self.children

    def affected(self) -> set[str]:
        return set().union(self._affected, *(c.affected() for c in self.children or ()))

    def apply(self, design: GridDesign) -> list[NetworkDiagnostic]:
        try:
            first = self.children is None
            if first:
                diags: list[NetworkDiagnostic] = []
                built = self._build(design, diags)
                if built is None or _has_error(diags):
                    return diags or [_diag(DESIGN, COMMAND_FAILED, "command not applied")]
                self.children = built
            assert self.children is not None
            for i, child in enumerate(self.children):
                out = child.apply(design)
                if _has_error(out):
                    for prev in reversed(self.children[:i]):
                        prev.revert(design)
                    if first:
                        self.children = None
                    return out
            return []
        except Exception as err:
            return [_diag(DESIGN, COMMAND_FAILED, f"{self.description} failed: {err!r}")]

    def revert(self, design: GridDesign) -> list[NetworkDiagnostic]:
        out: list[NetworkDiagnostic] = []
        for child in reversed(self.children or ()):
            out.extend(child.revert(design))
        return out


# ------------------------------------------------------------------ values and fields


_READ_ONLY = {
    "name": "use Rename",
    "bus1": "use Connect",
    "bus2": "use Connect",
    "terminal": "use Connect",
    "windings": "windings are added with the transformer",
    "closed": "use SetSwitchOpen",
}

_POSITIVE = {"kv", "kva", "length_m", "pu", "phases", "norm_amps", "emerg_amps", "tap",
             "min_tap", "max_tap", "num_taps", "vminpu", "vmaxpu", "vreg", "pt_ratio",
             "ct_primary", "xhl", "xht", "xlt", "pmpp_kw"}  # fmt: skip
_NON_NEGATIVE = {"pct_r", "pct_noload_loss", "pct_imag", "ppm_antifloat", "band", "delay_s",
                 "tap_delay_s", "max_tap_change", "irradiance", "kv_base_ln", "r_ohm",
                 "x_ohm", "r1_ohm", "x1_ohm", "r0_ohm", "x0_ohm", "xneut_ohm", "cvr_watts",
                 "cvr_vars"}  # fmt: skip
_POSITIVE_FOR = {(Capacitor, "kvar")}
_MATRICES = ("r_matrix", "x_matrix", "c_matrix")

_FIELD_LABELS = {"kw": "kW", "kvar": "kvar", "kv": "kV", "kva": "kVA", "pct_r": "%R",
                 "xhl": "Xhl", "xht": "Xht", "xlt": "Xlt", "pmpp_kw": "Pmpp",
                 "length_m": "length", "r_matrix": "R matrix", "x_matrix": "X matrix",
                 "c_matrix": "C matrix", "norm_amps": "normal rating",
                 "emerg_amps": "emergency rating", "kv_base_ln": "kV base",
                 "r1_ohm": "R1", "x1_ohm": "X1", "r0_ohm": "R0", "x0_ohm": "X0",
                 "r_ohm": "R", "x_ohm": "X", "pf": "PF", "vreg": "Vreg",
                 "pt_ratio": "PT ratio", "ct_primary": "CT primary"}  # fmt: skip


def _field_label(path: str) -> str:
    parts = path.split(".")
    if len(parts) == 3 and parts[0] == "windings" and parts[1].isdigit():
        return f"winding {int(parts[1]) + 1} {_FIELD_LABELS.get(parts[2], parts[2])}"
    return _FIELD_LABELS.get(path, path.replace("_", " "))


def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _split_optional(tp: Any) -> tuple[Any, bool]:
    if typing.get_origin(tp) in (types.UnionType, typing.Union):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return (args[0] if len(args) == 1 else tp), len(args) < len(typing.get_args(tp))
    return tp, False


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _coerce_type(tp: Any, value: object) -> tuple[Any, str | None]:
    """``value`` as the field type ``tp`` (an int becomes a float for a float field),
    or an error text."""
    inner, nullable = _split_optional(tp)
    if value is None:
        return (None, None) if nullable else (None, "must be set (None is not allowed)")
    if inner is float:
        return (float(value), None) if _is_number(value) else (None, "must be a finite number")
    if inner is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
        return (value, None) if ok else (None, "must be a whole number")
    if inner is bool:
        return (value, None) if isinstance(value, bool) else (None, "must be true or false")
    if inner is str:
        return (value.strip().lower(), None) if isinstance(value, str) else (None, "must be text")
    if typing.get_origin(inner) is tuple:
        item = typing.get_args(inner)[0]
        if not isinstance(value, (list, tuple)):
            return None, "must be a list of numbers"
        out = []
        for v in value:
            got, err = _coerce_type(item, v)
            if err is not None:
                return None, f"has an item that {err}"
            out.append(got)
        return tuple(out), None
    return None, "cannot be set"


def _check_range(grid: GridModel, owner: Any, element: Any, fname: str, value: Any) -> str | None:
    """Range and reference rules of one field (``owner`` is the element or winding)."""
    if value is None:
        return None
    cls = type(owner)
    if (fname in _POSITIVE or (cls, fname) in _POSITIVE_FOR) and not value > 0:
        return "must be greater than 0"
    if fname in _NON_NEGATIVE and not value >= 0:
        return "must not be negative"
    if fname == "conn" and value not in CONNECTIONS:
        return f"must be one of {', '.join(CONNECTIONS)}"
    if fname == "model" and isinstance(owner, Load) and value not in LOAD_MODELS:
        return f"must be one of {', '.join(LOAD_MODELS)}"
    if fname == "pf" and not (-1.0 <= value <= 1.0 and value != 0):
        return "must be between -1 and 1 and not 0"
    if fname == "states" and (not value or any(s not in (0, 1) for s in value)):
        return "must be one or more step states 0 or 1"
    if fname in _MATRICES:
        n = element.phases
        if len(value) != n * n:
            return f"must have {n * n} values ({n} x {n}, row-major)"
    if isinstance(owner, LineCode) and fname == "phases":
        for m in _MATRICES:
            mat = getattr(owner, m)
            if mat is not None and len(mat) != value * value:
                return f"does not fit the {len(mat)}-value matrices; set them to None first"
    if isinstance(owner, Line) and fname == "linecode" and value not in grid.linecodes:
        return f"names linecode {value!r}, which is not in the design"
    if isinstance(owner, Regulator):
        if fname == "transformer":
            t = grid.transformers.get(value)
            if t is None:
                return f"names transformer {value!r}, which is not in the design"
            if not 1 <= owner.winding <= len(t.windings):
                return f"transformer {value!r} has no winding {owner.winding}"
        if fname == "winding":
            t = grid.transformers.get(owner.transformer)
            if t is not None and not 1 <= value <= len(t.windings):
                return f"must be 1..{len(t.windings)} for transformer {t.name}"
        if fname == "pt_phase" and not (value in ("max", "min") or value.isdigit()):
            return 'must be a phase number, "max" or "min"'
        if fname == "monitored_bus" and value and Terminal.parse(value).bus not in grid.buses:
            return f"names bus {value!r}, which is not in the design"
    return None


def _field_target(element: Any, path: str) -> tuple[Any, str, str | None]:
    """(owner object, field name, error) for a SetProperty path."""
    parts = path.split(".") if isinstance(path, str) else []
    if len(parts) == 3 and parts[0] == "windings" and isinstance(element, Transformer):
        if not parts[1].isdigit() or int(parts[1]) >= len(element.windings):
            return None, "", f"has no winding index {parts[1]!r} (0-based)"
        return element.windings[int(parts[1])], parts[2], None
    if len(parts) != 1:
        return None, "", "is not a field"
    return element, parts[0], None


def check_value(
    grid: GridModel, element: Any, path: str, value: object
) -> tuple[Any, Any, str, NetworkDiagnostic | None]:
    """(owner, field, coerced value, error) for setting ``path`` of ``element``."""
    loc = getattr(element, "id", DESIGN)
    owner, fname, err = _field_target(element, path)
    if err is not None:
        return None, "", None, _diag(loc, UNKNOWN_FIELD, f"{path!r} {err}")
    names = {f.name for f in dataclasses.fields(owner)}
    if fname not in names:
        return None, "", None, _diag(loc, UNKNOWN_FIELD, f"{type(owner).__name__} has no field "
                                     f"{fname!r}")  # fmt: skip
    if fname in _READ_ONLY:
        return None, "", None, _diag(loc, READ_ONLY_FIELD, f"{fname} cannot be set here: "
                                     f"{_READ_ONLY[fname]}")  # fmt: skip
    coerced, err = _coerce_type(_hints(type(owner))[fname], value)
    if err is None:
        err = _check_range(grid, owner, element, fname, coerced)
    if err is not None:
        return None, "", None, _diag(loc, BAD_VALUE, f"{_field_label(path)} {err} (got {value!r})")
    return owner, fname, coerced, None


def _set_values(
    grid: GridModel, element: Any, values: Mapping[str, Any], diags: list[NetworkDiagnostic]
) -> None:
    """Set ``values`` on a new element (not yet in the design), each checked as by
    SetProperty."""
    for path, value in values.items():
        owner, fname, coerced, err = check_value(grid, element, path, value)
        if err is not None:
            diags.append(err)
        else:
            setattr(owner, fname, coerced)


# ------------------------------------------------------------------ terminals


def _slots(ref: _Ref) -> list[tuple[Any, str]]:
    """(owner, attribute) of each terminal, in terminal order (1-based for users)."""
    el = ref.obj
    if isinstance(el, Transformer):
        return [(w, "terminal") for w in el.windings]
    if isinstance(el, (Line, Switch)):
        return [(el, "bus1"), (el, "bus2")]
    if isinstance(el, Source):
        return [(el, "bus1")]
    if isinstance(el, (Load, Capacitor, PVSystem)):
        return [(el, "terminal")]
    return []


def _parse_terminal(raw: object) -> Terminal | None:
    if isinstance(raw, Terminal):
        return raw
    if isinstance(raw, str):
        return Terminal.parse(raw)
    return None


def _check_nodes(nodes: object) -> tuple[int, ...] | None:
    if not isinstance(nodes, (list, tuple)):
        return None
    ok = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in nodes)
    return tuple(nodes) if ok else None


def _rated_kv(zone_ll: float, phases: int, conn: str, nodes: tuple[int, ...]) -> float:
    """The rated kV of an element in a zone: LL for 3-phase, delta and 1-phase elements
    between two phases; LN (LL / sqrt 3) for a 1-phase wye element to neutral."""
    phase_nodes = [n for n in nodes if n != 0]
    if phases > 1 or conn == DELTA or len(phase_nodes) >= 2:
        return zone_ll
    return zone_ll / _SQRT3


# ------------------------------------------------------------------ add


class AddBus(Command):
    """Add a bus and its glyph. Without a position the glyph is placed by
    layout.auto_layout with every other bus fixed."""

    def __init__(
        self,
        name: str | None = None,
        position: tuple[float, float] | None = None,
        orientation: int = 0,
        length: float = BUS_MIN,
    ) -> None:
        super().__init__()
        self.name, self.position = name, position
        self.orientation, self.length = orientation, length
        self._description = "Add bus"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        grid, diagram = design.grid, design.diagram
        if self.name is None:
            name = lowest_free([grid.buses], BUS_PREFIX)
        else:
            name = _check_name(self.name, [grid.buses], f"bus.{self.name}", diags)
        loc = f"bus.{name}"
        if self.orientation not in ORIENTATIONS:
            diags.append(_diag(loc, BAD_VALUE, f"orientation {self.orientation!r} is not 0 or 90"))
        if not (_is_number(self.length) and self.length > 0):
            diags.append(_diag(loc, BAD_VALUE, f"length {self.length!r} is not a number > 0"))
        pos = self.position
        if pos is not None and not (len(pos) == 2 and all(_is_number(v) for v in pos)):
            diags.append(_diag(loc, BAD_VALUE, f"position {pos!r} is not two finite numbers"))
        if name is None or diags:
            return None
        bus = Bus(name, 0.0)
        if pos is None:
            tmp = dataclasses.replace(grid, buses={**grid.buses, name: bus})
            fixed = {b: (g.x, g.y) for b, g in diagram.bus_glyphs.items()}
            pos = auto_layout(tmp, fixed).get(name, (0.0, 0.0))
        glyph = BusGlyph(float(pos[0]), float(pos[1]), self.orientation, float(self.length))
        self.name = name
        self._description = f"Add bus {name}"
        self._affected = {loc}
        return [_Put(grid.buses, name, bus), _Put(diagram.bus_glyphs, name, glyph)]


class AddElement(Command):
    """Add one element of ``kind`` (a KINDS key) at ``terminals`` with its glyph.

    Structural defaults only (rule 24, module docstring). ``phases`` overrides the
    phases taken from the bus; ``values`` are fields typed by the user (each checked as
    by SetProperty, winding fields as "windings.<i>.<field>"); ``copy_from`` is the id
    of an element of the same kind whose fields are copied (except name and terminals).
    ``position`` is the glyph position; None = the layout default (shunts in a row
    under their bus, branches at the middle of their buses).
    """

    def __init__(
        self,
        kind: str,
        terminals: Sequence[Terminal | str] = (),
        position: tuple[float, float] | None = None,
        *,
        name: str | None = None,
        phases: int | None = None,
        values: Mapping[str, Any] | None = None,
        copy_from: str | None = None,
    ) -> None:
        super().__init__()
        self.kind, self.terminals, self.position = kind, list(terminals), position
        self.name, self.phases = name, phases
        self.values = dict(values or {})
        self.copy_from = copy_from
        k = KINDS.get(kind) if isinstance(kind, str) else None
        self._description = f"Add {k.label if k else kind}"

    @property
    def element_id(self) -> str:
        k = KINDS[self.kind]
        return f"{k.cls.cls}.{self.name}"  # type: ignore[attr-defined]

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        grid, diagram = design.grid, design.diagram
        kind = KINDS.get(self.kind) if isinstance(self.kind, str) else None
        if kind is None or kind.key == "regcontrol":
            hint = " (use AddRegulator)" if self.kind == "regcontrol" else ""
            diags.append(_diag(DESIGN, UNKNOWN_KIND, f"cannot add {self.kind!r}{hint}"))
            return None
        tables = _name_tables(grid, kind)
        if self.name is None:
            name = lowest_free(tables, kind.prefix)
        else:
            name = _check_name(self.name, tables, f"{kind.cls.cls}.{self.name}", diags)  # type: ignore[attr-defined]
        if name is None:
            return None
        loc = f"{kind.cls.cls}.{name}"  # type: ignore[attr-defined]

        terms: list[Terminal] = []
        for raw in self.terminals:
            t = _parse_terminal(raw)
            nodes = _check_nodes(t.nodes) if t is not None else None
            if t is None or nodes is None:
                diags.append(_diag(loc, BAD_TERMINAL, f"terminal {raw!r} is not <bus>[.<node>...]"))
            elif t.bus not in grid.buses:
                diags.append(_diag(loc, UNKNOWN_BUS, f"bus {t.bus!r} is not in the design"))
            else:
                terms.append(t)
        lo, hi = kind.terminals
        if not lo <= len(self.terminals) <= hi:
            want = str(lo) if lo == hi else f"{lo} to {hi}"
            diags.append(_diag(loc, BAD_TERMINAL, f"a {kind.label} needs {want} terminals, "
                               f"got {len(self.terminals)}"))  # fmt: skip
        if self.phases is not None and not (
            isinstance(self.phases, int) and not isinstance(self.phases, bool) and self.phases >= 1
        ):
            diags.append(_diag(loc, BAD_VALUE, f"phases {self.phases!r} is not an integer >= 1"))
        if diags:
            return None

        template = None
        if self.copy_from is not None:
            ref = resolve(grid, self.copy_from)
            if ref is None:
                diags.append(_diag(loc, UNKNOWN_ELEMENT, f"{self.copy_from!r} is not there"))
            elif ref.kind is not kind:
                diags.append(_diag(loc, WRONG_CLASS, f"{self.copy_from} is not a {kind.label}"))
            elif isinstance(ref.obj, Transformer) and len(ref.obj.windings) != len(terms):
                diags.append(_diag(loc, BAD_TERMINAL, f"{self.copy_from} has "
                                   f"{len(ref.obj.windings)} windings, got {len(terms)} "
                                   "terminals"))  # fmt: skip
            else:
                template = ref.obj
            if diags:
                return None

        graph = GridGraph.build(grid)
        terms, phases = self._phases(graph, terms, template)
        if template is not None:
            el = copy.deepcopy(template)
            el.name = name
            self._connect_copy(el, terms)
        else:
            el = self._new(kind, name, terms, phases, graph, diags)
            if el is None:
                return None
            missing = [f for f in REQUIRED_VALUES.get(kind.key, ()) if f not in self.values]
            if missing:
                diags.append(_diag(loc, VALUE_REQUIRED, f"a new {kind.label} needs "
                                   f"{', '.join(_field_label(f) for f in missing)}"))  # fmt: skip
                return None
        _set_values(grid, el, self.values, diags)
        if diags:
            return None
        zone_kv = kind.key in _KV_FROM_ZONE and template is None and "kv" not in self.values
        if zone_kv and not self._zone_kv(el, graph, loc, diags):
            return None

        ops: list[_Op] = [_Put(getattr(grid, kind.attr), name, el)]
        if kind.drawn:
            ops.append(_Put(diagram.element_glyphs, el.id, self._glyph(grid, diagram, kind, el)))
        self.name = name
        self._description = f"Add {kind.label} {name}"
        self._affected = {el.id}
        return ops

    # -- helpers

    def _phases(
        self, graph: GridGraph, terms: list[Terminal], template: Any
    ) -> tuple[list[Terminal], int]:
        """Phases: given, else the template's, else from the first terminal's explicit
        nodes, else from the nodes available at its bus, else 3. A terminal given
        without nodes on a bus that has only some phases gets those nodes; the second
        terminal of a line or switch then gets the same ones (conductor k to k)."""
        if self.phases is not None:
            return terms, self.phases
        if template is not None:
            return terms, template.phases
        if not terms:
            return terms, 3
        first = terms[0]
        if first.nodes:
            return terms, max(1, len([n for n in first.nodes if n != 0]))
        avail = graph.available_nodes(first.bus)
        if not avail:
            return terms, 3
        if avail != tuple(range(1, len(avail) + 1)):
            terms = [Terminal(first.bus, avail), *terms[1:]]
            if self.kind in ("line", "switch") and len(terms) == 2 and not terms[1].nodes:
                terms[1] = Terminal(terms[1].bus, avail)
        return terms, len(avail)

    def _new(
        self,
        kind: Kind,
        name: str,
        terms: list[Terminal],
        phases: int,
        graph: GridGraph,
        diags: list[NetworkDiagnostic],
    ) -> Any:
        conn = self.values.get("conn", WYE)
        conn = conn if conn in CONNECTIONS else WYE  # a bad conn is reported by _set_values
        key = kind.key
        if key == "vsource":
            t = terms[0]
            return Source(name, t, Terminal(t.bus, (0,) * phases), phases, 0.0, 1.0, 0.0,
                          0.0, 0.0, 0.0, 0.0)  # fmt: skip
        if key == "linecode":
            return LineCode(name, phases, None, None, None)
        if key == "line":
            return Line(name, terms[0], terms[1], phases, 0.0, "")
        if key == "switch":
            return Switch(name, terms[0], terms[1], phases, True, 0.0)
        if key == "transformer":
            zones = graph.voltage_zones()
            windings = []
            for t in terms:
                zone = zones.get(t.bus)
                kv = None if zone is None else _rated_kv(zone, phases, WYE, t.nodes)
                windings.append(Winding(t, WYE, kv, None, None))
            return Transformer(name, phases, windings, None, None, None)
        if key == "load":
            return Load(name, terms[0], phases, conn, 0.0, None, None, LOAD_PQ)
        if key == "capacitor":
            return Capacitor(name, terms[0], phases, conn, 0.0, None)
        if key == "pvsystem":
            return PVSystem(name, terms[0], phases, conn, 0.0, None, None, 1.0, 1.0)
        diags.append(_diag(DESIGN, UNKNOWN_KIND, f"cannot add {key!r}"))
        return None

    @staticmethod
    def _zone_kv(el: Any, graph: GridGraph, loc: str, diags: list[NetworkDiagnostic]) -> bool:
        term = el.terminal
        zone = graph.voltage_zones().get(term.bus)
        if zone is None:
            diags.append(
                _diag(
                    loc,
                    NO_VOLTAGE_ZONE,
                    f"bus.{term.bus} has no voltage zone yet: "
                    "connect it to a source or transformer first, or give kV",
                )
            )
            return False
        el.kv = _rated_kv(zone, el.phases, el.conn, term.nodes)
        return True

    @staticmethod
    def _connect_copy(el: Any, terms: list[Terminal]) -> None:
        if isinstance(el, Transformer):
            for w, t in zip(el.windings, terms, strict=True):
                w.terminal = t
        elif isinstance(el, Source):
            if el.bus2.bus == el.bus1.bus:
                el.bus2 = Terminal(terms[0].bus, el.bus2.nodes)
            el.bus1 = terms[0]
        elif isinstance(el, (Line, Switch)):
            el.bus1, el.bus2 = terms
        elif isinstance(el, (Load, Capacitor, PVSystem)):
            el.terminal = terms[0]

    def _glyph(self, grid: GridModel, diagram: Any, kind: Kind, el: Any) -> ElementGlyph:
        tmp = dataclasses.replace(grid, **{kind.attr: {**getattr(grid, kind.attr), el.name: el}})
        if kind.key in SHUNT_KINDS:
            placed = place_shunts(tmp, diagram.bus_glyphs).get(el.id)
        else:
            placed = place_branches(tmp, diagram.bus_glyphs).get(el.id)
        glyph = placed or ElementGlyph(0.0, 0.0)
        pos = self.position
        if pos is not None and len(pos) == 2 and all(_is_number(v) for v in pos):
            glyph.x, glyph.y = float(pos[0]), float(pos[1])
        return glyph


class _AddRegControl(Command):
    """The RegControl half of AddRegulator; its transformer is named on apply."""

    def __init__(
        self,
        transformer: Callable[[], str],
        winding: int,
        name: str | None,
        values: Mapping[str, Any],
    ) -> None:
        super().__init__()
        self.transformer, self.winding, self.name = transformer, winding, name
        self.values = dict(values)
        self._description = "Add regulator"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        grid = design.grid
        kind = KINDS["regcontrol"]
        tables = _name_tables(grid, kind)
        name = (lowest_free(tables, kind.prefix) if self.name is None
                else _check_name(self.name, tables, f"regcontrol.{self.name}", diags))  # fmt: skip
        if name is None:
            return None
        loc = f"regcontrol.{name}"
        tx = self.transformer()
        t = grid.transformers.get(tx)
        if t is None:
            diags.append(_diag(loc, UNKNOWN_ELEMENT, f"transformer {tx!r} is not in the design"))
            return None
        if not (isinstance(self.winding, int) and 1 <= self.winding <= len(t.windings)):
            diags.append(
                _diag(loc, BAD_VALUE, f"no winding {self.winding!r} (1..{len(t.windings)})")
            )
            return None
        missing = [f for f in REQUIRED_VALUES["regcontrol"] if f not in self.values]
        if missing:
            diags.append(_diag(loc, VALUE_REQUIRED, "a new regulator needs "
                               f"{', '.join(_field_label(f) for f in missing)}"))  # fmt: skip
            return None
        reg = Regulator(name, tx, self.winding, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        _set_values(grid, reg, self.values, diags)
        if diags:
            return None
        self.name = name
        self._description = f"Add regulator {name}"
        self._affected = {reg.id}
        return [_Put(grid.regulators, name, reg)]


class AddRegulator(Compound):
    """The library "Regulator": a single-phase transformer from ``bus1`` to ``bus2``
    plus a RegControl on its ``winding``, as one compound command.

    ``bus1`` without nodes takes the first phase node available at its bus; ``bus2``
    without nodes takes the same node. Winding kV comes from the voltage zone (LN for a
    1-phase wye winding); kVA, %R and Xhl stay unset (draft) unless in
    ``transformer_values``. ``regulator_values`` must give Vreg, band, PT ratio and CT
    primary (REQUIRED_VALUES)."""

    def __init__(
        self,
        bus1: Terminal | str,
        bus2: Terminal | str,
        position: tuple[float, float] | None = None,
        *,
        winding: int = 2,
        transformer_name: str | None = None,
        regulator_name: str | None = None,
        transformer_values: Mapping[str, Any] | None = None,
        regulator_values: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__("Add regulator")
        self.children = None
        self.bus1, self.bus2, self.position = bus1, bus2, position
        self.winding = winding
        self.transformer_name, self.regulator_name = transformer_name, regulator_name
        self.transformer_values = dict(transformer_values or {})
        self.regulator_values = dict(regulator_values or {})

    def _build(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[Command] | None:
        t1, t2 = _parse_terminal(self.bus1), _parse_terminal(self.bus2)
        if t1 is None or t2 is None:
            diags.append(_diag(DESIGN, BAD_TERMINAL, "a regulator needs two terminals "
                               "<bus>[.<node>]"))  # fmt: skip
            return None
        if not t1.nodes and t1.bus in design.grid.buses:
            avail = GridGraph.build(design.grid).available_nodes(t1.bus)
            t1 = Terminal(t1.bus, avail[:1]) if avail else t1
        if not t2.nodes and t1.nodes:
            t2 = Terminal(t2.bus, t1.nodes)
        tx = AddElement(
            "transformer",
            [t1, t2],
            self.position,
            name=self.transformer_name,
            phases=1,
            values=self.transformer_values,
        )
        reg = _AddRegControl(lambda: tx.name or "", self.winding, self.regulator_name,
                             self.regulator_values)  # fmt: skip
        return [tx, reg]

    def apply(self, design: GridDesign) -> list[NetworkDiagnostic]:
        out = super().apply(design)
        if not _has_error(out) and self.children:
            self._description = f"Add regulator {self.children[1].name}"  # type: ignore[attr-defined]
        return out


# ------------------------------------------------------------------ remove


class RemoveElement(Command):
    """Remove one element and its glyph. A transformer takes the regulators that
    control it along; a linecode still used by a line is refused. Buses: RemoveBus."""

    def __init__(self, element_id: str) -> None:
        super().__init__()
        self.element_id = element_id
        self._description = f"Remove {element_id}"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        grid, diagram = design.grid, design.diagram
        ref = resolve(grid, self.element_id)
        if ref is None:
            diags.append(_diag(str(self.element_id), UNKNOWN_ELEMENT, "not in the design"))
            return None
        if ref.kind is None:
            diags.append(_diag(ref.id, WRONG_CLASS, "a bus is removed with RemoveBus"))
            return None
        ops: list[_Op] = []
        if isinstance(ref.obj, LineCode):
            users = [ln.id for ln in grid.lines.values() if ln.linecode == ref.name]
            if users:
                diags.append(_diag(ref.id, IN_USE, f"used by {', '.join(users)}"))
                return None
        if isinstance(ref.obj, Transformer):
            for r in grid.regulators.values():
                if r.transformer == ref.name:
                    ops.append(_Pop(grid.regulators, r.name))
                    self._affected.add(r.id)
        ops.append(_Pop(ref.table, ref.name))
        if ref.kind.drawn and ref.id in diagram.element_glyphs:
            ops.append(_Pop(diagram.element_glyphs, ref.id))
        self._description = f"Remove {ref.label} {ref.name}"
        self._affected.add(ref.id)
        return ops


class _RemoveBusOnly(Command):
    def __init__(self, bus: str) -> None:
        super().__init__()
        self.bus = bus

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        self._affected = {f"bus.{self.bus}"}
        return [_Pop(design.grid.buses, self.bus), _Pop(design.diagram.bus_glyphs, self.bus)]


class RemoveBus(Compound):
    """Remove a bus and everything connected to it (topology.connected_elements, with
    the regulators of removed transformers) as one compound command. A regulator that
    monitors the bus keeps working on its own winding: its monitored bus is cleared."""

    def __init__(self, bus: str) -> None:
        name = bus[4:] if isinstance(bus, str) and bus.startswith("bus.") else bus
        super().__init__(f"Remove bus {name}")
        self.children = None
        self.bus = name

    def _build(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[Command] | None:
        grid = design.grid
        if not isinstance(self.bus, str) or self.bus not in grid.buses:
            diags.append(_diag(f"bus.{self.bus}", UNKNOWN_BUS, "not in the design"))
            return None
        children: list[Command] = [RemoveElement(i) for i in connected_elements(grid, self.bus)]
        removed_tx = {i[len("transformer."):] for i in connected_elements(grid, self.bus)
                      if i.startswith("transformer.")}  # fmt: skip
        for r in grid.regulators.values():
            if (
                r.transformer not in removed_tx
                and r.monitored_bus
                and (Terminal.parse(r.monitored_bus).bus == self.bus)
            ):
                children.insert(0, SetProperty(r.id, "monitored_bus", ""))
        children.append(_RemoveBusOnly(self.bus))
        return children


# ------------------------------------------------------------------ connect


class Connect(Command):
    """Connect terminal ``terminal`` (1-based: bus1 / bus2 / winding k) of an element to
    ``bus`` with ``nodes`` (empty = OpenDSS's 1..phases). Moving a source's bus1 also
    moves its bus2 when that was on the same bus (the source's neutral side)."""

    def __init__(self, element_id: str, terminal: int, bus: str, nodes: Sequence[int] = ()) -> None:
        super().__init__()
        self.element_id, self.terminal, self.bus, self.nodes = element_id, terminal, bus, nodes
        self._description = f"Connect {element_id}"

    def _target_bus(
        self, grid: GridModel, loc: str, diags: list[NetworkDiagnostic]
    ) -> Terminal | None:
        nodes = _check_nodes(self.nodes)
        if nodes is None:
            diags.append(_diag(loc, BAD_TERMINAL, f"nodes {self.nodes!r} are not integers >= 0"))
            return None
        if self.bus not in grid.buses:
            diags.append(_diag(loc, UNKNOWN_BUS, f"bus {self.bus!r} is not in the design"))
            return None
        return Terminal(self.bus, nodes)

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        grid = design.grid
        ref = resolve(grid, self.element_id)
        if ref is None or ref.kind is None:
            code = UNKNOWN_ELEMENT if ref is None else WRONG_CLASS
            diags.append(_diag(str(self.element_id), code, "not an element with terminals"))
            return None
        slots = _slots(ref)
        if not (isinstance(self.terminal, int) and 1 <= self.terminal <= len(slots)):
            diags.append(_diag(ref.id, BAD_TERMINAL, f"has no terminal {self.terminal!r} "
                               f"(1..{len(slots)})"))  # fmt: skip
            return None
        new = self._target_bus(grid, ref.id, diags)
        if new is None:
            return None
        owner, attr = slots[self.terminal - 1]
        old: Terminal = getattr(owner, attr)
        ops: list[_Op] = [_SetAttr(owner, attr, old, new)]
        if isinstance(ref.obj, Source) and ref.obj.bus2.bus == old.bus:
            b2 = ref.obj.bus2
            ops.append(_SetAttr(ref.obj, "bus2", b2, Terminal(new.bus, b2.nodes)))
        self._description = self._text(ref)
        self._affected = {ref.id} | {f"bus.{b}" for b in (old.bus, new.bus) if b}
        return ops

    def _text(self, ref: _Ref) -> str:
        return f"Connect {ref.label} {ref.name} terminal {self.terminal} to bus {self.bus}"


class Disconnect(Connect):
    """Disconnect one terminal: its bus becomes topology.DISCONNECTED (""), which the
    graph ignores and validate.py reports."""

    def __init__(self, element_id: str, terminal: int) -> None:
        super().__init__(element_id, terminal, DISCONNECTED)
        self._description = f"Disconnect {element_id}"

    def _target_bus(
        self, grid: GridModel, loc: str, diags: list[NetworkDiagnostic]
    ) -> Terminal | None:
        return Terminal(DISCONNECTED, ())

    def _text(self, ref: _Ref) -> str:
        return f"Disconnect {ref.label} {ref.name} terminal {self.terminal}"


# ------------------------------------------------------------------ properties


class SetProperty(Command):
    """Set one field of an element or bus (``path``: module docstring) after type and
    range checks. With a ``merge_key`` (one spin-box editing session) consecutive sets
    of the same field merge into one undo step; without one, never."""

    def __init__(self, element_id: str, path: str, value: Any, merge_key: str = "") -> None:
        super().__init__()
        self.element_id, self.path, self.value = element_id, path, value
        self.merge_key = merge_key
        if merge_key:
            self.merge_id = MERGE_SET_PROPERTY
        self._description = f"Set {element_id} {_field_label(str(path))}"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        ref = resolve(design.grid, self.element_id)
        if ref is None:
            diags.append(_diag(str(self.element_id), UNKNOWN_ELEMENT, "not in the design"))
            return None
        owner, fname, value, err = check_value(design.grid, ref.obj, self.path, self.value)
        if err is not None:
            diags.append(dataclasses.replace(err, block=ref.id))
            return None
        self._description = f"Set {ref.label} {ref.name} {_field_label(self.path)}"
        self._affected = {ref.id}
        return [_SetAttr(owner, fname, getattr(owner, fname), value)]

    def merge(self, other: Command) -> bool:
        if not (
            isinstance(other, SetProperty)
            and self.merge_key
            and other.merge_key == self.merge_key
            and (other.element_id, other.path) == (self.element_id, self.path)
            and self._ops
            and other._ops
        ):
            return False
        mine, theirs = self._ops[0], other._ops[0]
        assert isinstance(mine, _SetAttr) and isinstance(theirs, _SetAttr)
        mine.new = theirs.new
        self.value = other.value
        return True

    @property
    def obsolete(self) -> bool:
        op = self._ops[0] if self._ops else None
        return isinstance(op, _SetAttr) and op.old == op.new and bool(self.merge_key)


class Rename(Command):
    """Rename a bus or element and every reference to it: terminals naming a bus (and
    regulators monitoring it), lines naming a linecode, regulators naming a
    transformer, and glyph keys. Dict positions are kept."""

    def __init__(self, element_id: str, new_name: str) -> None:
        super().__init__()
        self.element_id, self.new_name = element_id, new_name
        self._description = f"Rename {element_id}"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        grid, diagram = design.grid, design.diagram
        ref = resolve(grid, self.element_id)
        if ref is None:
            diags.append(_diag(str(self.element_id), UNKNOWN_ELEMENT, "not in the design"))
            return None
        old = ref.name
        if isinstance(self.new_name, str) and self.new_name.strip().lower() == old:
            self._description = f"Rename {ref.label} {old}"
            return []
        new = _check_name(self.new_name, _name_tables(grid, ref.kind), ref.id, diags)
        if new is None:
            return None
        ops: list[_Op] = [_Rekey(ref.table, old, new), _SetAttr(ref.obj, "name", old, new)]
        affected = {ref.id}
        if ref.kind is None:
            ops.append(_Rekey(diagram.bus_glyphs, old, new))
            for el_id in connected_elements(grid, old):
                el_ref = resolve(grid, el_id)
                assert el_ref is not None
                owners = _slots(el_ref)
                if isinstance(el_ref.obj, Source):
                    owners.append((el_ref.obj, "bus2"))
                for owner, attr in owners:
                    t: Terminal = getattr(owner, attr)
                    if t.bus == old:
                        ops.append(_SetAttr(owner, attr, t, Terminal(new, t.nodes)))
                affected.add(el_id)
            for r in grid.regulators.values():
                mon = Terminal.parse(r.monitored_bus) if r.monitored_bus else None
                if mon is not None and mon.bus == old:
                    ops.append(_SetAttr(r, "monitored_bus", r.monitored_bus,
                                        str(Terminal(new, mon.nodes))))  # fmt: skip
                    affected.add(r.id)
            new_id = f"bus.{new}"
        else:
            new_id = f"{ref.obj.cls}.{new}"
            if ref.kind.drawn and ref.id in diagram.element_glyphs:
                ops.append(_Rekey(diagram.element_glyphs, ref.id, new_id))
            if isinstance(ref.obj, LineCode):
                for ln in grid.lines.values():
                    if ln.linecode == old:
                        ops.append(_SetAttr(ln, "linecode", old, new))
                        affected.add(ln.id)
            if isinstance(ref.obj, Transformer):
                for r in grid.regulators.values():
                    if r.transformer == old:
                        ops.append(_SetAttr(r, "transformer", old, new))
                        affected.add(r.id)
        self._description = f"Rename {ref.label} {old} to {new}"
        self._affected = affected | {new_id}
        return ops


class SetSwitchOpen(Command):
    def __init__(self, element_id: str, open: bool) -> None:
        super().__init__()
        self.element_id, self.open = element_id, open
        self._description = f"{'Open' if open else 'Close'} {element_id}"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        ref = resolve(design.grid, self.element_id)
        if ref is None:
            diags.append(_diag(str(self.element_id), UNKNOWN_ELEMENT, "not in the design"))
            return None
        if not isinstance(ref.obj, Switch):
            diags.append(_diag(ref.id, WRONG_CLASS, "is not a switch"))
            return None
        if not isinstance(self.open, bool):
            diags.append(_diag(ref.id, BAD_VALUE, f"open {self.open!r} is not true or false"))
            return None
        self._description = f"{'Open' if self.open else 'Close'} switch {ref.name}"
        self._affected = {ref.id}
        return [_SetAttr(ref.obj, "closed", ref.obj.closed, not self.open)]


# ------------------------------------------------------------------ glyphs (presentation)


def _glyph(design: GridDesign, glyph_id: str) -> BusGlyph | ElementGlyph | None:
    if not isinstance(glyph_id, str):
        return None
    if glyph_id.startswith("bus."):
        return design.diagram.bus_glyphs.get(glyph_id[4:])
    return design.diagram.element_glyphs.get(glyph_id)


class MoveGlyph(Command):
    """Move a glyph ("bus.<name>" or an element id). Presentation only: never changes
    the GridModel. Merges with the next MoveGlyph of the same glyph and ``gesture``
    (one mouse press), so a drag is one undo step and two drags stay two."""

    merge_id = MERGE_MOVE

    def __init__(self, glyph_id: str, x: float, y: float, gesture: int = 0) -> None:
        super().__init__()
        self.glyph_id, self.x, self.y, self.gesture = glyph_id, x, y, gesture
        self._description = f"Move {glyph_id}"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        g = _glyph(design, self.glyph_id)
        if g is None:
            diags.append(_diag(str(self.glyph_id), UNKNOWN_ELEMENT, "has no glyph"))
            return None
        if not (_is_number(self.x) and _is_number(self.y)):
            diags.append(_diag(self.glyph_id, BAD_VALUE, f"position ({self.x!r}, {self.y!r}) "
                               "is not two finite numbers"))  # fmt: skip
            return None
        self._affected = {self.glyph_id}
        return [_SetAttr(g, "x", g.x, float(self.x)), _SetAttr(g, "y", g.y, float(self.y))]

    def merge(self, other: Command) -> bool:
        if not (
            isinstance(other, MoveGlyph)
            and (other.glyph_id, other.gesture) == (self.glyph_id, self.gesture)
            and self._ops
            and other._ops
        ):
            return False
        for mine, theirs in zip(self._ops, other._ops, strict=True):
            assert isinstance(mine, _SetAttr) and isinstance(theirs, _SetAttr)
            mine.new = theirs.new
        self.x, self.y = other.x, other.y
        return True

    @property
    def obsolete(self) -> bool:
        return bool(self._ops) and all(
            isinstance(op, _SetAttr) and op.old == op.new
            for op in self._ops  # type: ignore[union-attr]
        )


class RotateGlyph(Command):
    """Rotate a glyph by ``quarter_turns`` x 90 degrees: an element glyph's rotation
    (0/90/180/270), a bus bar's orientation (0/90). Presentation only."""

    def __init__(self, glyph_id: str, quarter_turns: int = 1) -> None:
        super().__init__()
        self.glyph_id, self.quarter_turns = glyph_id, quarter_turns
        self._description = f"Rotate {glyph_id}"

    def _plan(self, design: GridDesign, diags: list[NetworkDiagnostic]) -> list[_Op] | None:
        g = _glyph(design, self.glyph_id)
        if g is None:
            diags.append(_diag(str(self.glyph_id), UNKNOWN_ELEMENT, "has no glyph"))
            return None
        q = self.quarter_turns
        if not (isinstance(q, int) and not isinstance(q, bool)):
            diags.append(_diag(self.glyph_id, BAD_VALUE, f"quarter turns {q!r}: not an integer"))
            return None
        self._affected = {self.glyph_id}
        if isinstance(g, BusGlyph):
            return [_SetAttr(g, "orientation", g.orientation, (g.orientation + 90 * q) % 180)]
        return [_SetAttr(g, "rotation", g.rotation, (g.rotation + 90 * q) % 360)]
