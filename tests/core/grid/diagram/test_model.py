"""core/grid/diagram/model.py: check_consistency (rule 21) and the presentation-only
glyph classes (rule 20)."""

from __future__ import annotations

import ast
import math
from dataclasses import fields
from pathlib import Path

import pytest

from core.grid.diagram import layout as lay
from core.grid.diagram import model as dm
from core.grid.model import Bus, LineCode, Switch
from core.netlist.model import ERROR
from tests.core.grid.test_validate import LV_LN, T, good_model

FEEDERS = ["ieee13", "ieee34", "ieee123"]


def _codes(diags) -> list[tuple[str, str]]:
    assert all(d.severity == ERROR for d in diags)
    return [(d.block, d.code) for d in diags]


def test_good_model_diagram_is_consistent() -> None:
    m = good_model()
    d = lay.build_diagram(m)
    assert dm.check_consistency(m, d) == []
    assert list(d.bus_glyphs) == list(m.buses)
    assert set(d.element_glyphs) == set(dm.drawn_element_ids(m))


def test_linecodes_and_regcontrols_have_no_glyph() -> None:
    m = good_model()
    d = lay.build_diagram(m)
    assert not any(k.startswith(("linecode.", "regcontrol.")) for k in d.element_glyphs)
    d.element_glyphs["linecode.lc3"] = dm.ElementGlyph(0, 0)
    assert _codes(dm.check_consistency(m, d)) == [("linecode.lc3", dm.ORPHAN_GLYPH)]


def test_missing_and_orphan_glyphs() -> None:
    m = good_model()
    d = lay.build_diagram(m)
    del d.bus_glyphs["c"]
    del d.element_glyphs["load.ld3"]
    d.bus_glyphs["zz"] = dm.BusGlyph(1, 1)
    d.element_glyphs["load.nope"] = dm.ElementGlyph(1, 1)
    assert _codes(dm.check_consistency(m, d)) == [
        ("bus.c", dm.MISSING_GLYPH),
        ("bus.zz", dm.ORPHAN_GLYPH),
        ("load.ld3", dm.MISSING_GLYPH),
        ("load.nope", dm.ORPHAN_GLYPH),
    ]


def test_new_element_without_glyph_is_reported() -> None:
    m = good_model()
    d = lay.build_diagram(m)
    m.buses["e"] = Bus("e", LV_LN)
    m.linecodes["new"] = LineCode("new", 1, None, None, None)  # no glyph needed
    assert _codes(dm.check_consistency(m, d)) == [("bus.e", dm.MISSING_GLYPH)]


def test_line_and_switch_sharing_a_name_is_an_error() -> None:
    m = good_model()
    d = lay.build_diagram(m)
    m.switches["l1"] = Switch("l1", T("a"), T("c"), 3, True, 1e-4)
    assert _codes(dm.check_consistency(m, d)) == [("line.l1", dm.SHARED_ID)]


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (lambda d: setattr(d.bus_glyphs["a"], "orientation", 45), "bus.a"),
        (lambda d: setattr(d.bus_glyphs["a"], "length", 0.0), "bus.a"),
        (lambda d: setattr(d.bus_glyphs["a"], "x", math.nan), "bus.a"),
        (lambda d: setattr(d.element_glyphs["line.l1"], "rotation", 45), "line.l1"),
        (lambda d: setattr(d.element_glyphs["line.l1"], "label_dy", math.inf), "line.l1"),
    ],
)
def test_bad_glyph_values(mutate, where: str) -> None:
    m = good_model()
    d = lay.build_diagram(m)
    mutate(d)
    assert _codes(dm.check_consistency(m, d)) == [(where, dm.BAD_GLYPH)]


def test_diagram_stores_no_connectivity() -> None:
    """Rule 20: presentation only; nothing names a bus or terminal."""
    assert [f.name for f in fields(dm.BusGlyph)] == ["x", "y", "orientation", "length"]
    assert [f.name for f in fields(dm.ElementGlyph)] == [
        "x",
        "y",
        "rotation",
        "label_dx",
        "label_dy",
    ]
    assert [f.name for f in fields(dm.DiagramModel)] == ["bus_glyphs", "element_glyphs"]


def test_codes_are_unique() -> None:
    assert len(set(dm.CODES)) == len(dm.CODES)


def test_d3_modules_are_qt_and_dss_free() -> None:
    from core.grid import design_io

    for module in (dm, lay, design_io):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not names & {"PySide6", "shiboken6", "dss", "bridge"}, module.__name__


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_ieee_diagram_is_consistent(feeder: str) -> None:
    pytest.importorskip("dss")
    from tests.core.grid.test_topology import _feeder_model

    m = _feeder_model(feeder)
    d = lay.build_diagram(m)
    assert dm.check_consistency(m, d) == []
    assert len(d.bus_glyphs) == len(m.buses)
