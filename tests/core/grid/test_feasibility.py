"""D7 feasibility: loadings, voltage drop and the FeasibilityReport (core/grid/analysis.py).

Reference values come from tests/fixtures/grid/<feeder>/opendss_reference.json (made
by scratch/phase4/make_reference.py, never edited by hand): per-branch currents and
powers of every terminal and conductor, NormalAmps and OpenDSS's own % of normal
(PDElements.AllPctNorm). OpenDSS has no kVA-based transformer loading (its % uses
NormHkVA = 1.1 kVA, S4), so transformer loadings are compared with |S| computed from
the reference terminal powers only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pytest

from core.grid.analysis import (
    CHECK_LINE_LOADING,
    CHECK_TRANSFORMER_LOADING,
    CHECK_VOLTAGE_MAX,
    CHECK_VOLTAGE_MIN,
    FAIL,
    NO_RATING,
    PASS,
    SNAPSHOT_LIMITATION,
    WARN,
    Loading,
    _loading_check,
    feasibility_report,
    line_loading,
    line_rating,
    transformer_loading,
    voltage_drop,
)
from core.grid.config import PowerFlowConfig
from core.grid.model import (
    LOAD_PQ,
    WYE,
    Bus,
    GridModel,
    Line,
    LineCode,
    Load,
    Source,
    Terminal,
)
from core.grid.result import BranchResult, GridResult
from core.netlist.model import INFO
from tests.core.grid.helpers import reference, solve_feeder

FEEDERS = ("ieee13", "ieee34", "ieee123")

# How close OpenDSSEngine's own solve is to the reference solve. IEEE 34 and 123 are
# solved the same way (one explicit Solve): bit-identical. IEEE 13 solves inside
# `compile` in the reference script and the engine solves once more, which moves the
# solution (v0.4 notes: compare solutions made the same way); measured: currents
# 9.4e-5, losses 1.2e-4 relative, 1.3e-5 pu.
ENGINE_REL = {"ieee13": 1e-3, "ieee34": 1e-12, "ieee123": 1e-12}


def _pairs(rows: list[list[float]]) -> np.ndarray:
    arr = np.asarray(rows, dtype=float)
    return arr[:, 0] + 1j * arr[:, 1]


def _reference_result(ref: dict[str, Any]) -> GridResult:
    """A GridResult holding exactly the reference solve (nodes, branches, totals)."""
    branches: dict[str, BranchResult] = {}
    for element, b in ref["branches"].items():
        n = b["conductors"]
        powers = _pairs(b["powers"])
        branches[element] = BranchResult(
            element=element,
            bus1=b["bus_names"][0].split(".")[0],
            bus2=b["bus_names"][1].split(".")[0] if len(b["bus_names"]) > 1 else "",
            phases=b["phases"],
            current=_pairs(b["currents"])[: b["phases"]],
            p_kw=float(powers[:n].real.sum()),
            q_kvar=float(powers[:n].imag.sum()),
            losses_kw=0.0,
            losses_kvar=0.0,
            normal_amps=b["norm_amps"],
            loading_pct=b["pct_norm"],
            terminal_power=tuple(
                complex(powers[k * n : (k + 1) * n].sum()) for k in range(b["terminals"])
            ),
        )
    return GridResult(
        converged=ref["converged"],
        node_names=list(ref["node_names"]),
        node_voltage=_pairs(ref["node_v"]),
        node_voltage_pu=np.asarray(ref["node_pu"], dtype=float),
        branches=branches,
        total_power_kw=-ref["total_power_kw"],  # OpenDSS sign: negative = supplied
        total_power_kvar=-ref["total_power_kvar"],
        total_losses_kw=ref["losses_kw"],
        total_losses_kvar=ref["losses_kvar"],
    )


def _ref_line_pct(model: GridModel, ref: dict[str, Any], line_name: str) -> float:
    current = _pairs(ref["currents"])[: model.lines[line_name].phases]
    rating = line_rating(model, line_name)
    assert rating is not None
    return float(np.abs(current).max()) / rating * 100.0


def _ref_transformer_pct(model: GridModel, ref: dict[str, Any], name: str) -> float:
    powers = _pairs(ref["powers"])
    n = ref["conductors"]
    windings = model.transformers[name].windings
    assert ref["terminals"] == len(windings)
    return max(
        abs(powers[k * n : (k + 1) * n].sum()) / w.kva * 100.0
        for k, w in enumerate(windings)
        if w.kva
    )


# ---- IEEE feeders against the OpenDSS reference


@dataclass
class Solved:
    feeder: str
    model: GridModel
    """Extracted from the original .dss file."""
    engine: GridResult
    """OpenDSSEngine's solve of the file."""
    ref: dict[str, Any]
    reference: GridResult
    """The reference solve itself (_reference_result)."""


@pytest.fixture(scope="module", params=FEEDERS)
def solved(request: pytest.FixtureRequest) -> Solved:
    pytest.importorskip("dss")
    model, result = solve_feeder(request.param)
    ref = reference(request.param)
    return Solved(request.param, model, result, ref, _reference_result(ref))


@pytest.mark.opendss
def test_line_loading_matches_reference_currents_and_opendss(solved: Solved) -> None:
    model, branches = solved.model, solved.ref["branches"]
    rows, diags = line_loading(model, solved.reference)
    assert diags == []  # every IEEE line has a rating (OpenDSS's 400 A default, read back)
    assert [r.element for r in rows] == [ln.id for ln in model.lines.values() if ln.enabled]
    for row in rows:
        ref = branches[row.element]
        name = row.element.split(".", 1)[1]
        assert row.rating == ref["norm_amps"]  # the model's rating is the one OpenDSS used
        assert row.pct == pytest.approx(_ref_line_pct(model, ref, name), rel=1e-12)
        # OpenDSS's own % of normal (PDElements.AllPctNorm, max conductor at terminal 1)
        assert row.pct == pytest.approx(ref["pct_norm"], rel=1e-9)
    # the engine's solve gives the same loadings
    engine_rows, _ = line_loading(model, solved.engine)
    for row, eng in zip(rows, engine_rows, strict=True):
        assert eng.element == row.element
        assert eng.pct == pytest.approx(row.pct, rel=ENGINE_REL[solved.feeder], abs=1e-9)


@pytest.mark.opendss
def test_transformer_loading_matches_reference_terminal_powers(solved: Solved) -> None:
    model, branches = solved.model, solved.ref["branches"]
    rows = transformer_loading(model, solved.reference)
    assert [r.element for r in rows] == [t.id for t in model.transformers.values() if t.enabled]
    assert {r.element for r in rows} == {k for k in branches if k.startswith("transformer.")}
    for row in rows:
        name = row.element.split(".", 1)[1]
        expected = _ref_transformer_pct(model, branches[row.element], name)
        assert row.pct == pytest.approx(expected, rel=1e-12)
        assert row.rating == model.transformers[name].windings[row.winding - 1].kva
    # the engine's per-terminal powers (BranchResult.terminal_power) give the same
    engine_rows = transformer_loading(model, solved.engine)
    for row, eng in zip(rows, engine_rows, strict=True):
        assert (eng.element, eng.winding) == (row.element, row.winding)
        assert eng.pct == pytest.approx(row.pct, rel=ENGINE_REL[solved.feeder])


@pytest.mark.opendss
def test_report_losses_and_summary_equal_the_reference(solved: Solved) -> None:
    model, ref = solved.model, solved.ref
    report = feasibility_report(model, solved.reference, PowerFlowConfig())
    assert report.solved and report.level1_ok
    assert report.limitation == SNAPSHOT_LIMITATION
    s = report.summary
    assert s is not None
    assert (s.losses_kw, s.losses_kvar) == (ref["losses_kw"], ref["losses_kvar"])
    assert (s.source_kw, s.source_kvar) == (-ref["total_power_kw"], -ref["total_power_kvar"])
    assert s.load_kw == pytest.approx(sum(ld.kw for ld in model.loads.values() if ld.enabled))

    # min / max V over the phase nodes, from the reference node voltages
    phase_nodes = [
        (n.rsplit(".", 1)[0], pu)
        for n, pu in zip(ref["node_names"], ref["node_pu"], strict=True)
        if n.rsplit(".", 1)[1] in ("1", "2", "3")
    ]
    lo = min(phase_nodes, key=lambda bp: bp[1])
    hi = max(phase_nodes, key=lambda bp: bp[1])
    assert (s.v_min_bus, s.v_min_pu, s.v_max_bus, s.v_max_pu) == (lo[0], lo[1], hi[0], hi[1])
    vmin, vmax = report.check(CHECK_VOLTAGE_MIN), report.check(CHECK_VOLTAGE_MAX)
    assert (vmin.worst_element, vmin.status) == (f"bus.{lo[0]}", FAIL if lo[1] < 0.95 else PASS)
    assert (vmax.worst_element, vmax.status) == (f"bus.{hi[0]}", FAIL if hi[1] > 1.05 else PASS)

    # largest loadings, from the reference currents and powers
    branches = ref["branches"]
    line_pct = {
        ln.id: _ref_line_pct(model, branches[ln.id], n)
        for n, ln in model.lines.items()
        if ln.enabled
    }
    worst_line = max(line_pct, key=line_pct.__getitem__)
    assert (s.max_line_loading_element, s.max_line_loading_pct) == (
        worst_line,
        pytest.approx(line_pct[worst_line], rel=1e-12),
    )
    xf_pct = {
        t.id: _ref_transformer_pct(model, branches[t.id], n)
        for n, t in model.transformers.items()
        if t.enabled
    }
    worst_xf = max(xf_pct, key=xf_pct.__getitem__)
    assert (s.max_transformer_loading_element, s.max_transformer_loading_pct) == (
        worst_xf,
        pytest.approx(xf_pct[worst_xf], rel=1e-12),
    )
    assert report.check(CHECK_LINE_LOADING).worst_element == worst_line
    assert report.check(CHECK_TRANSFORMER_LOADING).worst_element == worst_xf

    # the same report from the engine's own solve
    eng = feasibility_report(model, solved.engine, PowerFlowConfig())
    assert eng.summary is not None
    rel = ENGINE_REL[solved.feeder]
    assert eng.summary.losses_kw == pytest.approx(ref["losses_kw"], rel=rel)
    assert eng.summary.losses_kvar == pytest.approx(ref["losses_kvar"], rel=rel)
    assert eng.summary.max_line_loading_element == worst_line
    assert eng.summary.max_transformer_loading_element == worst_xf


@pytest.mark.opendss
def test_voltage_drop_follows_the_reference_voltages(solved: Solved) -> None:
    model, ref = solved.model, solved.ref
    pu = dict(zip(ref["node_names"], ref["node_pu"], strict=True))
    drops = voltage_drop(model, solved.reference)
    (src,) = {s.bus1.bus for s in model.sources.values()}
    by_bus = {d.bus: d for d in drops}
    assert by_bus[src].drop_pu == 0.0 and by_bus[src].path == ()
    branch_ids = {
        *(ln.id for ln in model.lines.values()),
        *(sw.id for sw in model.switches.values()),
        *(t.id for t in model.transformers.values()),
    }
    for d in drops:
        assert d.source_bus == src
        node = f"{d.bus}.{'abc'.index(d.phase) + 1}"
        src_node = f"{src}.{'abc'.index(d.phase) + 1}"
        assert d.drop_pu == pu[src_node] - pu[node]
        assert set(d.path) <= branch_ids and (len(d.path) > 0) == (d.bus != src)
    # every energized bus with a phase node is listed
    assert set(by_bus) == {n.rsplit(".", 1)[0] for n in pu if n.rsplit(".", 1)[1] in "123"}


# ---- a hand-built design


def _design(rating_l2: float | None) -> GridModel:
    """src -l1- a -l2- b (load), a -l3- c (load); 11 kV, 50 Hz. l1 has a large own
    rating, l2 its own ``rating_l2``, l3 none (its linecode has none either)."""
    diag = lambda v: (v, 0.0, 0.0, 0.0, v, 0.0, 0.0, 0.0, v)  # noqa: E731
    t = Terminal.parse
    return GridModel(
        name="design",
        base_frequency=50.0,
        derive_voltage_bases=True,
        buses={n: Bus(n, 0.0) for n in ("src", "a", "b", "c")},
        sources={
            "source": Source(
                "source", t("src"), t("src.0.0.0"), 3, 11.0, 1.0, 0.0, 0.01, 0.1, 0.01, 0.1
            )
        },
        linecodes={"oh": LineCode("oh", 3, diag(0.25), diag(0.35), diag(10.0))},
        lines={
            "l1": Line("l1", t("src.1.2.3"), t("a.1.2.3"), 3, 1000.0, "oh", norm_amps=1e6),
            "l2": Line("l2", t("a.1.2.3"), t("b.1.2.3"), 3, 500.0, "oh", norm_amps=rating_l2),
            "l3": Line("l3", t("a.1.2.3"), t("c.1.2.3"), 3, 500.0, "oh"),
        },
        loads={
            "ld1": Load("ld1", t("b.1.2.3"), 3, WYE, 11.0, 500.0, 200.0, LOAD_PQ),
            "ld2": Load("ld2", t("c.1.2.3"), 3, WYE, 11.0, 100.0, 50.0, LOAD_PQ),
        },
    )


def _solve(model: GridModel) -> GridResult:
    from core.grid.engines.opendss.engine import OpenDSSEngine

    engine = OpenDSSEngine()
    try:
        assert engine.load_model(model) == []
        result = engine.run(PowerFlowConfig())
    finally:
        engine.close()
    assert result.converged
    return result


@pytest.mark.opendss
def test_design_with_one_overloaded_line_fails_on_that_line() -> None:
    pytest.importorskip("dss")
    model = _design(rating_l2=1.0)  # far below the load current of b
    result = _solve(model)
    report = feasibility_report(model, result, PowerFlowConfig())
    assert report.level1_ok
    check = report.check(CHECK_LINE_LOADING)
    assert (check.status, check.worst_element) == (FAIL, "line.l2")
    over = [r.element for r in report.line_loadings if r.pct > check.limit]
    assert over == ["line.l2"]
    assert report.status == FAIL
    # l3 has no rating: an info Diagnostic and no value
    assert [(d.block, d.severity, d.code) for d in report.diagnostics] == [
        ("line.l3", INFO, NO_RATING)
    ]
    assert "line.l3" not in {r.element for r in report.line_loadings}
    assert report.summary is not None
    assert report.summary.losses_kw == pytest.approx(result.total_losses_kw)


@pytest.mark.opendss
def test_design_within_limits_passes_the_line_check() -> None:
    pytest.importorskip("dss")
    model = _design(rating_l2=1e6)
    report = feasibility_report(model, _solve(model), PowerFlowConfig())
    assert report.check(CHECK_LINE_LOADING).status == PASS


# ---- without OpenDSS


def _branch(element: str, amps: list[float], terminal_power: tuple[complex, ...] = ()) -> Any:
    return BranchResult(
        element=element,
        bus1="x",
        bus2="y",
        phases=len(amps),
        current=np.asarray(amps, dtype=complex),
        p_kw=0.0,
        q_kvar=0.0,
        losses_kw=0.0,
        losses_kvar=0.0,
        normal_amps=400.0,
        loading_pct=None,
        terminal_power=terminal_power,
    )


def test_line_without_norm_amps_gives_the_info_diagnostic() -> None:
    model = _design(rating_l2=None)
    result = GridResult(
        converged=True,
        branches={
            "line.l1": _branch("line.l1", [10.0, 12.0, 11.0]),
            "line.l2": _branch("line.l2", [5.0, 5.0, 5.0]),
            "line.l3": _branch("line.l3", [5.0, 5.0, 5.0]),
        },
    )
    rows, diags = line_loading(model, result)
    assert [r.element for r in rows] == ["line.l1"]
    assert rows[0].value == 12.0 and rows[0].rating == 1e6
    assert [(d.block, d.severity, d.code) for d in diags] == [
        ("line.l2", INFO, NO_RATING),
        ("line.l3", INFO, NO_RATING),
    ]
    assert all("no rating" in d.message for d in diags)


def test_linecode_rating_is_used_when_the_line_has_none() -> None:
    model = _design(rating_l2=None)
    model.linecodes["oh"].norm_amps = 50.0
    assert line_rating(model, "l3") == 50.0
    assert line_rating(model, "l1") == 1e6


def test_loading_status_thresholds() -> None:
    def status(pct: float) -> str:
        return _loading_check("x", [Loading("line.a", pct, 0.0, 1.0)], 100.0, 0.9).status

    assert status(89.9) == PASS
    assert status(90.0) == WARN
    assert status(100.0) == WARN
    assert status(100.1) == FAIL
    empty = _loading_check("x", [], 100.0, 0.9)
    assert (empty.status, empty.worst_value, empty.count) == (PASS, None, 0)


def test_unsolved_report_fails_and_states_the_limitation() -> None:
    model = _design(rating_l2=10.0)
    for result in (None, GridResult.failed([])):
        report = feasibility_report(model, result, PowerFlowConfig())
        assert report.level1_ok and not report.solved
        assert report.level2 == [] and report.summary is None
        assert report.status == FAIL
        assert "snapshot" in report.limitation and "protection" in report.limitation


def test_level1_holds_the_validation_errors() -> None:
    model = _design(rating_l2=10.0)
    model.loads["ld1"].kw = None  # a draft value
    report = feasibility_report(model, None, PowerFlowConfig())
    assert not report.level1_ok
    assert any(d.block == "load.ld1" for d in report.level1)


def test_voltage_checks_use_the_config_band() -> None:
    model = _design(rating_l2=10.0)
    result = GridResult(
        converged=True,
        node_names=["src.1", "src.2", "src.3", "a.1", "b.2", "c.3"],
        node_voltage_pu=np.array([1.0, 1.0, 1.0, 0.97, 0.93, 1.06]),
    )
    report = feasibility_report(model, result, PowerFlowConfig())
    lo, hi = report.check(CHECK_VOLTAGE_MIN), report.check(CHECK_VOLTAGE_MAX)
    assert (lo.status, lo.worst_element, lo.worst_value) == (FAIL, "bus.b", 0.93)
    assert (hi.status, hi.worst_element, hi.worst_value) == (FAIL, "bus.c", 1.06)
    wide = feasibility_report(
        model, result, replace(PowerFlowConfig(), voltage_min_pu=0.9, voltage_max_pu=1.1)
    )
    assert {c.status for c in wide.level2} == {PASS}
    drops = {d.bus: d for d in report.voltage_drops}
    assert drops["b"].phase == "b" and drops["b"].drop_pu == pytest.approx(0.07)
    assert drops["b"].path == ("line.l1", "line.l2")
    assert drops["c"].drop_pu == pytest.approx(-0.06)


def test_transformer_loading_takes_the_most_loaded_winding() -> None:
    from core.grid.model import Transformer, Winding

    t = Terminal.parse
    model = _design(rating_l2=10.0)
    model.buses["lv"] = Bus("lv", 0.0)
    model.transformers["tx1"] = Transformer(
        "tx1",
        3,
        [Winding(t("c"), WYE, 11.0, 1000.0, 0.5), Winding(t("lv"), WYE, 0.4, 250.0, 0.5)],
        4.0,
        None,
        None,
    )
    result = GridResult(
        converged=True,
        branches={
            "transformer.tx1": _branch(
                "transformer.tx1", [1.0, 1.0, 1.0], (300 + 400j, -(120 + 160j))
            )
        },
    )
    (row,) = transformer_loading(model, result)
    assert (row.winding, row.value, row.rating) == (2, 200.0, 250.0)
    assert row.pct == pytest.approx(80.0)


def test_voltage_checks_skip_buses_cut_off_by_an_open_switch() -> None:
    from core.grid.model import Switch
    from core.grid.validate import OPEN_SWITCH_ISOLATED

    t = Terminal.parse
    model = _design(rating_l2=10.0)
    model.buses["e"] = Bus("e", 0.0)
    model.switches["sw1"] = Switch("sw1", t("a"), t("e"), 3, closed=False, r_ohm=1e-4)
    model.loads["ld3"] = Load("ld3", t("e.1.2.3"), 3, WYE, 11.0, 10.0, 5.0, LOAD_PQ)
    result = GridResult(
        converged=True,
        node_names=["src.1", "a.1", "b.1", "c.1", "e.1", "e.2", "e.3"],
        node_voltage_pu=np.array([1.0, 0.99, 0.98, 0.985, 0.0, 0.0, 0.0]),
    )
    report = feasibility_report(model, result, PowerFlowConfig())
    lo = report.check(CHECK_VOLTAGE_MIN)
    assert (lo.status, lo.worst_element, lo.count) == (PASS, "bus.b", 4)
    assert report.summary is not None and report.summary.v_min_bus == "b"
    # Level 1 already says why e has no voltage
    assert ("bus.e", OPEN_SWITCH_ISOLATED) in {(d.block, d.code) for d in report.level1}
