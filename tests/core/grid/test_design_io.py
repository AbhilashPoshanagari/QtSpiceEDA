"""core/grid/design_io.py: the .qgrid file. Round trip exact (equal dataclasses, same
bytes on re-save), and every corrupted, unknown-version or incomplete file gives
Diagnostics instead of an exception."""

from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path
from typing import Any

import pytest

from core.grid import design_io as io
from core.grid.diagram import model as dm
from core.grid.diagram.layout import build_diagram
from core.grid.model import GridModel, LineCode
from core.netlist.model import ERROR, WARNING
from tests.core.grid.test_validate import good_model

FEEDERS = ["ieee13", "ieee34", "ieee123"]


def _design(m: GridModel | None = None) -> io.GridDesign:
    m = m or good_model()
    return io.GridDesign(m, build_diagram(m))


def _doc(design: io.GridDesign | None = None) -> dict[str, Any]:
    return json.loads(io.dumps(design or _design()))


def _codes(diags) -> list[str]:
    return [d.code for d in diags]


def _round_trip(tmp_path: Path, design: io.GridDesign) -> None:
    path = tmp_path / "d.qgrid"
    assert io.save(path, design) == []
    first = path.read_bytes()
    loaded, diags = io.load(path)
    assert diags == []
    assert loaded == design
    assert loaded is not None
    # key order of every dict is part of an exact model
    assert list(loaded.grid.lines) == list(design.grid.lines)
    assert list(loaded.diagram.element_glyphs) == list(design.diagram.element_glyphs)
    assert io.save(path, loaded) == []
    assert path.read_bytes() == first
    assert not (tmp_path / "d.qgrid.tmp").exists()


# ---- round trip


def test_round_trip_good_model(tmp_path: Path) -> None:
    _round_trip(tmp_path, _design())


def test_round_trip_draft_model(tmp_path: Path) -> None:
    m = good_model()
    m.derive_voltage_bases = True
    m.linecodes["draft"] = LineCode("draft", 3, None, None, None)
    m.loads["ld3"].kw = None
    m.transformers["t1"].windings[1].kva = None
    m.capacitors["cap1"].kvar = None
    _round_trip(tmp_path, _design(m))


def test_floats_are_bit_identical(tmp_path: Path) -> None:
    m = good_model()
    rng = random.Random(5)
    m.buses["a"].kv_base_ln = 1 / 3
    m.buses["b"].x, m.buses["b"].y = -0.0, 5e-324
    m.lines["l1"].length_m = 1e308
    m.sources["s"].angle_deg = rng.random()
    design = _design(m)
    path = tmp_path / "f.qgrid"
    io.save(path, design)
    loaded, _ = io.load(path)
    assert loaded is not None
    assert math.copysign(1, loaded.grid.buses["b"].x) == -1
    assert loaded.grid.buses["b"].y == 5e-324
    for a, b in ((m.buses["a"].kv_base_ln, loaded.grid.buses["a"].kv_base_ln),
                 (m.sources["s"].angle_deg, loaded.grid.sources["s"].angle_deg)):  # fmt: skip
        assert a.hex() == b.hex()


def test_file_layout(tmp_path: Path) -> None:
    path = tmp_path / "f.qgrid"
    io.save(path, _design())
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert list(doc) == list(io.TOP_KEYS)
    assert doc["format"] == io.FORMAT and doc["version"] == 1
    assert doc["base_frequency"] == 50.0
    assert "base_frequency" not in doc["grid_model"]
    assert isinstance(doc["grid_model"]["lines"], list)
    assert doc["grid_model"]["lines"][0]["bus1"] == {"bus": "src", "nodes": [1, 2, 3]}
    assert "cls" not in doc["grid_model"]["lines"][0]
    assert list(doc["diagram"]) == ["bus_glyphs", "element_glyphs"]
    assert "src" in doc["diagram"]["bus_glyphs"]
    assert text.endswith("\n") and "\r" not in text
    assert text.startswith('{\n "format"')  # indent 1


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_round_trip_ieee(tmp_path: Path, feeder: str) -> None:
    pytest.importorskip("dss")
    from tests.core.grid.test_topology import _feeder_model

    m = copy.deepcopy(_feeder_model(feeder))
    design = _design(m)
    assert dm.check_consistency(design.grid, design.diagram) == []
    _round_trip(tmp_path, design)


# ---- saving


def test_save_refuses_non_finite_and_keeps_old_file(tmp_path: Path) -> None:
    path = tmp_path / "d.qgrid"
    io.save(path, _design())
    before = path.read_bytes()
    bad = _design()
    bad.grid.lines["l1"].length_m = math.nan
    diags = io.save(path, bad)
    assert _codes(diags) == [io.SAVE_VALUE] and diags[0].severity == ERROR
    assert path.read_bytes() == before
    bad.grid.lines["l1"].length_m = 1.0
    bad.diagram.bus_glyphs["a"].x = math.inf
    assert _codes(io.save(path, bad)) == [io.SAVE_VALUE]
    assert path.read_bytes() == before


def test_save_to_missing_folder_is_a_diagnostic(tmp_path: Path) -> None:
    diags = io.save(tmp_path / "no" / "such" / "d.qgrid", _design())
    assert _codes(diags) == [io.IO_ERROR]


# ---- loading bad files


def _load_text(tmp_path: Path, text: str | bytes):
    path = tmp_path / "x.qgrid"
    if isinstance(text, str):
        text = text.encode("utf-8")
    path.write_bytes(text)
    design, diags = io.load(path)
    if design is None:
        assert any(d.severity == ERROR for d in diags)
    return design, diags


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", io.NOT_JSON),
        ("{", io.NOT_JSON),
        ("[1, 2]", io.BAD_FORMAT),
        ('"x"', io.BAD_FORMAT),
        ('{"format": "something-else", "version": 1}', io.BAD_FORMAT),
        ('{"format": "qtspice-grid-design"}', io.BAD_VERSION),
        ('{"format": "qtspice-grid-design", "version": "1"}', io.BAD_VERSION),
        ('{"format": "qtspice-grid-design", "version": 1.0}', io.BAD_VERSION),
        ('{"format": "qtspice-grid-design", "version": true}', io.BAD_VERSION),
        ('{"format": "qtspice-grid-design", "version": 0}', io.BAD_VERSION),
        ('{"format": "qtspice-grid-design", "version": 2}', io.NEWER_VERSION),
        ('{"format": "qtspice-grid-design", "version": 1}', io.BAD_FIELD),
        ("[" * 100000, io.NOT_JSON),
    ],
)
def test_bad_documents(tmp_path: Path, text: str, code: str) -> None:
    design, diags = _load_text(tmp_path, text)
    assert design is None
    assert code in _codes(diags)


def test_not_utf8_and_missing_file(tmp_path: Path) -> None:
    design, diags = _load_text(tmp_path, b"\xff\xfe{\x00")
    assert design is None and _codes(diags) == [io.NOT_UTF8]
    design, diags = io.load(tmp_path / "missing.qgrid")
    assert design is None and _codes(diags) == [io.IO_ERROR]


def test_future_version_message(tmp_path: Path) -> None:
    doc = _doc()
    doc["version"] = 7
    design, diags = _load_text(tmp_path, json.dumps(doc))
    assert design is None
    assert _codes(diags) == [io.NEWER_VERSION] and "newer QtSpice" in diags[0].message


def _broken(mutate) -> dict[str, Any]:
    doc = _doc()
    mutate(doc)
    return doc


def _line(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["grid_model"]["lines"][0]


@pytest.mark.parametrize(
    ("mutate", "code", "where"),
    [
        (lambda d: d.pop("base_frequency"), io.BAD_FIELD, io.FILE),
        (lambda d: d.__setitem__("base_frequency", 0), io.BAD_FIELD, io.FILE),
        (lambda d: d.__setitem__("base_frequency", "60"), io.BAD_FIELD, io.FILE),
        (lambda d: d.pop("diagram"), io.BAD_FIELD, io.FILE),
        (lambda d: d.__setitem__("grid_model", []), io.BAD_FIELD, io.FILE),
        (lambda d: d["grid_model"].pop("name"), io.BAD_FIELD, io.FILE),
        (lambda d: d["grid_model"].__setitem__("lines", {}), io.BAD_FIELD, io.FILE),
        (lambda d: _line(d).pop("length_m"), io.BAD_FIELD, "line.l1"),
        (lambda d: _line(d).__setitem__("length_m", "100"), io.BAD_FIELD, "line.l1"),
        (lambda d: _line(d).__setitem__("phases", 3.0), io.BAD_FIELD, "line.l1"),
        (lambda d: _line(d).__setitem__("enabled", 1), io.BAD_FIELD, "line.l1"),
        (lambda d: _line(d).__setitem__("linecode", None), io.BAD_FIELD, "line.l1"),
        (lambda d: _line(d)["bus1"].__setitem__("nodes", "1.2.3"), io.BAD_FIELD, "line.l1"),
        (
            lambda d: d["grid_model"]["lines"].append(dict(_line(d))),
            io.DUPLICATE,
            "line.l1",
        ),
        (
            lambda d: d["grid_model"]["linecodes"][0].__setitem__("r_matrix", [0.1, 0.2]),
            io.BAD_MATRIX,
            "linecode.lc3",
        ),
        (lambda d: d["grid_model"]["buses"].__setitem__(0, 5), io.BAD_FIELD, "grid_model.buses[0]"),
        (lambda d: d["diagram"]["bus_glyphs"].pop("a"), dm.MISSING_GLYPH, "bus.a"),
        (
            lambda d: d["diagram"]["element_glyphs"].__setitem__("load.zz", {"x": 0, "y": 0}),
            dm.ORPHAN_GLYPH,
            "load.zz",
        ),
        (lambda d: d["diagram"]["bus_glyphs"]["a"].pop("x"), io.BAD_FIELD, io.FILE),
        (
            lambda d: d["diagram"]["element_glyphs"]["line.l1"].__setitem__("rotation", 45),
            dm.BAD_GLYPH,
            "line.l1",
        ),
        (lambda d: d["diagram"].__setitem__("bus_glyphs", []), io.BAD_FIELD, io.FILE),
    ],
)
def test_bad_fields_load_nothing(tmp_path: Path, mutate, code: str, where: str) -> None:
    design, diags = _load_text(tmp_path, json.dumps(_broken(mutate)))
    assert design is None
    assert any(d.code == code and d.block == where and d.severity == ERROR for d in diags), diags


def test_nan_in_file_is_an_error(tmp_path: Path) -> None:
    text = io.dumps(_design()).replace('"length_m": 100.0', '"length_m": NaN', 1)
    assert "NaN" in text
    design, diags = _load_text(tmp_path, text)
    assert design is None and _codes(diags) == [io.NOT_JSON]


def test_unknown_fields_warn_and_load(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["extra"] = 1
        d["grid_model"]["future"] = []
        _line(d)["colour"] = "red"
        d["diagram"]["bus_glyphs"]["a"]["style"] = 2
        d["diagram"]["layers"] = {}

    design, diags = _load_text(tmp_path, json.dumps(_broken(mutate)))
    assert design == _design()
    assert _codes(diags) == [io.UNKNOWN_FIELD] * 5
    assert all(d.severity == WARNING for d in diags)
    assert {d.block for d in diags} == {io.FILE, "line.l1", "bus.a"}


def test_missing_fields_with_defaults_take_the_default(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _line(d).pop("enabled")
        _line(d).pop("norm_amps")
        d["grid_model"].pop("pvsystems")
        d["grid_model"].pop("voltage_bases")
        d["diagram"]["element_glyphs"]["line.l1"].pop("rotation")
        d["diagram"]["bus_glyphs"]["a"].pop("length")

    design, diags = _load_text(tmp_path, json.dumps(_broken(mutate)))
    assert diags == [] and design is not None
    assert design.grid.lines["l1"].enabled is True and design.grid.lines["l1"].norm_amps is None
    assert design.grid.pvsystems == {} and design.grid.voltage_bases == []
    assert design.diagram.element_glyphs["line.l1"].rotation == 0
    assert design.diagram.bus_glyphs["a"].length == dm.BusGlyph(0, 0).length


def test_int_for_float_is_read_as_float(tmp_path: Path) -> None:
    doc = _doc()
    doc["base_frequency"] = 50
    _line(doc)["length_m"] = 100
    design, diags = _load_text(tmp_path, json.dumps(doc))
    assert diags == [] and design is not None
    assert type(design.grid.base_frequency) is float
    assert type(design.grid.lines["l1"].length_m) is float


def test_random_corruption_never_raises(tmp_path: Path) -> None:
    good = io.dumps(_design()).encode("utf-8")
    rng = random.Random(11)
    for _ in range(300):
        data = bytearray(good)
        kind = rng.randrange(3)
        if kind == 0:
            data = data[: rng.randrange(len(data))]
        elif kind == 1:
            for _ in range(rng.randrange(1, 8)):
                data[rng.randrange(len(data))] = rng.randrange(256)
        else:
            i = rng.randrange(len(data))
            del data[i : i + rng.randrange(1, 40)]
        design, diags = io.loads(data.decode("utf-8", errors="replace"))
        assert design is not None or any(d.severity == ERROR for d in diags)
