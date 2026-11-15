"""The .qgrid design file: GridModel + DiagramModel as UTF-8 JSON. Qt-free, engine-free.

Written and read only here (CLAUDE.md "Design file"; schema and rules from spike S5,
scratch/phase5/spike5_file/README.md):

    {"format": "qtspice-grid-design", "version": 1, "base_frequency": 60.0,
     "grid_model": {...}, "diagram": {"bus_glyphs": {...}, "element_glyphs": {...}}}

Writing: a dataclass becomes an object with one key per field (ClassVars are not
fields); tuples and lists become arrays (matrices stay flat row-major); complex becomes
[re, im]; floats use Python's shortest round-trip repr, so every float reads back bit
for bit. GridModel class dicts are arrays in dict order (the key is the element name);
``base_frequency`` is written once, at the top level. Indent 1 (one line per value).
NaN / inf are refused (error Diagnostic, nothing written). The file is written to
``<name>.qgrid.tmp`` and renamed over the target. Results are never stored.

Reading (``load`` / ``loads`` never raise; every problem is a NetworkDiagnostic at the
element id or FILE):

- not UTF-8, not JSON, not an object, wrong "format" -> error;
- "version" missing, not an int, < 1, or newer than SUPPORTED_VERSION -> error; older
  versions are migrated step by step (none yet);
- unknown field at any level -> warning, ignored;
- missing field with a dataclass default -> the default; without one -> error;
- wrong JSON type, non-finite number, duplicate name, matrix not phases x phases, glyph
  inconsistent with the model (diagram.model.check_consistency) -> error;
- int for a float field -> float; null only where the type is ``X | None``;
- any error -> nothing loaded.
"""

from __future__ import annotations

import contextlib
import functools
import json
import math
import types
import typing
from dataclasses import MISSING, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

from core.grid.diagram.model import DiagramModel, check_consistency
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
SUFFIX = ".qgrid"
FILE = "file"
"""Diagnostic location for problems of the whole file."""
INDENT = 1

TOP_KEYS = ("format", "version", "base_frequency", "grid_model", "diagram")

# stable diagnostic codes
IO_ERROR = "io"
NOT_UTF8 = "not_utf8"
NOT_JSON = "not_json"
BAD_FORMAT = "bad_format"
BAD_VERSION = "bad_version"
NEWER_VERSION = "newer_version"
UNKNOWN_FIELD = "unknown_field"
BAD_FIELD = "bad_field"
DUPLICATE = "duplicate"
BAD_MATRIX = "bad_matrix"
SAVE_VALUE = "save_value"

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
"""GridModel class dicts, written as arrays."""
MATRICES: dict[type, tuple[str, ...]] = {LineCode: ("r_matrix", "x_matrix", "c_matrix")}


@dataclass
class GridDesign:
    """What a .qgrid file holds. The base frequency is ``grid.base_frequency``."""

    grid: GridModel
    diagram: DiagramModel


# ------------------------------------------------------------------ writing


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


class _SaveError(ValueError):
    pass


def _plain(obj: Any, tp: Any = None) -> Any:
    """``obj`` as JSON values. ``tp`` is the field's type hint: an int stored in a float
    field is written as a float, so saving a loaded design gives the same bytes."""
    if typing.get_origin(tp) in (types.UnionType, typing.Union):
        inner = [a for a in typing.get_args(tp) if a is not type(None)]
        tp = inner[0] if len(inner) == 1 else None
    if obj is None or isinstance(obj, bool | str):
        return obj
    if isinstance(obj, int):
        return _plain(float(obj)) if tp is float else obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise _SaveError(f"{obj!r} is not a finite number")
        return obj
    if isinstance(obj, complex):
        return [_plain(obj.real), _plain(obj.imag)]
    if is_dataclass(obj) and not isinstance(obj, type):
        hints = _hints(type(obj))
        return {f.name: _plain(getattr(obj, f.name), hints.get(f.name)) for f in fields(obj)}
    args = typing.get_args(tp)
    if isinstance(obj, list | tuple):
        item = args[0] if args else None
        return [_plain(v, item) for v in obj]
    if isinstance(obj, dict):
        value_t = args[1] if len(args) == 2 else None
        return {str(k): _plain(v, value_t) for k, v in obj.items()}
    raise _SaveError(f"cannot write a {type(obj).__name__}")


def _model_plain(model: GridModel) -> dict[str, Any]:
    hints = _hints(GridModel)
    out: dict[str, Any] = {}
    for f in fields(model):
        if f.name == "base_frequency":
            continue  # top level only
        value = getattr(model, f.name)
        if f.name in ELEMENT_LISTS:
            out[f.name] = [_plain(el) for el in value.values()]
        else:
            out[f.name] = _plain(value, hints[f.name])
    return out


def dumps(design: GridDesign) -> str:
    """The file text. Raises ValueError on a value JSON cannot hold (``save`` turns it
    into a Diagnostic)."""
    doc = {
        "format": FORMAT,
        "version": SUPPORTED_VERSION,
        "base_frequency": _plain(design.grid.base_frequency, float),
        "grid_model": _model_plain(design.grid),
        "diagram": _plain(design.diagram),
    }
    return json.dumps(doc, indent=INDENT, allow_nan=False, ensure_ascii=False) + "\n"


def save(path: Path, design: GridDesign) -> list[NetworkDiagnostic]:
    """Write ``path``. On a value JSON cannot hold or a write error, report it and
    leave any existing file unchanged. Never raises."""
    try:
        text = dumps(design)
    except ValueError as exc:
        return [_err(FILE, f"not saved: {exc}", SAVE_VALUE)]
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        tmp.replace(path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        return [_err(FILE, f"cannot write {path.name}: {exc.strerror or exc}", IO_ERROR)]
    return []


# ------------------------------------------------------------------ reading


class _Bad(Exception):
    """One field that cannot be read; becomes an error Diagnostic."""


def _jtype(value: Any) -> str:
    if value is None:
        return "null"
    return {dict: "object", list: "array", str: "string", bool: "true/false"}.get(
        type(value), "number"
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
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            raise _Bad(f"{where}: unsupported type {tp}")
        values = [_value(args[0], v, f"{where}[{i}]", loc, diags) for i, v in enumerate(raw)]
        return tuple(values) if origin is tuple else values
    if origin is dict:
        key_t, val_t = typing.get_args(tp)
        if key_t is not str or not isinstance(raw, dict):
            raise _Bad(f"{where}: object expected, got {_jtype(raw)}")
        return {
            k: _value(val_t, v, f"{where}.{k}", _glyph_loc(loc, k), diags) for k, v in raw.items()
        }
    if is_dataclass(tp):
        return _dataclass(tp, raw, where, loc, diags)
    raise _Bad(f"{where}: unsupported type {tp}")


def _glyph_loc(loc: str, key: str) -> str:
    """Location of a glyph: its element id, or "bus.<name>" for a bus glyph."""
    if loc == "diagram.bus_glyphs":
        return f"bus.{key}"
    if loc == "diagram.element_glyphs":
        return key
    return loc


def _dataclass(cls: type, raw: Any, where: str, loc: str, diags: list[NetworkDiagnostic]) -> Any:
    if not isinstance(raw, dict):
        raise _Bad(f"{where}: object expected, got {_jtype(raw)}")
    hints = _hints(cls)
    known = {f.name: f for f in fields(cls)}
    for key in raw:
        if key not in known:
            msg = f"{where}: unknown field '{key}' ignored".lstrip(": ")
            diags.append(NetworkDiagnostic(loc, WARNING, msg, code=UNKNOWN_FIELD))
    kwargs: dict[str, Any] = {}
    for name, f in known.items():
        sub = f"{where}.{name}".lstrip(".")
        if name in raw:
            sub_loc = f"diagram.{name}" if cls is DiagramModel else loc
            kwargs[name] = _value(hints[name], raw[name], sub, sub_loc, diags)
        elif f.default is MISSING and f.default_factory is MISSING:
            raise _Bad(f"{sub}: missing (no default)")
    return cls(**kwargs)


def _err(loc: str, message: str, code: str) -> NetworkDiagnostic:
    return NetworkDiagnostic(loc, ERROR, message, code=code)


def _read_model(raw: Any, base_frequency: float, diags: list[NetworkDiagnostic]) -> GridModel:
    if not isinstance(raw, dict):
        raise _Bad(f"grid_model: object expected, got {_jtype(raw)}")
    hints = _hints(GridModel)
    known = [f.name for f in fields(GridModel) if f.name != "base_frequency"]
    for key in raw:
        if key not in known:
            msg = f"grid_model: unknown field '{key}' ignored"
            diags.append(NetworkDiagnostic(FILE, WARNING, msg, code=UNKNOWN_FIELD))
    kwargs: dict[str, Any] = {"base_frequency": base_frequency}
    bad = False
    if "name" not in raw:
        diags.append(_err(FILE, "grid_model.name: missing (no default)", BAD_FIELD))
        bad = True
    for name in known:
        if name not in raw:
            continue  # every other GridModel field has a default
        if name not in ELEMENT_LISTS:
            try:
                kwargs[name] = _value(hints[name], raw[name], f"grid_model.{name}", FILE, diags)
            except _Bad as exc:
                diags.append(_err(FILE, str(exc), BAD_FIELD))
                bad = True
            continue
        cls = ELEMENT_LISTS[name]
        items = raw[name]
        if not isinstance(items, list):
            diags.append(_err(FILE, f"grid_model.{name}: array expected", BAD_FIELD))
            bad = True
            continue
        out: dict[str, Any] = {}
        for i, item in enumerate(items):
            el_name = item.get("name") if isinstance(item, dict) else None
            loc = f"{cls.cls}.{el_name}" if isinstance(el_name, str) else f"grid_model.{name}[{i}]"
            try:
                el = _dataclass(cls, item, "", loc, diags)
            except _Bad as exc:
                diags.append(_err(loc, str(exc), BAD_FIELD))
                bad = True
                continue
            if el.name in out:
                diags.append(_err(loc, f"duplicate name '{el.name}' in {name}", DUPLICATE))
                bad = True
                continue
            for m in MATRICES.get(cls, ()):
                values = getattr(el, m)
                if values is not None and len(values) != el.phases * el.phases:
                    msg = f"{m}: {el.phases}x{el.phases} values expected, got {len(values)}"
                    diags.append(_err(loc, msg, BAD_MATRIX))
                    bad = True
            out[el.name] = el
        kwargs[name] = out
    if bad:
        raise _Bad("")
    return GridModel(**kwargs)


def _migrate(doc: dict[str, Any], version: int) -> dict[str, Any]:
    """Bring an older document up to SUPPORTED_VERSION, one version at a time. There is
    only version 1 so far."""
    return doc


def loads(text: str) -> tuple[GridDesign | None, list[NetworkDiagnostic]]:
    """Read a design from file text. Never raises."""
    diags: list[NetworkDiagnostic] = []
    try:
        doc = json.loads(text, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        return None, [_err(FILE, f"not a design file (not JSON: {exc})", NOT_JSON)]
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return None, [_err(FILE, "not a QtSpice grid design file", BAD_FORMAT)]
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        return None, [_err(FILE, "design file version missing or not a whole number", BAD_VERSION)]
    if version > SUPPORTED_VERSION:
        msg = (
            f"design file version {version} was made by a newer QtSpice; "
            f"this version reads up to {SUPPORTED_VERSION}"
        )
        return None, [_err(FILE, msg, NEWER_VERSION)]
    if version < 1:
        return None, [_err(FILE, f"unknown design file version {version}", BAD_VERSION)]
    doc = _migrate(doc, version)
    for key in doc:
        if key not in TOP_KEYS:
            diags.append(
                NetworkDiagnostic(
                    FILE, WARNING, f"unknown field '{key}' ignored", code=UNKNOWN_FIELD
                )
            )
    for key in TOP_KEYS[2:]:
        if key not in doc:
            diags.append(_err(FILE, f"{key}: missing (no default)", BAD_FIELD))
    if _has_error(diags):
        return None, diags
    try:
        base_frequency = _value(float, doc["base_frequency"], "base_frequency", FILE, diags)
        if base_frequency <= 0:
            raise _Bad("base_frequency: must be > 0 Hz")
    except _Bad as exc:
        return None, [*diags, _err(FILE, str(exc), BAD_FIELD)]
    model: GridModel | None = None
    diagram: DiagramModel | None = None
    try:
        model = _read_model(doc["grid_model"], base_frequency, diags)
    except _Bad as exc:
        if str(exc):
            diags.append(_err(FILE, str(exc), BAD_FIELD))
    try:
        diagram = _dataclass(DiagramModel, doc["diagram"], "diagram", FILE, diags)
    except _Bad as exc:
        diags.append(_err(FILE, str(exc), BAD_FIELD))
    if model is None or diagram is None or _has_error(diags):
        return None, diags
    diags += check_consistency(model, diagram)
    if _has_error(diags):
        return None, diags
    return GridDesign(model, diagram), diags


def load(path: Path) -> tuple[GridDesign | None, list[NetworkDiagnostic]]:
    """Read a .qgrid file. Never raises."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, [_err(FILE, f"cannot read {path.name}: {exc.strerror or exc}", IO_ERROR)]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, [_err(FILE, f"{path.name} is not UTF-8 text", NOT_UTF8)]
    return loads(text)


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in a design file")


def _has_error(diags: list[NetworkDiagnostic]) -> bool:
    return any(d.severity == ERROR for d in diags)
