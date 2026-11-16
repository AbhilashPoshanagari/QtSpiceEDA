"""core/grid/diagram/commands.py (D4): designer edit commands.

Every command: apply then revert gives an equal design (dict key order included),
check_consistency() and the topology are valid after apply and after revert, and a
second apply (redo) gives the first result again. Seeded random sequences of 1000
commands undo to the start and redo to the end. The proof test (CLAUDE.md
"Conventions") rebuilds IEEE 13 from an empty design with commands only and solves it.
"""

from __future__ import annotations

import copy
import dataclasses
import math
import pickle
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.grid import design_io
from core.grid import validate as v
from core.grid.design_io import GridDesign
from core.grid.diagram import commands as c
from core.grid.diagram.layout import build_diagram
from core.grid.diagram.model import DiagramModel, check_consistency
from core.grid.model import DELTA, GridModel, Terminal
from core.grid.topology import GridGraph, connected_elements
from core.netlist.model import ERROR
from tests.core.grid.test_validate import good_model

opendss = pytest.mark.opendss

T = Terminal.parse
REG_VALUES = {"vreg": 120.0, "band": 2.0, "pt_ratio": 20.0, "ct_primary": 700.0}
SRC_VALUES = {"kv": 11.0, "r1_ohm": 0.1, "x1_ohm": 1.0, "r0_ohm": 0.2, "x0_ohm": 2.0}
CLASS_DICTS = ("buses", "sources", "linecodes", "lines", "switches", "transformers",
               "regulators", "loads", "capacitors", "pvsystems")  # fmt: skip


# ------------------------------------------------------------------ helpers


def _design(model: GridModel | None = None) -> GridDesign:
    m = good_model() if model is None else model
    return GridDesign(m, build_diagram(m))


def _order(design: GridDesign) -> tuple[Any, ...]:
    """Key order of every dict (== on dataclasses ignores it; the DSS script and the
    .qgrid follow it)."""
    g = design.grid
    return (
        *(tuple(getattr(g, name)) for name in CLASS_DICTS),
        tuple(design.diagram.bus_glyphs),
        tuple(design.diagram.element_glyphs),
    )


def _topology(design: GridDesign) -> tuple[Any, ...]:
    g = GridGraph.build(design.grid)
    return (
        g.islands(),
        g.voltage_zones(),
        {b: g.available_nodes(b) for b in g.buses},
        [lp.element_ids for lp in g.loops()],
        sorted(g.energized_buses()),
    )


def _assert_valid(design: GridDesign, full: bool = True) -> None:
    assert check_consistency(design.grid, design.diagram) == []
    if not full:
        GridGraph.build(design.grid).islands()  # the graph builds and walks
        return
    _topology(design)  # the graph builds and answers every question
    codes = {d.code for d in v.validate(design.grid)}
    assert v.VALIDATION_FAILED not in codes
    assert v.REGULATOR_TARGET not in codes  # no dangling regulator


def _snap(design: GridDesign) -> tuple[GridDesign, tuple[Any, ...]]:
    return copy.deepcopy(design), _order(design)


def _assert_same(design: GridDesign, snap: tuple[GridDesign, tuple[Any, ...]]) -> None:
    assert design == snap[0]
    assert _order(design) == snap[1]


def _round_trip(design: GridDesign, cmd: c.Command) -> None:
    """apply, revert, apply again: exact, valid after each step."""
    before, topo = _snap(design), _topology(design)
    assert cmd.apply(design) == [], cmd.description
    _assert_valid(design)
    after = _snap(design)
    assert design != before[0] or _order(design) != before[1] or cmd.obsolete or not cmd._ops
    assert cmd.revert(design) == []
    _assert_same(design, before)
    assert _topology(design) == topo
    _assert_valid(design)
    assert cmd.apply(design) == []
    _assert_same(design, after)
    _assert_valid(design)


def _codes(diags) -> list[str]:
    assert all(d.severity == ERROR for d in diags)
    return [d.code for d in diags]


# ------------------------------------------------------------------ every command


CASES: list[tuple[str, Callable[[], c.Command]]] = [
    ("add bus", lambda: c.AddBus()),
    ("add bus placed", lambda: c.AddBus("e", (3.0, 4.0), 90, 4.0)),
    ("add load", lambda: c.AddElement("load", ["b"])),
    ("add load at position", lambda: c.AddElement("load", ["c"], (7.5, 9.0))),
    ("add 1-ph load", lambda: c.AddElement("load", ["d"])),
    ("add delta load", lambda: c.AddElement("load", ["c.1.2"], values={"conn": DELTA})),
    ("add capacitor", lambda: c.AddElement("capacitor", ["a"], values={"kvar": 300})),
    ("add pv", lambda: c.AddElement("pvsystem", ["b"], values={"kva": 50, "pmpp_kw": 45})),
    ("add line", lambda: c.AddElement("line", ["b", "c"], values={"linecode": "lc3"})),
    ("add switch", lambda: c.AddElement("switch", ["b", "c"], values={"r_ohm": 1e-4})),
    ("add transformer", lambda: c.AddElement("transformer", ["a", "b"])),
    ("add 3-winding", lambda: c.AddElement("transformer", ["a", "b", "c"])),
    ("add linecode", lambda: c.AddElement("linecode", phases=2)),
    ("add source", lambda: c.AddElement("vsource", ["a"], values=SRC_VALUES)),
    ("copy load", lambda: c.AddElement("load", ["c"], copy_from="load.ld3")),
    (
        "copy transformer",
        lambda: c.AddElement("transformer", ["a", "c"], copy_from="transformer.t1"),
    ),
    ("add regulator", lambda: c.AddRegulator("c", "d", regulator_values=REG_VALUES)),
    ("remove load", lambda: c.RemoveElement("load.ld3")),
    ("remove line", lambda: c.RemoveElement("line.l1")),
    ("remove switch", lambda: c.RemoveElement("line.sw1")),
    ("remove transformer", lambda: c.RemoveElement("transformer.t1")),
    ("remove regulator", lambda: c.RemoveElement("regcontrol.r1")),
    ("remove source", lambda: c.RemoveElement("vsource.s")),
    ("remove bus b", lambda: c.RemoveBus("b")),
    ("remove bus src", lambda: c.RemoveBus("bus.src")),
    ("connect load", lambda: c.Connect("load.ld3", 1, "c")),
    ("connect source", lambda: c.Connect("vsource.s", 1, "a")),
    ("connect winding", lambda: c.Connect("transformer.t1", 2, "c", (1, 2, 3, 0))),
    ("disconnect line", lambda: c.Disconnect("line.l1", 2)),
    ("disconnect load", lambda: c.Disconnect("load.ld1", 1)),
    ("set kw", lambda: c.SetProperty("load.ld3", "kw", 120)),
    ("clear kw", lambda: c.SetProperty("load.ld3", "kw", None)),
    ("set winding kva", lambda: c.SetProperty("transformer.t1", "windings.1.kva", 630.0)),
    ("set matrix", lambda: c.SetProperty("linecode.lc3", "r_matrix", [0.1] * 9)),
    ("set bus x", lambda: c.SetProperty("bus.a", "x", 12.0)),
    ("set vreg", lambda: c.SetProperty("regcontrol.r1", "vreg", 121.0)),
    ("set linecode", lambda: c.SetProperty("line.l1", "linecode", "lc1")),
    ("set length", lambda: c.SetProperty("line.l2", "length_m", 75)),
    ("set states", lambda: c.SetProperty("capacitor.cap1", "states", [1, 0])),
    ("rename bus", lambda: c.Rename("bus.b", "b_new")),
    ("rename src bus", lambda: c.Rename("bus.src", "sourcebus")),
    ("rename line", lambda: c.Rename("line.l1", "l9")),
    ("rename switch", lambda: c.Rename("line.sw1", "sw9")),
    ("rename linecode", lambda: c.Rename("linecode.lc3", "mtx601")),
    ("rename transformer", lambda: c.Rename("transformer.t1", "sub")),
    ("rename load", lambda: c.Rename("load.ld3", "LD30")),
    ("rename source", lambda: c.Rename("vsource.s", "source")),
    ("rename regulator", lambda: c.Rename("regcontrol.r1", "reg1")),
    ("open switch", lambda: c.SetSwitchOpen("line.sw1", True)),
    ("move bus", lambda: c.MoveGlyph("bus.a", 1.5, -2.0)),
    ("move load", lambda: c.MoveGlyph("load.ld3", 4.0, 5.0)),
    ("move selection", lambda: c.MoveGlyphs(["bus.a", "load.ld3", "line.l1"], 2.0, -1.0)),
    ("rotate bus", lambda: c.RotateGlyph("bus.c")),
    ("rotate line", lambda: c.RotateGlyph("line.l1", 3)),
    (
        "compound",
        lambda: c.Compound(
            "Remove l2 and its linecode",
            [c.RemoveElement("line.l2"), c.RemoveElement("linecode.lc1")],
        ),
    ),
]


@pytest.mark.parametrize("make", [m for _, m in CASES], ids=[n for n, _ in CASES])
def test_apply_revert_is_exact(make: Callable[[], c.Command]) -> None:
    design = _design()
    _assert_valid(design)
    _round_trip(design, make())


def test_descriptions() -> None:
    design = _design()
    cmds = [
        c.AddElement("load", ["b"]),
        c.AddBus(),
        c.SetProperty("load.ld3", "kw", 1.0),
        c.SetProperty("transformer.t1", "windings.1.kva", 1.0),
        c.Rename("line.l1", "l9"),
        c.SetSwitchOpen("line.sw1", True),
        c.RemoveBus("d"),
        c.AddRegulator("b", "c", regulator_values=REG_VALUES),
    ]
    for cmd in cmds:
        assert cmd.apply(design) == []
    assert [cmd.description for cmd in cmds] == [
        "Add load ld2",
        "Add bus b1",
        "Set load ld3 kW",
        "Set transformer t1 winding 2 kVA",
        "Rename line l1 to l9",
        "Open switch sw1",
        "Remove bus d",
        "Add regulator reg1",
    ]


# ------------------------------------------------------------------ structural defaults


def test_new_names_take_the_lowest_free_number() -> None:
    design = _design()
    a = c.AddElement("load", ["b"])
    b = c.AddElement("load", ["b"])
    assert a.apply(design) == [] and b.apply(design) == []
    assert (a.name, b.name) == ("ld2", "ld4")  # ld1 and ld3 exist
    sw = c.AddElement("switch", ["b", "c"], values={"r_ohm": 1e-4})
    assert sw.apply(design) == [] and sw.name == "sw2"
    # lines and switches share "line.<name>"
    clash = c.AddElement("line", ["b", "c"], name="sw2")
    assert _codes(clash.apply(design)) == [c.DUPLICATE_NAME]


def test_new_load_is_a_draft_with_phases_and_kv_from_the_bus() -> None:
    design = _design()
    zones = GridGraph.build(design.grid).voltage_zones()
    three = c.AddElement("load", ["b"])
    one = c.AddElement("load", ["d"])  # bus d has node 1 only
    assert three.apply(design) == [] and one.apply(design) == []
    ld3, ld1 = design.grid.loads[three.name], design.grid.loads[one.name]
    assert (ld3.phases, ld3.terminal, ld3.kv, ld3.kw, ld3.kvar) == (
        3,
        T("b"),
        zones["b"],
        None,
        None,
    )
    assert (ld1.phases, ld1.terminal) == (1, T("d"))  # OpenDSS: node 1
    assert ld1.kv == pytest.approx(zones["d"] / math.sqrt(3.0))  # 1-phase wye: LN
    missing = [(d.block, d.message) for d in v.validate(design.grid) if d.code == v.MISSING_VALUE]
    assert (ld3.id, "kW not set") in missing and (ld1.id, "kvar not set") in missing


def test_new_transformer_takes_zone_kv_and_leaves_ratings_unset() -> None:
    design = _design()
    cmd = c.AddElement("transformer", ["a", "b"])
    assert cmd.apply(design) == []
    t = design.grid.transformers[cmd.name]
    assert [w.kv for w in t.windings] == [11.0, 0.41]
    assert [w.kva for w in t.windings] == [None, None] and t.xhl is None
    labels = {d.message for d in v.validate(design.grid) if d.block == t.id}
    assert {"winding 1 kVA not set", "winding 2 %R not set", "Xhl (%) not set"} <= labels


def test_new_line_has_no_invented_impedance_or_length() -> None:
    design = _design()
    cmd = c.AddElement("line", ["c", "d"])
    assert cmd.apply(design) == []
    ln = design.grid.lines[cmd.name]
    assert (ln.linecode, ln.length_m, ln.phases) == ("", 0.0, 3)
    codes = {d.code for d in v.validate(design.grid) if d.block == ln.id}
    assert {v.UNKNOWN_LINECODE, v.NONPOSITIVE_LENGTH} <= codes


def test_line_on_a_partial_bus_takes_its_nodes_at_both_ends() -> None:
    design = _design()
    assert c.AddBus("e").apply(design) == []
    cmd = c.AddElement("line", ["d", "e"])
    assert cmd.apply(design) == []
    ln = design.grid.lines[cmd.name]
    assert (ln.phases, ln.bus1, ln.bus2) == (1, T("d"), T("e"))
    # a bus with only phase 3 gives the line node 3 at both ends
    design.grid.lines["l2"].bus2 = T("d.3")
    design.grid.lines["l2"].bus1 = T("c.3")
    assert c.AddBus("f").apply(design) == []
    cmd = c.AddElement("line", ["d", "f"])
    assert cmd.apply(design) == []
    ln = design.grid.lines[cmd.name]
    assert (ln.phases, ln.bus1, ln.bus2) == (1, T("d.3"), T("f.3"))


def test_regulator_is_one_single_phase_transformer_and_regcontrol() -> None:
    design = _design()
    cmd = c.AddRegulator("c", "d", regulator_values=REG_VALUES)
    assert cmd.apply(design) == []
    tx, reg = cmd.children  # type: ignore[misc]
    t = design.grid.transformers[tx.name]
    r = design.grid.regulators[reg.name]
    assert t.phases == 1 and [w.terminal for w in t.windings] == [T("c.1"), T("d.1")]
    assert (r.transformer, r.winding, r.vreg, r.r, r.x) == (t.name, 2, 120.0, 0.0, 0.0)
    assert cmd.affected() == {t.id, r.id}
    assert cmd.revert(design) == []
    assert tx.name not in design.grid.transformers and reg.name not in design.grid.regulators


def test_values_that_must_be_given() -> None:
    design = _design()
    before = _snap(design)
    assert _codes(c.AddElement("vsource", ["a"]).apply(design)) == [c.VALUE_REQUIRED]
    assert _codes(c.AddElement("switch", ["b", "c"]).apply(design)) == [c.VALUE_REQUIRED]
    assert _codes(c.AddRegulator("c", "d").apply(design)) == [c.VALUE_REQUIRED]
    assert c.AddBus("e").apply(design) == []
    after_bus = _snap(design)
    assert _codes(c.AddElement("load", ["e"]).apply(design)) == [c.NO_VOLTAGE_ZONE]
    _assert_same(design, after_bus)
    ok = c.AddElement("load", ["e"], values={"kv": 0.4})
    assert ok.apply(design) == []
    assert design.grid.loads[ok.name].kv == 0.4
    assert before[0] != design


# ------------------------------------------------------------------ preconditions


FAILURES: list[tuple[str, Callable[[], c.Command], str]] = [
    ("duplicate bus", lambda: c.AddBus("a"), c.DUPLICATE_NAME),
    ("bad bus name", lambda: c.AddBus("a b"), c.BAD_NAME),
    ("bad orientation", lambda: c.AddBus("e", orientation=45), c.BAD_VALUE),
    ("nan position", lambda: c.AddBus("e", (math.nan, 0.0)), c.BAD_VALUE),
    ("duplicate load", lambda: c.AddElement("load", ["b"], name="ld3"), c.DUPLICATE_NAME),
    ("missing bus", lambda: c.AddElement("load", ["zz"]), c.UNKNOWN_BUS),
    ("unknown kind", lambda: c.AddElement("fuse", ["a"]), c.UNKNOWN_KIND),
    ("regcontrol kind", lambda: c.AddElement("regcontrol"), c.UNKNOWN_KIND),
    ("terminal count", lambda: c.AddElement("line", ["a"]), c.BAD_TERMINAL),
    ("bad node", lambda: c.AddElement("load", [Terminal("a", (-1,))]), c.BAD_TERMINAL),
    ("copy wrong class", lambda: c.AddElement("load", ["b"], copy_from="line.l1"), c.WRONG_CLASS),
    ("bad value at add", lambda: c.AddElement("load", ["b"], values={"kv": -1}), c.BAD_VALUE),
    ("remove missing", lambda: c.RemoveElement("load.nope"), c.UNKNOWN_ELEMENT),
    ("remove bus as element", lambda: c.RemoveElement("bus.a"), c.WRONG_CLASS),
    ("remove used linecode", lambda: c.RemoveElement("linecode.lc3"), c.IN_USE),
    ("remove missing bus", lambda: c.RemoveBus("zz"), c.UNKNOWN_BUS),
    ("connect missing bus", lambda: c.Connect("load.ld3", 1, "zz"), c.UNKNOWN_BUS),
    ("connect bad terminal", lambda: c.Connect("load.ld3", 2, "a"), c.BAD_TERMINAL),
    ("connect linecode", lambda: c.Connect("linecode.lc3", 1, "a"), c.BAD_TERMINAL),
    ("connect bus", lambda: c.Connect("bus.a", 1, "b"), c.WRONG_CLASS),
    ("disconnect missing", lambda: c.Disconnect("line.nope", 1), c.UNKNOWN_ELEMENT),
    ("negative kva", lambda: c.SetProperty("transformer.t1", "windings.0.kva", -5), c.BAD_VALUE),
    ("negative cap kvar", lambda: c.SetProperty("capacitor.cap1", "kvar", -300), c.BAD_VALUE),
    ("text for number", lambda: c.SetProperty("load.ld3", "kw", "100"), c.BAD_VALUE),
    ("bool for number", lambda: c.SetProperty("load.ld3", "kw", True), c.BAD_VALUE),
    ("none for required", lambda: c.SetProperty("load.ld3", "kv", None), c.BAD_VALUE),
    ("inf", lambda: c.SetProperty("load.ld3", "kvar", math.inf), c.BAD_VALUE),
    ("unknown field", lambda: c.SetProperty("load.ld3", "kva", 1.0), c.UNKNOWN_FIELD),
    (
        "winding index",
        lambda: c.SetProperty("transformer.t1", "windings.5.kva", 1.0),
        c.UNKNOWN_FIELD,
    ),
    ("name field", lambda: c.SetProperty("load.ld3", "name", "x"), c.READ_ONLY_FIELD),
    ("terminal field", lambda: c.SetProperty("load.ld3", "terminal", "a"), c.READ_ONLY_FIELD),
    ("closed field", lambda: c.SetProperty("line.sw1", "closed", False), c.READ_ONLY_FIELD),
    ("unknown linecode", lambda: c.SetProperty("line.l1", "linecode", "nope"), c.BAD_VALUE),
    ("matrix size", lambda: c.SetProperty("linecode.lc3", "x_matrix", [0.1] * 4), c.BAD_VALUE),
    ("linecode phases", lambda: c.SetProperty("linecode.lc3", "phases", 2), c.BAD_VALUE),
    ("conn", lambda: c.SetProperty("load.ld3", "conn", "zigzag"), c.BAD_VALUE),
    ("load model", lambda: c.SetProperty("load.ld3", "model", "fancy"), c.BAD_VALUE),
    ("pf", lambda: c.SetProperty("load.ld3", "vminpu", 0), c.BAD_VALUE),
    ("reg transformer", lambda: c.SetProperty("regcontrol.r1", "transformer", "nope"), c.BAD_VALUE),
    ("reg winding", lambda: c.SetProperty("regcontrol.r1", "winding", 3), c.BAD_VALUE),
    ("monitored bus", lambda: c.SetProperty("regcontrol.r1", "monitored_bus", "zz.1"), c.BAD_VALUE),
    ("states", lambda: c.SetProperty("capacitor.cap1", "states", [2]), c.BAD_VALUE),
    ("phases int", lambda: c.SetProperty("load.ld3", "phases", 1.5), c.BAD_VALUE),
    ("rename duplicate", lambda: c.Rename("bus.a", "b"), c.DUPLICATE_NAME),
    ("rename line to switch", lambda: c.Rename("line.l1", "sw1"), c.DUPLICATE_NAME),
    ("rename bad", lambda: c.Rename("load.ld3", "ld-3"), c.BAD_NAME),
    ("rename missing", lambda: c.Rename("load.nope", "x"), c.UNKNOWN_ELEMENT),
    ("open a line", lambda: c.SetSwitchOpen("line.l1", True), c.WRONG_CLASS),
    ("move missing", lambda: c.MoveGlyph("linecode.lc3", 0, 0), c.UNKNOWN_ELEMENT),
    ("move nan", lambda: c.MoveGlyph("bus.a", math.nan, 0), c.BAD_VALUE),
    ("move none", lambda: c.MoveGlyphs([], 1, 0), c.UNKNOWN_ELEMENT),
    ("move some missing", lambda: c.MoveGlyphs(["bus.a", "bus.zz"], 1, 0), c.UNKNOWN_ELEMENT),
    ("move by nan", lambda: c.MoveGlyphs(["bus.a"], math.inf, 0), c.BAD_VALUE),
    ("rotate float", lambda: c.RotateGlyph("bus.a", 0.5), c.BAD_VALUE),  # type: ignore[arg-type]
    ("not an id", lambda: c.SetProperty(None, "kw", 1.0), c.UNKNOWN_ELEMENT),  # type: ignore[arg-type]
    (
        "compound child",
        lambda: c.Compound("two", [c.RemoveElement("load.ld3"), c.RemoveElement("load.ld3")]),
        c.UNKNOWN_ELEMENT,
    ),
]


@pytest.mark.parametrize(
    ("make", "code"), [(m, k) for _, m, k in FAILURES], ids=[n for n, _, _ in FAILURES]
)
def test_failed_precondition_changes_nothing(make: Callable[[], c.Command], code: str) -> None:
    design = _design()
    before = _snap(design)
    cmd = make()
    out = cmd.apply(design)
    assert _codes(out) == [code], [d.message for d in out]
    _assert_same(design, before)
    assert cmd.revert(design) == []  # never applied: nothing to undo
    _assert_same(design, before)


def test_diagnostic_names_the_element() -> None:
    design = _design()
    (d,) = c.SetProperty("transformer.t1", "windings.0.kva", -5).apply(design)
    assert d.block == "transformer.t1" and "winding 1 kVA must be greater than 0" in d.message


def test_commands_never_raise_on_a_broken_design() -> None:
    design = _design()
    design.grid.loads["ld3"] = None  # type: ignore[assignment]
    for cmd in (c.SetProperty("load.ld3", "kw", 1.0), c.RemoveBus("b"), c.Rename("bus.b", "x"),
                c.AddElement("load", ["b"]), c.MoveGlyph("load.ld3", 1, 1)):  # fmt: skip
        out = cmd.apply(design)
        assert isinstance(out, list)


# ------------------------------------------------------------------ references


def test_rename_bus_updates_every_reference() -> None:
    design = _design()
    g = design.grid
    g.regulators["r1"].monitored_bus = "b.1"
    assert c.Rename("bus.b", "lv").apply(design) == []
    assert list(g.buses)[2] == "lv" and g.buses["lv"].name == "lv"
    assert list(design.diagram.bus_glyphs)[2] == "lv"
    assert g.transformers["t1"].windings[1].terminal == T("lv.1.2.3.0")
    assert g.switches["sw1"].bus1 == T("lv") and g.loads["ld3"].terminal == T("lv")
    assert g.regulators["r1"].monitored_bus == "lv.1"
    assert connected_elements(g, "b") == []
    assert c.Rename("bus.src", "sb").apply(design) == []
    assert g.sources["s"].bus1 == T("sb") and g.sources["s"].bus2 == T("sb.0.0.0")
    assert v.validate(g) == []


def test_rename_element_updates_references_and_glyph_key() -> None:
    design = _design()
    g = design.grid
    assert c.Rename("linecode.lc3", "mtx601").apply(design) == []
    assert g.lines["l1"].linecode == "mtx601" and list(g.linecodes) == ["mtx601", "lc1"]
    assert c.Rename("transformer.t1", "sub").apply(design) == []
    assert g.regulators["r1"].transformer == "sub"
    assert "transformer.sub" in design.diagram.element_glyphs
    assert c.Rename("line.sw1", "s_1").apply(design) == []
    assert list(g.switches) == ["s_1"] and "line.s_1" in design.diagram.element_glyphs
    assert v.validate(g) == []


def test_remove_bus_takes_everything_connected_as_one_step() -> None:
    design = _design()
    before = _snap(design)
    cmd = c.RemoveBus("b")
    assert cmd.apply(design) == []
    g = design.grid
    assert "b" not in g.buses and "t1" not in g.transformers and "r1" not in g.regulators
    assert "sw1" not in g.switches and "ld3" not in g.loads
    assert {"transformer.t1", "regcontrol.r1", "line.sw1", "load.ld3", "bus.b"} <= cmd.affected()
    _assert_valid(design)
    assert cmd.revert(design) == []
    _assert_same(design, before)


def test_remove_bus_clears_a_regulators_monitored_bus() -> None:
    design = _design()
    design.grid.regulators["r1"].monitored_bus = "d.1"
    assert c.RemoveBus("d").apply(design) == []
    assert design.grid.regulators["r1"].monitored_bus == ""


def test_remove_transformer_takes_its_regulator() -> None:
    design = _design()
    cmd = c.RemoveElement("transformer.t1")
    assert cmd.apply(design) == []
    assert design.grid.regulators == {} and cmd.affected() == {"transformer.t1", "regcontrol.r1"}


def test_connect_and_disconnect() -> None:
    design = _design()
    g = design.grid
    assert c.Disconnect("line.l2", 2).apply(design) == []
    assert g.lines["l2"].bus2 == Terminal("", ())
    graph = GridGraph.build(g)
    assert "" not in graph.buses and all(e.element_id != "line.l2" for e in graph.edges)
    assert [d.message for d in v.validate(g) if d.block == "line.l2"] == [
        "bus2 is not connected to a bus"
    ]
    assert c.Connect("line.l2", 2, "d", (1,)).apply(design) == []
    assert v.validate(g) == []


def test_switch_open_and_close() -> None:
    design = _design()
    assert c.SetSwitchOpen("line.sw1", True).apply(design) == []
    assert design.grid.switches["sw1"].closed is False
    assert "c" not in GridGraph.build(design.grid).energized_buses()
    assert c.SetSwitchOpen("line.sw1", False).apply(design) == []
    assert design.grid.switches["sw1"].closed is True


def test_set_property_coerces_ints_to_floats() -> None:
    design = _design()
    assert c.SetProperty("load.ld3", "kw", 7).apply(design) == []
    assert type(design.grid.loads["ld3"].kw) is float


# ------------------------------------------------------------------ presentation and merge


def test_glyph_commands_are_presentation_only() -> None:
    design = _design()
    grid = copy.deepcopy(design.grid)
    for cmd in (c.MoveGlyph("bus.a", 9, 9), c.MoveGlyph("line.l1", 1, 2), c.RotateGlyph("bus.a"),
                c.RotateGlyph("load.ld3", 2)):  # fmt: skip
        assert cmd.apply(design) == []
    assert design.grid == grid
    assert design.diagram.bus_glyphs["a"].orientation == 90
    assert design.diagram.element_glyphs["load.ld3"].rotation == 180


def test_move_merges_within_one_gesture_only() -> None:
    design = _design()
    g = design.diagram.bus_glyphs["a"]
    start = (g.x, g.y)
    first = c.MoveGlyph("bus.a", 1, 1, gesture=7)
    assert first.apply(design) == []
    for x in (2, 3):
        nxt = c.MoveGlyph("bus.a", x, x, gesture=7)
        assert nxt.apply(design) == []
        assert first.merge(nxt)
    other = c.MoveGlyph("bus.a", 5, 5, gesture=8)
    assert other.apply(design) == [] and not first.merge(other)
    assert not first.merge(c.MoveGlyph("bus.b", 3, 3, gesture=7))
    assert other.revert(design) == [] and (g.x, g.y) == (3.0, 3.0)
    assert first.revert(design) == [] and (g.x, g.y) == start
    assert first.apply(design) == [] and (g.x, g.y) == (3.0, 3.0)
    # a drag that ends where it started is obsolete (QUndoStack drops it)
    back = c.MoveGlyph("bus.a", *start, gesture=7)
    assert back.apply(design) == [] and first.merge(back)
    assert first.obsolete
    assert first.merge_id == c.MERGE_MOVE


def test_move_selection_merges_within_one_gesture_only() -> None:
    design = _design()
    ids = ["bus.a", "load.ld3"]
    ga, gl = design.diagram.bus_glyphs["a"], design.diagram.element_glyphs["load.ld3"]
    start = [(ga.x, ga.y), (gl.x, gl.y)]
    first = c.MoveGlyphs(ids, 1, 0, gesture=3)
    assert first.apply(design) == [] and first.description == "Move 2 items"
    for step in ((1, 0), (0, 2)):
        nxt = c.MoveGlyphs(ids, *step, gesture=3)
        assert nxt.apply(design) == [] and first.merge(nxt)
    assert (ga.x, ga.y) == (start[0][0] + 2, start[0][1] + 2)
    assert (gl.x, gl.y) == (start[1][0] + 2, start[1][1] + 2)
    assert (first.dx, first.dy) == (2, 2)
    assert not first.merge(c.MoveGlyphs(ids, 1, 1, gesture=4))
    assert not first.merge(c.MoveGlyphs(["bus.a"], 1, 1, gesture=3))
    assert not first.merge(c.MoveGlyph("bus.a", 1, 1, gesture=3))
    assert first.revert(design) == []
    assert [(ga.x, ga.y), (gl.x, gl.y)] == start
    _assert_valid(design)
    assert first.apply(design) == [] and (ga.x, ga.y) == (start[0][0] + 2, start[0][1] + 2)
    back = c.MoveGlyphs(ids, -2, -2, gesture=3)
    assert back.apply(design) == [] and first.merge(back) and first.obsolete


def test_set_property_merges_only_with_a_merge_key() -> None:
    design = _design()
    plain = c.SetProperty("load.ld3", "kw", 1.0)
    assert plain.apply(design) == [] and plain.merge_id == c.NO_MERGE
    nxt = c.SetProperty("load.ld3", "kw", 2.0)
    assert nxt.apply(design) == [] and not plain.merge(nxt)

    design = _design()
    a = c.SetProperty("load.ld3", "kw", 1.0, merge_key="spin")
    b = c.SetProperty("load.ld3", "kw", 2.0, merge_key="spin")
    assert a.merge_id == c.MERGE_SET_PROPERTY
    assert a.apply(design) == [] and b.apply(design) == [] and a.merge(b)
    assert not a.merge(c.SetProperty("load.ld3", "kvar", 2.0, merge_key="spin"))
    assert a.revert(design) == [] and design.grid.loads["ld3"].kw == 100.0
    assert a.apply(design) == [] and design.grid.loads["ld3"].kw == 2.0


def test_other_commands_never_merge() -> None:
    design = _design()
    a, b = c.AddBus(), c.AddBus()
    assert a.apply(design) == [] and b.apply(design) == []
    assert a.merge_id == c.NO_MERGE and not a.merge(b)


def test_commands_module_is_qt_and_dss_free() -> None:
    text = Path(c.__file__).read_text(encoding="utf-8")
    assert "PySide6" not in text and "import dss" not in text and "from dss" not in text


# ------------------------------------------------------------------ random sequences


def _ids(design: GridDesign) -> list[str]:
    g = design.grid
    out = [f"bus.{b}" for b in g.buses]
    for name in CLASS_DICTS[1:]:
        out += [el.id for el in getattr(g, name).values()]
    return out


def _random_value(rng: random.Random, current: object, design: GridDesign) -> object:
    if isinstance(current, bool):
        return rng.random() < 0.5
    if isinstance(current, int):
        return rng.choice([1, 2, 3, 0, -1])
    if isinstance(current, float) or current is None:
        return rng.choice([round(rng.uniform(0.0, 200.0), 3), -1.0, None, 0.0, 1.0, "x"])
    if isinstance(current, str):
        return rng.choice(["", "1", "max", *design.grid.linecodes, *design.grid.transformers,
                           *design.grid.buses, "nope"])  # fmt: skip
    if isinstance(current, tuple):
        return [rng.choice([0, 1, 0.5]) for _ in range(rng.choice([1, 4, 9]))]
    return current


def _random_set(rng: random.Random, design: GridDesign) -> c.Command:
    el_id = rng.choice(_ids(design))
    ref = c.resolve(design.grid, el_id)
    assert ref is not None
    obj = ref.obj
    names = [f.name for f in dataclasses.fields(obj)] + ["nope"]
    path = rng.choice(names)
    owner = obj
    if path == "windings" and obj.windings:
        i = rng.randrange(len(obj.windings))
        owner = obj.windings[i]
        path = f"windings.{i}.{rng.choice([f.name for f in dataclasses.fields(owner)])}"
    field = path.split(".")[-1]
    value = _random_value(rng, getattr(owner, field, None), design)
    return c.SetProperty(el_id, path, value)


def _random_command(rng: random.Random, design: GridDesign, depth: int = 0) -> c.Command:
    g = design.grid
    buses = [*g.buses] or ["nope"]

    def bus() -> str:
        return rng.choice(buses) if rng.random() < 0.95 else "nope"

    ids = _ids(design) + ["load.nope"]
    glyphs = [f"bus.{b}" for b in design.diagram.bus_glyphs] + [*design.diagram.element_glyphs]
    r = rng.random()
    if r < 0.08:
        name = rng.choice([None, None, None, "a", "bad name", f"n{rng.randrange(40)}"])
        pos = None if rng.random() < 0.5 else (rng.randrange(-20, 20), rng.randrange(-20, 20))
        return c.AddBus(name, pos, rng.choice([0, 90]))
    if r < 0.26:
        kind = rng.choice(["load", "load", "capacitor", "pvsystem", "line", "line", "switch",
                           "transformer", "linecode", "vsource"])  # fmt: skip
        n = c.KINDS[kind].terminals[0] + (1 if rng.random() < 0.05 else 0)
        terms = [bus() for _ in range(n)]
        values: dict[str, Any] = {}
        if kind == "vsource":
            values = dict(SRC_VALUES)
        elif kind == "switch":
            values = {"r_ohm": 1e-4}
        elif kind in ("load", "capacitor") and rng.random() < 0.3:
            values = {"kv": rng.choice([0.4, -1.0])}
        elif kind == "line" and g.linecodes and rng.random() < 0.5:
            values = {"linecode": rng.choice([*g.linecodes])}
        name = None if rng.random() < 0.8 else rng.choice(["ld3", "l1", "x_1"])
        phases = None if kind != "linecode" else rng.choice([1, 3])
        return c.AddElement(kind, terms, name=name, phases=phases, values=values)
    if r < 0.29:
        vals = REG_VALUES if rng.random() < 0.9 else {}
        return c.AddRegulator(bus(), bus(), regulator_values=vals)
    if r < 0.35:
        return c.RemoveElement(rng.choice(ids))
    if r < 0.38:
        return c.RemoveBus(bus())
    if r < 0.43:
        nodes = rng.choice([(), (1,), (1, 2, 3), (1, 2, 3, 0)])
        return c.Connect(rng.choice(ids), rng.choice([1, 1, 2, 3]), bus(), nodes)
    if r < 0.46:
        return c.Disconnect(rng.choice(ids), rng.choice([1, 2]))
    if r < 0.62:
        return _random_set(rng, design)
    if r < 0.68:
        new = rng.choice([f"x{rng.randrange(30)}", "UPPER", "bad-name", *buses[:3]])
        return c.Rename(rng.choice(ids), new)
    if r < 0.72:
        sw = [s.id for s in g.switches.values()] or ids
        return c.SetSwitchOpen(rng.choice(sw), rng.random() < 0.5)
    if r < 0.86 and glyphs:
        return c.MoveGlyph(rng.choice(glyphs), rng.randrange(-30, 30), rng.randrange(-30, 30))
    if r < 0.91 and glyphs:
        return c.RotateGlyph(rng.choice(glyphs), rng.choice([1, 2, 3]))
    if depth == 0:
        # the children are built against the current design; later ones may fail after
        # earlier ones applied, which must roll the compound back
        kids = [_random_command(rng, design, 1) for _ in range(rng.choice([2, 3]))]
        return c.Compound("random", kids)
    return c.AddBus()


def _run_random(design: GridDesign, seed: int, steps: int = 1000) -> None:
    """Apply ``steps`` random commands (failed ones must change nothing), then undo all
    (== the start) and redo all (== the end). The snapshot before each command is a
    pickle (much faster than deepcopy on a grown design). Consistency and the graph are
    checked after every step, the full validate() every FULL_EVERY steps."""
    rng = random.Random(seed)
    start = _snap(design)
    done: list[c.Command] = []
    failed = 0
    while len(done) < steps:
        cmd = _random_command(rng, design)
        before = pickle.dumps(design), _order(design)
        out = cmd.apply(design)
        if out:
            failed += 1
            assert all(d.severity == ERROR for d in out)
            assert not any(d.code == c.COMMAND_FAILED for d in out), [d.message for d in out]
            _assert_same(design, (pickle.loads(before[0]), before[1]))
            continue
        _assert_valid(design, full=len(done) % FULL_EVERY == 0)
        done.append(cmd)
    assert failed > 50  # the failure path is exercised too
    _assert_valid(design)
    end = _snap(design)
    # the result is a design that saves and loads unchanged
    loaded, diags = design_io.loads(design_io.dumps(design))
    assert loaded == design and not [d for d in diags if d.severity == ERROR]

    for i, cmd in enumerate(reversed(done)):
        assert cmd.revert(design) == []
        _assert_valid(design, full=i % FULL_EVERY == 0)
    _assert_same(design, start)
    for cmd in done:
        assert cmd.apply(design) == []
    _assert_same(design, end)
    kinds = {type(cmd).__name__ for cmd in done}
    assert {"AddBus", "AddElement", "AddRegulator", "RemoveElement", "RemoveBus", "Connect",
            "Disconnect", "SetProperty", "Rename", "SetSwitchOpen", "MoveGlyph", "RotateGlyph",
            "Compound"} <= kinds  # fmt: skip


FULL_EVERY = 25


@pytest.mark.parametrize("seed", [1, 7, 2026])
def test_random_sequences_undo_and_redo_exactly(seed: int) -> None:
    _run_random(_design(), seed)


@opendss
def test_random_sequence_on_ieee13() -> None:
    from tests.core.grid.test_topology import _feeder_model

    _run_random(_design(copy.deepcopy(_feeder_model("ieee13"))), 13)


# ------------------------------------------------------------------ the proof test

_SKIP = {"name", "bus1", "bus2", "terminal", "windings", "closed"}


@opendss
def test_rebuild_ieee13_from_an_empty_design_with_commands_only() -> None:
    """CLAUDE.md "Conventions": rebuild IEEE 13 with edit commands only (values read
    from the extracted GridModel of the fixture), run it, compare, undo everything.

    The comparison follows G3 and D2 (test_writer.py): opendss_reference.json is
    OpenDSS's default-tolerance solve inside `compile`, and any re-solve, the original
    file included, is up to 1.4e-5 pu from it (S4 b), so 1e-6 pu is checked against the
    original file solved the same way as the design (Tolerance 1e-10), and the original
    against the reference within 1e-4 pu. The rebuilt GridModel itself must equal the
    extracted one exactly (as a design: derived voltage bases)."""
    from core.grid.engines.opendss.engine import OpenDSSEngine
    from tests.core.grid.engines.opendss.test_writer import (
        CONFIG,
        _assert_same_solution,
        _original,
        _pu_by_node,
    )
    from tests.core.grid.helpers import reference
    from tests.core.grid.test_topology import _feeder_model

    fixture = copy.deepcopy(_feeder_model("ieee13"))
    start = GridDesign(
        GridModel(fixture.name, fixture.base_frequency, derive_voltage_bases=True), DiagramModel()
    )
    design = copy.deepcopy(start)
    g = design.grid
    done: list[c.Command] = []

    def do(cmd: c.Command) -> Any:
        out = cmd.apply(design)
        assert out == [], (cmd.description, [d.message for d in out])
        assert check_consistency(g, design.diagram) == []
        done.append(cmd)
        return cmd

    def copy_fields(el_id: str, target: Any, source: Any, prefix: str = "") -> None:
        for f in dataclasses.fields(source):
            want = getattr(source, f.name)
            if f.name not in _SKIP and getattr(target, f.name) != want:
                do(c.SetProperty(el_id, prefix + f.name, want))

    def add(kind: str, name: str, terminals: list[Terminal], **kw: Any) -> str:
        cmd = do(c.AddElement(kind, terminals, **kw))
        do(c.Rename(cmd.element_id, name))
        return f"{c.KINDS[kind].cls.cls}.{name}"

    for b in fixture.buses.values():
        do(c.AddBus(b.name))
        copy_fields(f"bus.{b.name}", g.buses[b.name], b)
    for s in fixture.sources.values():
        vals = {f: getattr(s, f) for f in c.REQUIRED_VALUES["vsource"]}
        el_id = add("vsource", s.name, [s.bus1], values=vals)
        copy_fields(el_id, g.sources[s.name], s)
    for lc in fixture.linecodes.values():
        el_id = add("linecode", lc.name, [], phases=lc.phases)
        copy_fields(el_id, g.linecodes[lc.name], lc)
    regs = {r.transformer: r for r in fixture.regulators.values()}
    for t in fixture.transformers.values():
        terms = [w.terminal for w in t.windings]
        if t.name in regs:
            r = regs[t.name]
            vals = {f: getattr(r, f) for f in c.REQUIRED_VALUES["regcontrol"]}
            cmd = do(c.AddRegulator(*terms, winding=r.winding, regulator_values=vals))
            tx, rc = cmd.children
            do(c.Rename(tx.element_id, t.name))
            do(c.Rename(f"regcontrol.{rc.name}", r.name))
            copy_fields(r.id, g.regulators[r.name], r)
        else:
            add("transformer", t.name, terms)
        for i, w in enumerate(t.windings):
            copy_fields(t.id, g.transformers[t.name].windings[i], w, f"windings.{i}.")
        copy_fields(t.id, g.transformers[t.name], t)
    for ln in fixture.lines.values():
        el_id = add("line", ln.name, [ln.bus1, ln.bus2])
        copy_fields(el_id, g.lines[ln.name], ln)
    for sw in fixture.switches.values():
        el_id = add("switch", sw.name, [sw.bus1, sw.bus2], values={"r_ohm": sw.r_ohm})
        copy_fields(el_id, g.switches[sw.name], sw)
        if not sw.closed:
            do(c.SetSwitchOpen(el_id, True))
    for kind, attr in (("load", "loads"), ("capacitor", "capacitors"), ("pvsystem", "pvsystems")):
        for el in getattr(fixture, attr).values():
            el_id = add(kind, el.name, [el.terminal])
            copy_fields(el_id, getattr(g, attr)[el.name], el)

    expected = dataclasses.replace(fixture, voltage_bases=[], derive_voltage_bases=True)
    assert g == expected
    assert (
        _order(design)[: len(CLASS_DICTS)]
        == _order(GridDesign(expected, DiagramModel()))[: len(CLASS_DICTS)]
    )
    assert [d for d in v.validate(g) if d.severity == ERROR] == []
    assert len(done) > 100

    original, a = _original("ieee13")
    engine = OpenDSSEngine()
    try:
        assert engine.load_model(g) == []
        b = engine.run(CONFIG)
        assert b.converged and b.ok, b.diagnostics
        ref = reference("ieee13")
        ref_pu = dict(zip(ref["node_names"], ref["node_pu"], strict=True))
        pa, pb = _pu_by_node(a), _pu_by_node(b)
        assert sorted(pb) == sorted(ref_pu)
        assert max(abs(pa[n] - ref_pu[n]) for n in ref_pu) < 1e-4
        assert max(abs(pb[n] - ref_pu[n]) for n in ref_pu) < 1e-4
        assert math.isclose(b.total_losses_kw, ref["losses_kw"], rel_tol=1e-3)
        _assert_same_solution(a, b)  # 1e-6 pu on every node, 0.1 % losses, same taps
    finally:
        engine.close()
        original.close()

    rebuilt = _snap(design)
    for cmd in reversed(done):
        assert cmd.revert(design) == []
        assert check_consistency(design.grid, design.diagram) == []
    _assert_same(design, _snap(start))
    for cmd in done:
        assert cmd.apply(design) == []
    _assert_same(design, rebuilt)
