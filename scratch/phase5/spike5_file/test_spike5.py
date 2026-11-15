"""S5 spike checks: `pytest -q scratch/phase5/spike5_file` (not part of `pytest -q`)."""

from __future__ import annotations

import json
import math
import random
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import designfile as df  # noqa: E402
from feeders import FEEDERS, diagram_from_coordinates, extracted  # noqa: E402

from core.grid.engines.opendss.engine import OpenDSSEngine  # noqa: E402
from core.grid.model import GridModel, LineCode  # noqa: E402
from core.netlist.model import ERROR, WARNING  # noqa: E402

needs_dss = pytest.mark.skipif(not OpenDSSEngine.available(), reason="needs dss-python")


@pytest.fixture(scope="module", params=FEEDERS)
def feeder(request: Any) -> tuple[GridModel, df.DiagramModel]:
    if not OpenDSSEngine.available():
        pytest.skip("needs dss-python")
    model = extracted(request.param)
    return model, diagram_from_coordinates(model)


def _small() -> tuple[GridModel, df.DiagramModel]:
    model = GridModel("tiny", 50.0, [11.0])
    model.linecodes["lc"] = LineCode("lc", 1, (0.1,), (0.2,), (3.0,), 400.0, 600.0)
    return model, df.DiagramModel({}, {"linecode.lc": df.ElementGlyph(1.0, 2.0)})


def _doc(text: str) -> dict[str, Any]:
    return json.loads(text)


# ------------------------------------------------------------------ IEEE feeders


@needs_dss
def test_every_element_and_bus_has_a_glyph(feeder: Any) -> None:
    model, diagram = feeder
    assert list(diagram.bus_glyphs) == list(model.buses)
    drawn = [
        el.id
        for group in (
            model.sources,
            model.lines,
            model.switches,
            model.transformers,
            model.loads,
            model.capacitors,
            model.pvsystems,
        )
        for el in group.values()
    ]
    assert sorted(diagram.element_glyphs) == sorted(drawn)


@needs_dss
@pytest.mark.parametrize("indent", [None, 1, 2])
def test_round_trip_is_exact(feeder: Any, indent: int | None, tmp_path: Path) -> None:
    model, diagram = feeder
    path = tmp_path / "d.qgrid"
    assert df.save(path, model, diagram, indent) == []
    m2, d2, diags = df.load(path)
    assert diags == []
    assert m2 == model and d2 == diagram
    assert df.float_mismatches(model, m2) == [] and df.float_mismatches(diagram, d2) == []
    first = path.read_bytes()
    df.save(path, m2, d2, indent)
    assert path.read_bytes() == first


@needs_dss
def test_base_frequency_is_written_once(feeder: Any) -> None:
    model, diagram = feeder
    doc = _doc(df.dumps(model, diagram))
    assert list(doc) == ["format", "version", "base_frequency", "grid_model", "diagram"]
    assert doc["base_frequency"] == model.base_frequency
    assert "base_frequency" not in doc["grid_model"]


# ------------------------------------------------------------------ floats


def _bits(n: int, rng: random.Random) -> list[float]:
    out = []
    while len(out) < n:
        value = struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0]
        if math.isfinite(value):
            out.append(value)
    return out


def test_every_finite_float_survives_bit_for_bit() -> None:
    rng = random.Random(5)
    specials = [
        0.0,
        -0.0,
        5e-324,
        -5e-324,
        2.2250738585072014e-308,
        1.7976931348623157e308,
        0.1,
        1 / 3,
        1e-4,
        4.16,
        1e16,
        123456789012345678.0,
    ]
    values = specials + _bits(20000 - len(specials), rng)
    n = int(math.isqrt(len(values)))
    values = values[: n * n]
    model = GridModel("f", 60.0)
    model.linecodes["x"] = LineCode("x", n, tuple(values), tuple(values), tuple(values), 1.0, 2.0)
    m2, _d, diags = df.loads(df.dumps(model, df.DiagramModel()))
    assert diags == []
    assert df.float_mismatches(model, m2) == []


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_float_is_not_saved(bad: float, tmp_path: Path) -> None:
    model, diagram = _small()
    model.voltage_bases = [bad]
    path = tmp_path / "d.qgrid"
    diags = df.save(path, model, diagram)
    assert [d.severity for d in diags] == [ERROR] and not path.exists()


def test_complex_is_an_re_im_pair() -> None:
    assert df.to_plain(complex(1.5, -0.25)) == [1.5, -0.25]


# ------------------------------------------------------------------ version rules


def test_newer_version_loads_nothing() -> None:
    model, diagram = _small()
    doc = _doc(df.dumps(model, diagram))
    doc["version"] = 2
    m, d, diags = df.loads(json.dumps(doc))
    assert m is None and d is None
    assert [(x.severity, x.code) for x in diags] == [(ERROR, "newer_version")]


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, True, None])
def test_bad_version_loads_nothing(version: Any) -> None:
    doc = _doc(df.dumps(*_small()))
    doc["version"] = version
    m, _d, diags = df.loads(json.dumps(doc))
    assert m is None and [x.severity for x in diags] == [ERROR]


@pytest.mark.parametrize(
    "text", ["", "{", "[]", '{"format": "something-else", "version": 1}', '{"version": 1}']
)
def test_not_a_design_file(text: str) -> None:
    m, _d, diags = df.loads(text)
    assert m is None and [x.severity for x in diags] == [ERROR]


def test_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "d.qgrid"
    path.write_bytes(b'{"format": "\xff"}')
    assert df.load(path)[2][0].code == "not_utf8"


def test_unknown_fields_warn_and_load() -> None:
    doc = _doc(df.dumps(*_small()))
    doc["results"] = {}
    doc["grid_model"]["scenarios"] = []
    doc["grid_model"]["linecodes"][0]["ampacity_table"] = [1, 2]
    doc["diagram"]["element_glyphs"]["linecode.lc"]["colour"] = "red"
    m, d, diags = df.loads(json.dumps(doc))
    assert (m, d) == _small()
    assert [x.severity for x in diags] == [WARNING] * 4
    assert {x.code for x in diags} == {"unknown_field"}
    assert "linecode.lc" in [x.block for x in diags]


def test_missing_field_with_default_takes_the_default() -> None:
    doc = _doc(df.dumps(*_small()))
    del doc["grid_model"]["linecodes"][0]["generated"]
    del doc["grid_model"]["pvsystems"]
    del doc["diagram"]["element_glyphs"]["linecode.lc"]["rotation"]
    m, d, diags = df.loads(json.dumps(doc))
    assert diags == [] and (m, d) == _small()


@pytest.mark.parametrize(
    "path",
    [
        ("base_frequency",),
        ("grid_model",),
        ("diagram",),
        ("grid_model", "name"),
        ("grid_model", "linecodes", 0, "normal_amps"),
        ("diagram", "element_glyphs", "linecode.lc", "x"),
    ],
)
def test_missing_field_without_default_loads_nothing(path: tuple[Any, ...]) -> None:
    doc = _doc(df.dumps(*_small()))
    parent = doc
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    m, _d, diags = df.loads(json.dumps(doc))
    assert m is None and ERROR in [x.severity for x in diags]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["grid_model"]["linecodes"][0].update(phases="1"),
        lambda d: d["grid_model"]["linecodes"][0].update(phases=1.0),
        lambda d: d["grid_model"]["linecodes"][0].update(normal_amps="400"),
        lambda d: d["grid_model"]["linecodes"][0].update(r_matrix=[0.1, 0.2]),
        lambda d: d["grid_model"]["linecodes"].append(dict(d["grid_model"]["linecodes"][0])),
        lambda d: d.update(base_frequency=0),
        lambda d: d.update(base_frequency=None),
    ],
    ids=["str_int", "float_int", "str_float", "matrix_size", "duplicate", "freq0", "freq_null"],
)
def test_bad_values_load_nothing(change: Any) -> None:
    doc = _doc(df.dumps(*_small()))
    change(doc)
    m, _d, diags = df.loads(json.dumps(doc))
    assert m is None and ERROR in [x.severity for x in diags]


def test_nan_in_file_loads_nothing() -> None:
    text = df.dumps(*_small()).replace('"normal_amps": 400.0', '"normal_amps": NaN')
    assert "NaN" in text
    m, _d, diags = df.loads(text)
    assert m is None and [x.severity for x in diags] == [ERROR]


def test_int_for_a_float_field_is_read_as_float() -> None:
    doc = _doc(df.dumps(*_small()))
    doc["base_frequency"] = 50
    doc["grid_model"]["linecodes"][0]["normal_amps"] = 400
    m, _d, diags = df.loads(json.dumps(doc))
    assert diags == [] and m is not None
    assert type(m.base_frequency) is float and type(m.linecodes["lc"].normal_amps) is float


def test_element_order_is_kept() -> None:
    model, diagram = _small()
    for name in ("z", "a", "m"):
        model.linecodes[name] = LineCode(name, 1, (0.1,), (0.2,), (3.0,), 400.0, 600.0)
    m, _d, _diags = df.loads(df.dumps(model, diagram))
    assert list(m.linecodes) == ["lc", "z", "a", "m"]
