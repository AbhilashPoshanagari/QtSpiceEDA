"""G2: a GridModel extracted from IEEE 13/34/123 against what OpenDSS reports for the
same circuit (counts, linecode matrices, lengths) and against the reference files
(regulator taps)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from core.grid.engines.opendss.engine import OpenDSSEngine
from core.grid.model import LOAD_CVR, WYE, GridModel, Terminal
from core.netlist.model import ERROR, WARNING
from core.physical.component import NetworkDiagnostic
from tests.core.grid.helpers import (
    AFTER_COMPILE,
    FEEDER_FILES,
    FIXTURES,
    IEEE13,
    IEEE34,
    IEEE123,
    reference,
)

pytestmark = pytest.mark.opendss

FUSE_PV = FIXTURES / "small" / "fuse_pv.dss"
FEEDERS = FEEDER_FILES


def _extract(
    path: Path, solve: bool = True
) -> tuple[OpenDSSEngine, GridModel, list[NetworkDiagnostic]]:
    engine = OpenDSSEngine()
    assert engine.load_native(path) == []
    feeder = next((k for k, v in FEEDERS.items() if v == path), None)
    if feeder is not None:
        assert engine.context is not None
        for command in AFTER_COMPILE[feeder]:
            engine.context.Text.Command = command
    if solve:
        assert engine.run().converged
    model, diagnostics = engine.extract_model()
    assert model is not None
    return engine, model, diagnostics


def _opendss_counts(ctx: Any) -> dict[str, int]:
    """Element counts as OpenDSS reports them for the loaded circuit."""
    ckt = ctx.ActiveCircuit
    switches = 0
    idx = ckt.Lines.First
    while idx > 0:
        switches += bool(ckt.Lines.IsSwitch)
        idx = ckt.Lines.Next
    return {
        "buses": int(ckt.NumBuses),
        "sources": int(ckt.Vsources.Count),
        "lines": int(ckt.Lines.Count) - switches,
        "switches": switches,
        "transformers": int(ckt.Transformers.Count),
        "regulators": int(ckt.RegControls.Count),
        "loads": int(ckt.Loads.Count),
        "capacitors": int(ckt.Capacitors.Count),
        "pvsystems": int(ckt.PVSystems.Count),
    }


def test_ieee13_counts() -> None:
    _, model, diagnostics = _extract(IEEE13)
    assert diagnostics == []
    counts = model.counts()
    assert counts["sources"] == 1
    assert counts["lines"] == 11
    assert counts["switches"] == 1
    assert counts["transformers"] == 5
    assert counts["regulators"] == 3
    assert counts["loads"] == 15
    assert counts["capacitors"] == 2
    assert counts["pvsystems"] == 0


@pytest.mark.parametrize("feeder", list(FEEDERS))
def test_feeders_extract_without_errors_and_match_opendss_counts(feeder: str) -> None:
    engine, model, diagnostics = _extract(FEEDERS[feeder])
    assert not [d for d in diagnostics if d.severity == ERROR]
    assert diagnostics == []  # no unsupported classes in these feeders (S4)
    assert engine.context is not None
    expected = _opendss_counts(engine.context)
    counts = model.counts()
    assert {k: counts[k] for k in expected} == expected
    # Every circuit element is in the model (linecodes and buses are not elements).
    in_model = sum(counts[k] for k in expected if k != "buses")
    assert in_model == len(engine.context.ActiveCircuit.AllElementNames)


@pytest.mark.parametrize("feeder", list(FEEDERS))
def test_base_frequency_and_voltage_bases_come_from_the_file(feeder: str) -> None:
    _, model, _ = _extract(FEEDERS[feeder])
    ref = reference(feeder)
    assert model.base_frequency == ref["base_frequency"]
    assert model.voltage_bases == ref["voltage_bases"]


@pytest.mark.parametrize("feeder", list(FEEDERS))
def test_regulator_taps_are_the_solved_ones(feeder: str) -> None:
    _, model, _ = _extract(FEEDERS[feeder])
    taps = {
        name: model.transformers[reg.transformer].windings[reg.winding - 1].tap
        for name, reg in model.regulators.items()
    }
    assert taps == pytest.approx(reference(feeder)["taps"], abs=1e-12)


def test_ieee13_linecode_mtx601_is_in_ohm_per_km() -> None:
    engine, model, _ = _extract(IEEE13)
    assert engine.context is not None
    lc = engine.context.ActiveCircuit.LineCodes
    lc.Name = "mtx601"
    assert int(lc.Units) == 1  # miles in the .dss file
    km_per_mile = 1.609344
    code = model.linecodes["mtx601"]
    assert code.phases == 3
    assert code.r_matrix == pytest.approx([v / km_per_mile for v in lc.Rmatrix], rel=1e-12)
    assert code.x_matrix == pytest.approx([v / km_per_mile for v in lc.Xmatrix], rel=1e-12)
    assert code.c_matrix == pytest.approx([v / km_per_mile for v in lc.Cmatrix], rel=1e-12)
    assert len(code.r_matrix) == 9
    assert not code.generated


def test_ieee13_line_650632_is_609_6_m() -> None:
    _, model, _ = _extract(IEEE13)
    line = model.lines["650632"]
    assert line.length_m == pytest.approx(609.6, rel=1e-12)  # 2000 ft
    assert line.linecode == "mtx601"
    assert line.bus1 == Terminal("rg60", (1, 2, 3))
    assert line.bus2 == Terminal("632", (1, 2, 3))
    assert line.id == "line.650632"


def test_ieee13_switch_keeps_its_impedance() -> None:
    engine, model, _ = _extract(IEEE13)
    assert engine.context is not None
    ln = engine.context.ActiveCircuit.Lines
    ln.Name = "671692"
    sw = model.switches["671692"]
    assert sw.closed
    assert sw.r_ohm == pytest.approx(float(ln.R1) * float(ln.Length), rel=1e-12)
    assert sw.x_ohm == 0.0
    assert "671692" not in model.lines


def test_ieee13_only_used_linecodes_are_kept() -> None:
    _, model, _ = _extract(IEEE13)
    used = {line.linecode for line in model.lines.values()}
    assert set(model.linecodes) == used


def test_ieee13_regulators_name_a_transformer_winding() -> None:
    _, model, _ = _extract(IEEE13)
    for reg in model.regulators.values():
        assert reg.transformer in model.transformers
        assert 1 <= reg.winding <= len(model.transformers[reg.transformer].windings)
        assert reg.id.startswith("regcontrol.")


def test_ieee13_buses_have_coordinates_and_ln_bases() -> None:
    _, model, _ = _extract(IEEE13)
    assert all(b.x is not None and b.y is not None for b in model.buses.values())
    assert model.buses["671"].kv_base_ln == pytest.approx(4.16 / 3**0.5, rel=1e-6)


def test_ieee123_open_switch_buses_have_no_coordinates() -> None:
    _, model, _ = _extract(IEEE123)
    missing = {name for name, b in model.buses.items() if b.x is None}
    assert missing == {"300_open", "94_open"}


def test_ieee34_cvr_loads_are_kept() -> None:
    engine, model, _ = _extract(IEEE34)
    assert engine.context is not None
    ld = engine.context.ActiveCircuit.Loads
    models: Counter[int] = Counter()
    idx = ld.First
    while idx > 0:
        models[int(ld.Model)] += 1
        idx = ld.Next
    assert Counter(load.model for load in model.loads.values())[LOAD_CVR] == models[4]


def test_unsolved_circuit_warns_about_taps() -> None:
    _, _, diagnostics = _extract(IEEE34, solve=False)
    assert any("not solved" in d.message and d.severity == WARNING for d in diagnostics)


def test_fuse_is_reported_as_unsupported() -> None:
    _, model, diagnostics = _extract(FUSE_PV)
    fuse = [d for d in diagnostics if d.block == "fuse.f1"]
    assert len(fuse) == 1
    assert fuse[0].severity == WARNING
    assert "unsupported element fuse.f1" in fuse[0].message
    assert not [d for d in diagnostics if d.severity == ERROR]
    assert model.base_frequency == 50.0  # Set DefaultBaseFrequency=50 in the file


def test_line_without_linecode_gets_a_generated_one() -> None:
    _, model, _ = _extract(FUSE_PV)
    line = model.lines["feeder"]
    assert line.linecode == "_feeder"
    code = model.linecodes["_feeder"]
    assert code.generated
    assert line.length_m == pytest.approx(1500.0)  # length=1.5 units=km
    # r1=0.25 r0=0.5 ohm/km -> self term (2*r1 + r0)/3, mutual (r0 - r1)/3
    assert code.r_matrix[0] == pytest.approx((2 * 0.25 + 0.5) / 3)
    assert code.r_matrix[1] == pytest.approx((0.5 - 0.25) / 3)


def test_pvsystem_values_from_the_file() -> None:
    _, model, _ = _extract(FUSE_PV)
    pv = model.pvsystems["pv1"]
    assert pv.terminal.bus == "a"
    assert pv.phases == 3
    assert pv.conn == WYE
    assert pv.kv == pytest.approx(11.0)
    assert pv.kva == pytest.approx(300.0)
    assert pv.pmpp_kw == pytest.approx(250.0)
    assert pv.irradiance == pytest.approx(0.8)
    assert pv.pf == pytest.approx(1.0)
