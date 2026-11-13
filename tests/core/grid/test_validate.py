"""core/grid/validate.py: every check passes on a good model and fails with its own
code on the right element; the IEEE feeders give no errors."""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from core.grid import validate as v
from core.grid.model import (
    DELTA,
    LOAD_PQ,
    WYE,
    Bus,
    Capacitor,
    GridModel,
    Line,
    LineCode,
    Load,
    Regulator,
    Source,
    Switch,
    Terminal,
    Transformer,
    Winding,
)
from core.netlist.model import ERROR, WARNING
from core.physical.component import NetworkDiagnostic

T = Terminal.parse
MV_LN = 11.0 / math.sqrt(3.0)
LV_LN = 0.4 / math.sqrt(3.0)


def _code(name: str, phases: int) -> LineCode:
    n = phases * phases
    return LineCode(name, phases, (0.3,) * n, (0.4,) * n, (10.0,) * n, 400.0, 600.0)


def good_model() -> GridModel:
    """src -l1- a =t1= b -sw1- c -l2(1-ph)- d. Every check passes."""
    m = GridModel("demo", 50.0, [11.0, 0.4])
    for name, base in (("src", MV_LN), ("a", MV_LN), ("b", LV_LN), ("c", LV_LN), ("d", LV_LN)):
        m.buses[name] = Bus(name, base)
    m.sources["s"] = Source("s", T("src"), T("src.0.0.0"), 3, 11.0, 1.0, 0.0, 0.1, 1, 0.2, 2)
    m.linecodes["lc3"] = _code("lc3", 3)
    m.linecodes["lc1"] = _code("lc1", 1)
    m.lines["l1"] = Line("l1", T("src.1.2.3"), T("a.1.2.3"), 3, 100.0, "lc3")
    m.transformers["t1"] = Transformer(
        "t1",
        3,
        [
            Winding(T("a"), DELTA, 11.0, 500.0, 0.5),
            Winding(T("b.1.2.3.0"), WYE, 0.41, 500.0, 0.5),  # 2.5 %: within 5 %
        ],
        xhl=4.0,
        xht=4.0,
        xlt=4.0,
    )
    m.regulators["r1"] = Regulator("r1", "t1", 2, 120.0, 2.0, 2.0, 700.0, 0.0, 0.0)
    m.switches["sw1"] = Switch("sw1", T("b"), T("c"), 3, True, 1e-4)
    m.lines["l2"] = Line("l2", T("c.1"), T("d.1"), 1, 50.0, "lc1")
    m.loads["ld3"] = Load("ld3", T("b"), 3, WYE, 0.4, 100.0, 30.0, LOAD_PQ)
    m.loads["ld1"] = Load("ld1", T("d.1.0"), 1, WYE, 0.23, 10.0, 3.0, LOAD_PQ)
    m.loads["ldd"] = Load("ldd", T("c.1.2"), 1, DELTA, 0.4, 10.0, 3.0, LOAD_PQ)
    m.loads["ldll"] = Load("ldll", T("c.2.3"), 1, WYE, 0.4, 10.0, 3.0, LOAD_PQ)  # wye, LL
    m.capacitors["cap1"] = Capacitor("cap1", T("a"), 3, DELTA, 11.0, 300.0)
    return m


def _only(diags: list[NetworkDiagnostic], code: str) -> list[NetworkDiagnostic]:
    return [d for d in diags if d.code == code]


def test_good_model_passes() -> None:
    assert v.validate(good_model()) == []


def test_every_code_is_listed() -> None:
    assert len(set(v.CODES)) == len(v.CODES)
    assert all(c == c.lower() and " " not in c for c in v.CODES)


def _add_dup_switch(m: GridModel) -> None:
    m.switches["l1"] = Switch("l1", T("a"), T("c"), 3, True, 1e-4)  # Line.l1 already


def _add_island(m: GridModel) -> None:
    m.buses["e"] = Bus("e", LV_LN)
    m.loads["le"] = Load("le", T("e"), 3, WYE, 0.4, 1.0, 0.0, LOAD_PQ)


def _set(obj_path: Callable[[GridModel], object], attr: str, value: object):
    def mutate(m: GridModel) -> None:
        setattr(obj_path(m), attr, value)

    return mutate


# (id, mutation, code, element, severity)
FAILURES: list[tuple[str, Callable[[GridModel], None], str, str, str]] = [
    ("unknown bus", _set(lambda m: m.loads["ld3"], "terminal", T("zz")),
     v.UNKNOWN_BUS, "load.ld3", ERROR),
    ("unknown bus2", _set(lambda m: m.lines["l2"], "bus2", T("zz.1")),
     v.UNKNOWN_BUS, "line.l2", ERROR),
    ("duplicate line/switch", _add_dup_switch, v.DUPLICATE_NAME, "line.l1", ERROR),
    ("unreachable", _add_island, v.UNREACHABLE_BUS, "bus.e", ERROR),
    ("disabled line", _set(lambda m: m.lines["l2"], "enabled", False),
     v.UNREACHABLE_BUS, "bus.d", ERROR),
    ("open switch", _set(lambda m: m.switches["sw1"], "closed", False),
     v.OPEN_SWITCH_ISOLATED, "bus.c", WARNING),
    ("line nodes", _set(lambda m: m.lines["l1"], "bus1", T("src.1.2")),
     v.PHASE_NODE_MISMATCH, "line.l1", ERROR),
    ("wye nodes", _set(lambda m: m.loads["ld3"], "terminal", T("b.1.2.3.0.0")),
     v.PHASE_NODE_MISMATCH, "load.ld3", ERROR),
    ("delta 3 nodes", _set(lambda m: m.loads["ldd"], "terminal", T("c.1.2.3")),
     v.PHASE_NODE_MISMATCH, "load.ldd", ERROR),
    ("delta 1 node", _set(lambda m: m.loads["ldd"], "terminal", T("c.1")),
     v.PHASE_NODE_MISMATCH, "load.ldd", WARNING),
    ("winding nodes", _set(lambda m: m.transformers["t1"].windings[0], "terminal", T("a.1.2")),
     v.PHASE_NODE_MISMATCH, "transformer.t1", ERROR),
    ("phase not on bus1", _set(lambda m: m.lines["l2"], "bus2", T("d.2")),
     v.PHASE_NOT_ON_BUS1, "line.l2", ERROR),
    ("switch phase", _set(lambda m: m.switches["sw1"], "bus1", T("b.1.2.4")),
     v.PHASE_NOT_ON_BUS1, "line.sw1", ERROR),
    ("unknown linecode", _set(lambda m: m.lines["l1"], "linecode", "nope"),
     v.UNKNOWN_LINECODE, "line.l1", ERROR),
    ("linecode phases", _set(lambda m: m.lines["l2"], "linecode", "lc3"),
     v.LINECODE_PHASES, "line.l2", ERROR),
    ("zero length", _set(lambda m: m.lines["l1"], "length_m", 0.0),
     v.NONPOSITIVE_LENGTH, "line.l1", ERROR),
    ("negative length", _set(lambda m: m.lines["l2"], "length_m", -5.0),
     v.NONPOSITIVE_LENGTH, "line.l2", ERROR),
    ("winding kv", _set(lambda m: m.transformers["t1"].windings[1], "kv", 0.48),
     v.WINDING_KV, "transformer.t1", ERROR),
    ("winding kv LN as LL", _set(lambda m: m.transformers["t1"].windings[0], "kv", MV_LN),
     v.WINDING_KV, "transformer.t1", ERROR),
    ("1-ph wye load on LL kV", _set(lambda m: m.loads["ld1"], "kv", 0.4),
     v.ELEMENT_KV, "load.ld1", ERROR),
    ("3-ph load on LN kV", _set(lambda m: m.loads["ld3"], "kv", 0.23),
     v.ELEMENT_KV, "load.ld3", ERROR),
    ("delta load on LN kV", _set(lambda m: m.loads["ldd"], "kv", 0.23),
     v.ELEMENT_KV, "load.ldd", ERROR),
    ("delta capacitor on LN kV", _set(lambda m: m.capacitors["cap1"], "kv", MV_LN),
     v.ELEMENT_KV, "capacitor.cap1", ERROR),
    ("regulator transformer", _set(lambda m: m.regulators["r1"], "transformer", "nope"),
     v.REGULATOR_TARGET, "regcontrol.r1", ERROR),
    ("regulator winding", _set(lambda m: m.regulators["r1"], "winding", 3),
     v.REGULATOR_TARGET, "regcontrol.r1", ERROR),
    ("regulator winding 0", _set(lambda m: m.regulators["r1"], "winding", 0),
     v.REGULATOR_TARGET, "regcontrol.r1", ERROR),
    ("transformer kva", _set(lambda m: m.transformers["t1"].windings[0], "kva", 0.0),
     v.TRANSFORMER_KVA, "transformer.t1", ERROR),
    ("no voltage bases", _set(lambda m: m, "voltage_bases", []),
     v.NO_VOLTAGE_BASES, v.MODEL, WARNING),
    ("no source", lambda m: m.sources.clear(), v.NO_SOURCE, v.MODEL, ERROR),
    ("disabled source", _set(lambda m: m.sources["s"], "enabled", False),
     v.NO_SOURCE, v.MODEL, ERROR),
]  # fmt: skip


@pytest.mark.parametrize(
    ("mutate", "code", "element", "severity"),
    [f[1:] for f in FAILURES],
    ids=[f[0] for f in FAILURES],
)
def test_each_check_fails_with_its_code_on_the_element(
    mutate: Callable[[GridModel], None], code: str, element: str, severity: str
) -> None:
    m = good_model()
    mutate(m)
    found = _only(v.validate(m), code)
    assert [(d.block, d.severity) for d in found if d.block == element] == [(element, severity)]
    assert all(d.message for d in found)


def test_every_code_has_a_failing_case() -> None:
    tested = {f[2] for f in FAILURES}
    assert tested == set(v.CODES) - {v.VALIDATION_FAILED}


def test_open_switch_gives_only_warnings_downstream() -> None:
    m = good_model()
    m.switches["sw1"].closed = False
    diags = v.validate(m)
    assert {d.block for d in diags} == {"bus.c", "bus.d"}
    assert all(d.severity == WARNING and d.code == v.OPEN_SWITCH_ISOLATED for d in diags)


def test_incomplete_model_reports_reachability_as_warnings() -> None:
    m = good_model()
    del m.lines["l1"]  # e.g. a Reactor the extractor does not support
    strict = _only(v.validate(m), v.UNREACHABLE_BUS)
    assert {d.block for d in strict} == {"bus.a", "bus.b", "bus.c", "bus.d"}
    assert all(d.severity == ERROR for d in strict)
    lenient = v.validate(m, incomplete=True)
    assert [d.code for d in lenient] == [v.UNREACHABLE_BUS] * 4
    assert all(d.severity == WARNING for d in lenient)


def test_kv_checks_skip_buses_without_a_base() -> None:
    m = good_model()
    m.buses["d"].kv_base_ln = 0.0
    m.loads["ld1"].kv = 99.0
    assert v.validate(m) == []


def test_never_raises_on_a_malformed_model() -> None:
    m = good_model()
    m.lines["l1"] = None  # type: ignore[assignment]
    m.transformers["t1"].windings = "junk"  # type: ignore[assignment]
    diags = v.validate(m)
    failed = _only(diags, v.VALIDATION_FAILED)
    assert failed and all(d.severity == ERROR and d.block == v.MODEL for d in failed)


# ---- the IEEE feeders (OpenDSS needed to read them)

# Warnings each feeder is expected to give: IEEE 34 has six 1-phase delta loads with
# one node (Bus1=832.1 Conn=Delta); OpenDSS grounds the second node.
FEEDER_WARNINGS = {
    "ieee13": set(),
    "ieee34": {(v.PHASE_NODE_MISMATCH, f"load.d832_858{end}{ph}") for end in "sr" for ph in "abc"},
    "ieee123": set(),
}


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", ["ieee13", "ieee34", "ieee123"])
def test_ieee_feeders_give_no_errors(feeder: str) -> None:
    pytest.importorskip("dss")
    from core.grid.engines.opendss.engine import OpenDSSEngine
    from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES

    engine = OpenDSSEngine()
    assert engine.load_native(FEEDER_FILES[feeder]) == []
    for command in AFTER_COMPILE[feeder]:
        engine.context.Text.Command = command
    model, _ = engine.extract_model()
    engine.close()
    assert model is not None
    diags = v.validate(model)
    assert [d for d in diags if d.severity == ERROR] == []
    assert {(d.code, d.block) for d in diags} == FEEDER_WARNINGS[feeder]
