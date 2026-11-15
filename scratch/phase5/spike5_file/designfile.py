"""S5 spike: the .qgrid design file (Qt-free, engine-free).

A plain dataclass <-> JSON converter for GridModel + DiagramModel, and the loader rules
proposed for later versions. In the product this becomes core/grid/design_io.py (D3);
DiagramModel moves to core/grid/diagram/model.py.

Encoding (see README.md "Schema"):

- dataclass -> JSON object with one key per dataclass field (ClassVars such as ``cls``
  are not fields, so they are not written);
- tuple / list -> JSON array; a matrix stays the model's flat row-major list
  (phases * phases values);
- complex -> [re, im] (GridModel has no complex field today; results are never stored);
- float -> JSON number written with Python's shortest round-trip repr, so every finite
  float is read back bit for bit. NaN and +-inf are refused at save (not JSON);
- GridModel's class dicts (buses, lines, ...) -> JSON arrays in dict order; the dict key
  is each element's ``name``. Order is part of an exact model (S2), and a JSON object's
  key order is not something other tools keep;
- base_frequency is written once, at the top level, not inside "grid_model".

Loader rules (``load`` never raises):

- not UTF-8 / not JSON / not an object / wrong "format" -> error, nothing loaded;
- "version" not an int, < 1 or > SUPPORTED_VERSION -> error, nothing loaded;
- unknown field (at any level) -> warning, ignored;
- missing field that has a dataclass default -> that default, no Diagnostic;
- missing field without a default (base_frequency, an element's name, kV, ...) -> error;
- wrong JSON type, NaN/inf, duplicate name, matrix of the wrong size -> error;
- any error -> nothing loaded (no partial design; it would break rule 21).
"""

from __future__ import annotations

import json
import math
import types
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from core.grid.model import (
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
    Transformer,
)
from core.netlist.model import ERROR, WARNING
from core.physical.component import NetworkDiagnostic

FORMAT = "qtspice-grid-design"
SUPPORTED_VERSION = 1
FILE = "file"
"""Location for problems of the whole file."""


# ------------------------------------------------------------------ diagram model


@dataclass
class BusGlyph:
    x: float
    y: float
    orientation: int = 0
    """0 = horizontal bus bar, 90 = vertical."""
    length: float = 4.0


@dataclass
class ElementGlyph:
    x: float
    y: float
    rotation: int = 0
    """0 / 90 / 180 / 270."""
    label_dx: float = 0.0
    label_dy: float = 0.0


@dataclass
class DiagramModel:
    bus_glyphs: dict[str, BusGlyph] = field(default_factory=dict)
    """Bus name -> glyph."""
    element_glyphs: dict[str, ElementGlyph] = field(default_factory=dict)
    """Element id ("load.671", "line.650632") -> glyph."""


# GridModel class dicts written as arrays, in GridModel field order.
ELEMENT_LISTS: dict[str, type] = {
    "buses": Bus,
    "sources": Source,
    "linecodes": LineCode,
    "lines": Line,
    "switches": Switch,
    "transformers": Transformer,
    "regulators": Regulator,
    "loads": Load,
    "capacitors": Capacitor,
    "pvsystems": PVSystem,
}
MATRICES = {LineCode: ("r_matrix", "x_matrix", "c_matrix")}


# ------------------------------------------------------------------ to plain


class SaveError(ValueError):
    pass


def to_plain(obj: Any) -> Any:
    """Any model value -> JSON-ready dicts, lists, str, int, float, bool, None."""
    if obj is None or isinstance(obj, bool | int | str):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise SaveError(f"{obj!r} is not a finite number")
        return obj
    if isinstance(obj, complex):
        return [to_plain(obj.real), to_plain(obj.imag)]
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list | tuple):
        return [to_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    raise SaveError(f"cannot write a {type(obj).__name__}")


def model_to_plain(model: GridModel) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(model):
        if f.name == "base_frequency":
            continue  # top level only
        value = getattr(model, f.name)
        if f.name in ELEMENT_LISTS:
            out[f.name] = [to_plain(el) for el in value.values()]
        else:
            out[f.name] = to_plain(value)
    return out


def document(model: GridModel, diagram: DiagramModel) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": SUPPORTED_VERSION,
        "base_frequency": to_plain(model.base_frequency),
        "grid_model": model_to_plain(model),
        "diagram": to_plain(diagram),
    }


def dumps(model: GridModel, diagram: DiagramModel, indent: int | None = 1) -> str:
    return json.dumps(document(model, diagram), indent=indent, allow_nan=False) + "\n"


def save(
    path: Path, model: GridModel, diagram: DiagramModel, indent: int | None = 1
) -> list[NetworkDiagnostic]:
    """Write ``path``; on a value that JSON cannot hold, write nothing and report it."""
    try:
        text = dumps(model, diagram, indent)
    except (SaveError, ValueError) as exc:
        return [NetworkDiagnostic(FILE, ERROR, f"not saved: {exc}", code="save_value")]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)  # never leave a half-written design
    return []


# ------------------------------------------------------------------ from plain


class _Bad(Exception):
    """One field that cannot be read; turned into an error Diagnostic."""


_HINTS: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def _jtype(value: Any) -> str:
    return {dict: "object", list: "array", str: "string", bool: "true/false"}.get(
        type(value), "null" if value is None else "number"
    )


def _value(tp: Any, raw: Any, where: str, loc: str, diags: list[NetworkDiagnostic]) -> Any:
    origin = typing.get_origin(tp)
    if origin in (types.UnionType, typing.Union):
        args = typing.get_args(tp)
        if raw is None and type(None) in args:
            return None
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1:
            return _value(inner[0], raw, where, loc, diags)
        raise _Bad(f"{where}: unsupported type {tp}")
    if tp is float:
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise _Bad(f"{where}: number expected, got {_jtype(raw)}")
        value = float(raw)
        if not math.isfinite(value):
            raise _Bad(f"{where}: {raw!r} is not a finite number")
        return value
    if tp is int:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise _Bad(f"{where}: whole number expected, got {raw!r}")
        return raw
    if tp is bool:
        if not isinstance(raw, bool):
            raise _Bad(f"{where}: true/false expected, got {_jtype(raw)}")
        return raw
    if tp is str:
        if not isinstance(raw, str):
            raise _Bad(f"{where}: string expected, got {_jtype(raw)}")
        return raw
    if tp is complex:
        if not (isinstance(raw, list) and len(raw) == 2):
            raise _Bad(f"{where}: [re, im] expected")
        return complex(
            _value(float, raw[0], where, loc, diags), _value(float, raw[1], where, loc, diags)
        )
    if origin in (tuple, list):
        args = typing.get_args(tp)
        if not isinstance(raw, list):
            raise _Bad(f"{where}: array expected, got {_jtype(raw)}")
        item = args[0]
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            raise _Bad(f"{where}: unsupported type {tp}")
        values = [_value(item, v, f"{where}[{i}]", loc, diags) for i, v in enumerate(raw)]
        return tuple(values) if origin is tuple else values
    if origin is dict:
        key_t, val_t = typing.get_args(tp)
        if key_t is not str or not isinstance(raw, dict):
            raise _Bad(f"{where}: object expected, got {_jtype(raw)}")
        return {k: _value(val_t, v, f"{where}.{k}", loc, diags) for k, v in raw.items()}
    if is_dataclass(tp):
        return _dataclass(tp, raw, where, loc, diags)
    raise _Bad(f"{where}: unsupported type {tp}")


def _dataclass(cls: type, raw: Any, where: str, loc: str, diags: list[NetworkDiagnostic]) -> Any:
    if not isinstance(raw, dict):
        raise _Bad(f"{where}: object expected, got {_jtype(raw)}")
    hints = _hints(cls)
    known = {f.name: f for f in fields(cls)}
    for key in raw:
        if key not in known:
            diags.append(
                NetworkDiagnostic(
                    loc, WARNING, f"{where}: unknown field '{key}' ignored", code="unknown_field"
                )
            )
    kwargs: dict[str, Any] = {}
    for name, f in known.items():
        if name in raw:
            kwargs[name] = _value(hints[name], raw[name], f"{where}.{name}".lstrip("."), loc, diags)
        elif f.default is MISSING and f.default_factory is MISSING:
            raise _Bad(f"{where}.{name}: missing (no default)".lstrip("."))
        # else: missing with a default -> the dataclass default
    return cls(**kwargs)


def _err(loc: str, message: str, code: str) -> NetworkDiagnostic:
    return NetworkDiagnostic(loc, ERROR, message, code=code)


def _read_model(raw: Any, base_frequency: float, diags: list[NetworkDiagnostic]) -> GridModel:
    if not isinstance(raw, dict):
        raise _Bad("grid_model: object expected")
    hints = _hints(GridModel)
    known = {f.name for f in fields(GridModel)} - {"base_frequency"}
    for key in raw:
        if key not in known:
            msg = f"grid_model: unknown field '{key}' ignored"
            diags.append(NetworkDiagnostic(FILE, WARNING, msg, code="unknown_field"))
    kwargs: dict[str, Any] = {"base_frequency": base_frequency}
    bad = False
    for name in known:
        if name not in raw:
            continue  # every GridModel field but name/base_frequency has a default
        if name not in ELEMENT_LISTS:
            try:
                kwargs[name] = _value(hints[name], raw[name], f"grid_model.{name}", FILE, diags)
            except _Bad as exc:
                diags.append(_err(FILE, str(exc), "bad_field"))
                bad = True
            continue
        cls = ELEMENT_LISTS[name]
        items = raw[name]
        if not isinstance(items, list):
            diags.append(_err(FILE, f"grid_model.{name}: array expected", "bad_field"))
            bad = True
            continue
        out: dict[str, Any] = {}
        for i, item in enumerate(items):
            el_name = item.get("name") if isinstance(item, dict) else None
            loc = f"{cls.cls}.{el_name}" if isinstance(el_name, str) else f"grid_model.{name}[{i}]"
            try:
                el = _dataclass(cls, item, "", loc, diags)
            except _Bad as exc:
                diags.append(_err(loc, str(exc) or "bad element", "bad_field"))
                bad = True
                continue
            if el.name in out:
                diags.append(_err(loc, f"duplicate name '{el.name}' in {name}", "duplicate"))
                bad = True
                continue
            for m in MATRICES.get(cls, ()):
                if len(getattr(el, m)) != el.phases * el.phases:
                    diags.append(
                        _err(loc, f"{m}: {el.phases}x{el.phases} values expected", "bad_matrix")
                    )
                    bad = True
            out[el.name] = el
        kwargs[name] = out
    if "name" not in raw:
        diags.append(_err(FILE, "grid_model.name: missing (no default)", "bad_field"))
        bad = True
    if bad:
        raise _Bad("")
    return GridModel(**kwargs)


def loads(text: str) -> tuple[GridModel | None, DiagramModel | None, list[NetworkDiagnostic]]:
    diags: list[NetworkDiagnostic] = []
    try:
        doc = json.loads(text)
    except ValueError as exc:  # JSONDecodeError; NaN/Infinity are caught below
        return None, None, [_err(FILE, f"not a design file (not JSON: {exc})", "not_json")]
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return None, None, [_err(FILE, "not a QtSpice grid design file", "bad_format")]
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        return (
            None,
            None,
            [_err(FILE, "design file version missing or not a number", "bad_version")],
        )
    if version > SUPPORTED_VERSION:
        msg = (
            f"design file version {version} was made by a newer QtSpice; "
            f"this version reads up to {SUPPORTED_VERSION}"
        )
        return None, None, [_err(FILE, msg, "newer_version")]
    if version < 1:
        return None, None, [_err(FILE, f"unknown design file version {version}", "bad_version")]
    # (version < SUPPORTED_VERSION: migrate(doc, version) here once there is a version 2)
    for key in doc:
        if key not in ("format", "version", "base_frequency", "grid_model", "diagram"):
            diags.append(
                NetworkDiagnostic(
                    FILE, WARNING, f"unknown field '{key}' ignored", code="unknown_field"
                )
            )
    for key in ("base_frequency", "grid_model", "diagram"):
        if key not in doc:
            diags.append(_err(FILE, f"{key}: missing (no default)", "bad_field"))
    if any(d.severity == ERROR for d in diags):
        return None, None, diags
    try:
        base_frequency = _value(float, doc["base_frequency"], "base_frequency", FILE, diags)
        if base_frequency <= 0:
            raise _Bad("base_frequency: must be > 0")
    except _Bad as exc:
        return None, None, [*diags, _err(FILE, str(exc), "bad_field")]
    model: GridModel | None = None
    diagram: DiagramModel | None = None
    try:
        model = _read_model(doc["grid_model"], base_frequency, diags)
    except _Bad as exc:
        if str(exc):
            diags.append(_err(FILE, str(exc), "bad_field"))
    try:
        diagram = _dataclass(DiagramModel, doc["diagram"], "diagram", FILE, diags)
    except _Bad as exc:
        diags.append(_err(FILE, str(exc), "bad_field"))
    if model is None or diagram is None or any(d.severity == ERROR for d in diags):
        return None, None, diags
    return model, diagram, diags


def load(path: Path) -> tuple[GridModel | None, DiagramModel | None, list[NetworkDiagnostic]]:
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        return None, None, [_err(FILE, f"cannot read {path.name}: {exc.strerror}", "io")]
    except UnicodeDecodeError:
        return None, None, [_err(FILE, f"{path.name} is not UTF-8 text", "not_utf8")]
    return loads(text)


# ------------------------------------------------------------------ exactness


def float_mismatches(a: Any, b: Any, where: str = "") -> list[str]:
    """Every place where a and b differ in type, or in a float's bit pattern (so -0.0 vs
    0.0 counts, NaN never occurs). Also dict key order. [] = exactly equal."""
    if type(a) is not type(b):
        return [f"{where}: {type(a).__name__} != {type(b).__name__}"]
    if isinstance(a, float):
        return [] if a.hex() == b.hex() else [f"{where}: {a!r} != {b!r}"]
    if is_dataclass(a):
        out: list[str] = []
        for f in fields(a):
            out += float_mismatches(getattr(a, f.name), getattr(b, f.name), f"{where}.{f.name}")
        return out
    if isinstance(a, dict):
        if list(a) != list(b):
            return [f"{where}: keys or key order differ"]
        return [m for k in a for m in float_mismatches(a[k], b[k], f"{where}[{k}]")]
    if isinstance(a, list | tuple):
        if len(a) != len(b):
            return [f"{where}: length {len(a)} != {len(b)}"]
        return [
            m
            for i, (x, y) in enumerate(zip(a, b, strict=True))
            for m in float_mismatches(x, y, f"{where}[{i}]")
        ]
    return [] if a == b else [f"{where}: {a!r} != {b!r}"]


def count_floats(obj: Any) -> int:
    if isinstance(obj, float):
        return 1
    if is_dataclass(obj):
        return sum(count_floats(getattr(obj, f.name)) for f in fields(obj))
    if isinstance(obj, dict):
        return sum(count_floats(v) for v in obj.values())
    if isinstance(obj, list | tuple):
        return sum(count_floats(v) for v in obj)
    return 0
