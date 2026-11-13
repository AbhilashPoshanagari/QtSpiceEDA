"""G3: GridModel -> DSS script. The round trip (.dss -> GridModel -> script -> solve)
of IEEE 13, 34 and 123 against the OpenDSS solution of the original files, the same
with an open switch, a GridModel built by hand, and the script text itself.

Both solves of a round trip use Solution.Tolerance 1e-10: at the default 1e-4 a
second solve alone moves a feeder by up to 5e-6 pu (spike S2), more than the 1e-6 pu
the round trip must meet.
"""

from __future__ import annotations

import locale
import math
from collections.abc import Iterator

import numpy as np
import pytest

from core.grid.config import CONTROL_STATIC, PowerFlowConfig
from core.grid.engines.opendss.engine import OpenDSSEngine
from core.grid.engines.opendss.writer import main, num, script_commands, write_dss
from core.grid.model import (
    LOAD_PQ,
    WYE,
    Bus,
    GridModel,
    Line,
    LineCode,
    Load,
    PVSystem,
    Source,
    Switch,
    Terminal,
)
from core.grid.result import GridResult
from core.netlist.model import ERROR
from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES, IEEE13, reference

opendss = pytest.mark.opendss

PU_TOL = 1e-6
LOSS_REL_TOL = 1e-3  # 0.1 %
CONFIG = PowerFlowConfig(control_mode=CONTROL_STATIC, tolerance=1e-10, max_control_iterations=30)


# ---- helpers


def _original(feeder: str, *commands: str) -> tuple[OpenDSSEngine, GridResult]:
    engine = OpenDSSEngine()
    assert engine.load_native(FEEDER_FILES[feeder]) == []
    assert engine.context is not None
    for command in (*AFTER_COMPILE[feeder], *commands):
        engine.context.Text.Command = command
    result = engine.run(CONFIG)
    assert result.converged and result.ok, result.diagnostics
    return engine, result


def _round_trip(original: OpenDSSEngine) -> tuple[OpenDSSEngine, GridResult, GridModel]:
    model, _ = original.extract_model()
    assert model is not None
    engine = OpenDSSEngine()
    assert engine.load_model(model) == []
    result = engine.run(CONFIG)
    assert result.converged and result.ok, result.diagnostics
    return engine, result, model


def _pu_by_node(result: GridResult) -> dict[str, float]:
    return dict(zip(result.node_names, (float(v) for v in result.node_voltage_pu), strict=True))


def _assert_same_solution(a: GridResult, b: GridResult) -> None:
    pa, pb = _pu_by_node(a), _pu_by_node(b)
    assert sorted(pa) == sorted(pb)
    worst = max(pa, key=lambda n: abs(np.nan_to_num(pa[n]) - np.nan_to_num(pb[n])))
    diff = abs(np.nan_to_num(pa[worst]) - np.nan_to_num(pb[worst]))
    assert diff <= PU_TOL, f"{worst}: {pa[worst]} vs {pb[worst]}"
    assert math.isclose(b.total_losses_kw, a.total_losses_kw, rel_tol=LOSS_REL_TOL)
    assert b.metadata["regulator_taps"] == a.metadata["regulator_taps"]


# ---- round trips


@opendss
@pytest.mark.parametrize("feeder", ["ieee13", "ieee34", "ieee123"])
def test_round_trip_matches_the_original(feeder: str) -> None:
    original, a = _original(feeder)
    # The original solve itself is the reference solution of the file.
    ref = reference(feeder)
    ref_pu = dict(zip(ref["node_names"], ref["node_pu"], strict=True))
    pa = _pu_by_node(a)
    assert max(abs(pa[n] - ref_pu[n]) for n in ref_pu) < 1e-4  # S2: default-tolerance solve
    engine, b, _ = _round_trip(original)
    _assert_same_solution(a, b)
    # Taps are already where the regulators want them: no tap moves.
    assert b.control_iterations == 1


@opendss
def test_round_trip_with_an_open_switch() -> None:
    original, a = _original("ieee13", "Open line.671692 1")
    engine, b, model = _round_trip(original)
    assert model.switches["671692"].closed is False
    _assert_same_solution(a, b)
    pb = _pu_by_node(b)
    assert all(pb[f"675.{p}"] < 1e-6 for p in (1, 2, 3))  # 692/675 are cut off
    back, _ = engine.extract_model()
    assert back is not None and back.switches["671692"].closed is False


@opendss
def test_round_trip_keeps_the_model() -> None:
    """Reading the written circuit back gives the same model (taps, switches, loads)."""
    original, _ = _original("ieee123")
    engine, _, model = _round_trip(original)
    back, _ = engine.extract_model()
    assert back is not None
    assert back.counts() == model.counts()
    assert back.transformers["reg2a"].ppm_antifloat == 0.0
    for name, t in model.transformers.items():
        assert [w.tap for w in back.transformers[name].windings] == [w.tap for w in t.windings]
    assert back.switches == model.switches
    assert back.loads == model.loads
    for name, bus in model.buses.items():
        assert (back.buses[name].x, back.buses[name].y) == (bus.x, bus.y)


# ---- a GridModel built by hand


def _hand_model() -> GridModel:
    """src -- line -- a (load) -- switch -- b (PV), 11 kV, 50 Hz."""
    r1, x1, c1 = 0.25, 0.35, 10.0  # ohm/km, nF/km; equal sequences -> diagonal matrices
    diag = lambda v: (v, 0.0, 0.0, 0.0, v, 0.0, 0.0, 0.0, v)  # noqa: E731
    return GridModel(
        name="Hand",
        base_frequency=50.0,
        voltage_bases=[11.0],
        buses={
            "src": Bus("src", 0.0, 0.0, 0.0),
            "a": Bus("a", 0.0, 1500.0, 0.0),
            "b": Bus("b", 0.0, 1500.0, 10.0),
        },
        sources={
            "source": Source(
                name="source",
                bus1=Terminal("src"),
                bus2=Terminal("src", (0, 0, 0)),
                phases=3,
                kv=11.0,
                pu=1.0,
                angle_deg=0.0,
                r1_ohm=0.01,
                x1_ohm=0.1,
                r0_ohm=0.01,
                x0_ohm=0.1,
            )
        },
        linecodes={
            "ug": LineCode("ug", 3, diag(r1), diag(x1), diag(c1), 400.0, 600.0),
        },
        lines={
            "feeder": Line(
                "feeder", Terminal("src", (1, 2, 3)), Terminal("a", (1, 2, 3)), 3, 1500.0, "ug"
            ),
        },
        switches={
            "s1": Switch("s1", Terminal("a"), Terminal("b"), 3, closed=True, r_ohm=1e-4),
        },
        loads={
            "ld1": Load("ld1", Terminal("a", (1, 2, 3)), 3, WYE, 11.0, 500.0, 200.0, LOAD_PQ),
        },
        pvsystems={
            "pv1": PVSystem("pv1", Terminal("b", (1, 2, 3)), 3, WYE, 11.0, 300.0, 250.0, 0.8, 1.0),
        },
    )


@opendss
def test_hand_built_model_writes_solves_and_converges() -> None:
    engine = OpenDSSEngine()
    assert engine.load_model(_hand_model()) == []
    result = engine.run(PowerFlowConfig())
    assert result.converged and result.ok, result.diagnostics
    assert result.metadata["base_frequency"] == 50.0
    pu = _pu_by_node(result)
    assert sorted({n.split(".")[0] for n in pu}) == ["a", "b", "src"]
    assert all(0.95 < pu[f"b.{p}"] < 1.05 for p in (1, 2, 3))
    # Source = load - PV (250 kW * 0.8 at unity pf) + losses.
    assert result.total_power_kw == pytest.approx(500.0 - 200.0 + result.total_losses_kw, abs=1e-3)
    back, _ = engine.extract_model()
    assert back is not None
    assert back.counts()["pvsystems"] == 1 and back.counts()["switches"] == 1
    assert back.lines["feeder"].length_m == pytest.approx(1500.0)
    assert (back.buses["b"].x, back.buses["b"].y) == (1500.0, 10.0)


@opendss
def test_load_model_reports_writer_errors() -> None:
    model = _hand_model()
    model.sources.clear()
    engine = OpenDSSEngine()
    diagnostics = engine.load_model(model)
    assert [d.severity for d in diagnostics] == [ERROR]
    assert "no source" in diagnostics[0].message
    assert not engine.loaded


@opendss
def test_load_model_reports_engine_errors_with_the_script_line() -> None:
    model = _hand_model()
    model.loads["ld1"].phases = 0  # the writer writes it; OpenDSS refuses it
    engine = OpenDSSEngine()
    diagnostics = engine.load_model(model)
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert (d.block, d.severity) == ("load.ld1", ERROR)
    assert d.message.startswith("script line ") and "cannot be zero" in d.message
    assert not engine.loaded


# ---- the script text (no OpenDSS needed)


def test_numbers_are_exact_and_locale_free() -> None:
    assert num(1.5) == "1.5"
    assert num(2000.0) == "2000"
    assert num(0.1 + 0.2) == "0.30000000000000004"
    assert num(1e-7) == "1e-07"
    assert num(-0.0) == "0"
    assert float(num(6.5625e-05 * 3.28084)) == 6.5625e-05 * 3.28084


@pytest.fixture
def german_locale() -> Iterator[None]:
    old = locale.setlocale(locale.LC_NUMERIC)
    for name in ("de_DE.UTF-8", "de_DE", "German_Germany.1252"):
        try:
            locale.setlocale(locale.LC_NUMERIC, name)
            break
        except locale.Error:
            continue
    else:
        pytest.skip("no German locale")
    yield
    locale.setlocale(locale.LC_NUMERIC, old)


def test_script_has_no_decimal_commas(german_locale: None) -> None:
    text, _ = write_dss(_hand_model())
    assert "0,8" not in text and "irradiance=0.8" in text


def test_script_order_and_properties() -> None:
    text, diagnostics = write_dss(_hand_model())
    assert diagnostics == []
    lines = [c for _, c in script_commands(text)]
    assert lines[0] == "Clear"
    assert lines[1] == "Set DefaultBaseFrequency=50"
    assert lines[2].startswith("New Circuit.hand ")
    assert "mvasc" not in text.lower()
    circuit = lines[2]
    for prop in ("r1=0.01", "x1=0.1", "r0=0.01", "x0=0.1", "basefreq=50"):
        assert prop in circuit.split()
    order = [c.split()[0] + " " + c.split()[1].split(".")[0] for c in lines if c.startswith("New")]
    assert order == ["New Circuit", "New Linecode", "New Line", "New Line", "New Load",
                     "New PVSystem"]  # fmt: skip
    calcv = lines.index("CalcVoltageBases")
    assert lines[calcv - 1] == "Set VoltageBases=[11]"
    assert lines[calcv + 1 :] == [
        "SetBusXY bus=src x=0 y=0",
        "SetBusXY bus=a x=1500 y=0",
        "SetBusXY bus=b x=1500 y=10",
    ]


def test_units_come_after_the_impedances() -> None:
    text, _ = write_dss(_hand_model())
    code = text[text.index("New Linecode.ug") :].split("\n")[:5]
    assert code[-1].endswith("units=km")
    line = next(c for _, c in script_commands(text) if c.startswith("New Line.feeder"))
    assert line.split()[-2:] == ["length=1500", "units=m"]


def test_switch_impedance_follows_switch_yes() -> None:
    model = _hand_model()
    model.switches["s1"].closed = False
    text, _ = write_dss(model)
    switch = next(c for _, c in script_commands(text) if c.startswith("New Line.s1")).split()
    i = switch.index("switch=yes")
    assert switch[i + 1 :] == ["r1=0.0001", "x1=0", "r0=0.0001", "x0=0", "c1=0", "c0=0",
                               "length=1", "units=none"]  # fmt: skip
    lines = [c for _, c in script_commands(text)]
    assert lines.index("Open line.s1 1") > lines.index("CalcVoltageBases")


def test_names_are_lower_case() -> None:
    model = _hand_model()
    model.loads["ld1"].name = "LD1"
    model.loads["ld1"].terminal = Terminal("A", (1, 2, 3))
    text, _ = write_dss(model)
    assert "New Load.ld1 phases=3 bus1=a.1.2.3 " in text


def test_bad_elements_are_diagnostics_not_exceptions() -> None:
    model = _hand_model()
    model.linecodes["ug"].r_matrix = (1.0, 2.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    model.loads["ld1"].kw = float("nan")
    text, diagnostics = write_dss(model)
    by_location = {d.block: d for d in diagnostics}
    assert "not symmetric" in by_location["linecode.ug"].message
    assert "nan" in by_location["load.ld1"].message
    assert "New Linecode.ug" not in text and "New Load.ld1" not in text
    assert all(d.severity == ERROR for d in diagnostics)


def test_names_that_break_the_script_are_diagnostics() -> None:
    model = _hand_model()
    model.loads["ld1"].name = "my load"
    text, diagnostics = write_dss(model)
    assert [d.block for d in diagnostics] == ["load.my load"]
    assert "cannot be written" in diagnostics[0].message
    assert "New Load." not in text


def test_no_source_is_a_diagnostic() -> None:
    model = _hand_model()
    model.sources.clear()
    text, diagnostics = write_dss(model)
    assert [d.block for d in diagnostics] == ["circuit"]
    assert "New Circuit" not in text


# ---- CLI


@opendss
def test_cli_writes_a_readable_ieee13_script(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "ieee13_written.dss"
    assert main([str(IEEE13), str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "New Circuit.ieee13nodeckt " in text
    assert "New Line.671692 phases=3 bus1=671 bus2=692 switch=yes" in text
    assert "tap=1.05625" in text  # reg1, solved
    engine = OpenDSSEngine()
    assert engine.load_native(out) == []
    assert engine.run(CONFIG).converged
