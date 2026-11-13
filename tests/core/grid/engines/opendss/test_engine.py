from __future__ import annotations

import os

import numpy as np
import pytest

from core.grid.config import PowerFlowConfig
from core.grid.engines.base import GridEngine
from core.grid.engines.opendss.engine import OpenDSSEngine
from core.netlist.model import ERROR
from tests.core.grid.helpers import (
    BAD_REDIRECT,
    FIXTURES,
    IEEE13,
    IEEE34,
    UNKNOWN_LINECODE,
    reference,
    solve_directly,
)

pytestmark = pytest.mark.opendss


def _solve(path: os.PathLike[str] | str, config: PowerFlowConfig | None = None):
    engine = OpenDSSEngine()
    assert engine.load_native(path) == []
    return engine, engine.run(config or PowerFlowConfig())


def test_engine_follows_the_protocol() -> None:
    assert isinstance(OpenDSSEngine(), GridEngine)
    assert OpenDSSEngine.available()


def test_ieee13_equals_opendss_directly() -> None:
    engine, result = _solve(IEEE13)
    direct = solve_directly(IEEE13)
    assert result.converged and result.ok
    assert result.node_names == direct.node_names
    assert np.max(np.abs(result.node_voltage - direct.node_voltage)) <= 1e-9 * np.max(
        np.abs(direct.node_voltage)
    )
    assert np.max(np.abs(result.node_voltage_pu - direct.node_pu)) <= 1e-9
    assert result.total_losses_kw == pytest.approx(direct.losses_kw, rel=1e-9, abs=1e-9)
    assert result.total_losses_kvar == pytest.approx(direct.losses_kvar, rel=1e-9, abs=1e-9)
    # GridResult flips the OpenDSS sign: positive = supplied to the grid
    assert result.total_power_kw == pytest.approx(-direct.total_kw, rel=1e-9)
    assert result.total_power_kw > 0
    engine.close()


def test_ieee13_matches_the_reference_file() -> None:
    ref = reference("ieee13")
    _, result = _solve(IEEE13)
    assert result.node_names == ref["node_names"]
    # The reference is the compile-only solution; run() solves once more, which moves
    # the solution by a few 1e-5 pu at the default tolerance (spike S2).
    assert np.max(np.abs(result.node_voltage_pu - np.array(ref["node_pu"]))) < 5e-5
    assert result.total_losses_kw == pytest.approx(ref["losses_kw"], rel=1e-3)
    assert result.total_power_kw == pytest.approx(-ref["total_power_kw"], rel=1e-3)
    assert result.total_power_kvar == pytest.approx(-ref["total_power_kvar"], rel=1e-3)
    assert result.metadata["regulator_taps"] == ref["taps"]
    assert result.metadata["base_frequency"] == ref["base_frequency"]
    assert result.metadata["voltage_bases"] == ref["voltage_bases"]
    assert result.metadata["engine"] == ref["about"]["engine"].strip()


def test_ieee13_branches() -> None:
    _, result = _solve(IEEE13)
    direct = solve_directly(IEEE13)
    # 12 lines (switch 671692 included) and 5 transformers; capacitors are not branches
    assert len(result.branches) == 17
    assert "line.671692" in result.branches and "transformer.reg1" in result.branches
    line = result.branches["line.650632"]
    assert (line.bus1, line.bus2, line.phases) == ("rg60", "632", 3)
    assert line.current.shape == (3,) and np.iscomplexobj(line.current)
    powers = direct.branch_powers["line.650632"]
    n = len(powers) // 4  # two terminals, (kW, kvar) per conductor
    assert line.p_kw == pytest.approx(powers[0 : 2 * n : 2].sum(), rel=1e-9)
    assert line.q_kvar == pytest.approx(powers[1 : 2 * n : 2].sum(), rel=1e-9)
    assert line.losses_kw > 0
    assert line.normal_amps > 0
    assert line.loading_pct == pytest.approx(
        np.max(np.abs(line.current)) / line.normal_amps * 100.0
    )
    reg = result.branches["transformer.reg1"]
    assert reg.phases == 1 and reg.current.shape == (1,)
    total_branch_losses = sum(b.losses_kw for b in result.branches.values())
    assert total_branch_losses == pytest.approx(result.total_losses_kw, rel=1e-6)


def test_compile_does_not_change_the_working_directory() -> None:
    before = os.getcwd()
    _solve(IEEE13)
    assert os.getcwd() == before


def test_two_engines_do_not_affect_each_other() -> None:
    a, _ = _solve(IEEE13)
    b, before_b = _solve(IEEE34)
    assert b.context is not a.context
    b.context.Text.Command = "Load.s860.kW=500"
    after_b = b.run(PowerFlowConfig())
    assert not np.array_equal(after_b.node_voltage, before_b.node_voltage)
    again = a.run(PowerFlowConfig())
    # the same two runs in an engine that never ran next to b
    alone, _ = _solve(IEEE13)
    expected = alone.run(PowerFlowConfig())
    assert again.node_names == expected.node_names
    assert np.array_equal(again.node_voltage, expected.node_voltage)
    assert again.total_losses_kw == expected.total_losses_kw


def test_misspelled_redirect_is_an_error_diagnostic() -> None:
    engine = OpenDSSEngine()
    diagnostics = engine.load_native(BAD_REDIRECT)
    assert [d.severity for d in diagnostics] == [ERROR]
    assert "IEEELineCodez.dss" in diagnostics[0].message
    assert "not found" in diagnostics[0].message
    assert not engine.loaded
    result = engine.run(PowerFlowConfig())
    assert not result.converged
    assert result.diagnostics[0].message == "no circuit is loaded"


def test_unknown_linecode_is_an_error_diagnostic() -> None:
    engine = OpenDSSEngine()
    diagnostics = engine.load_native(UNKNOWN_LINECODE)
    assert [d.severity for d in diagnostics] == [ERROR]
    assert "no_such_code" in diagnostics[0].message
    assert not engine.loaded


def test_missing_file_is_an_error_diagnostic() -> None:
    diagnostics = OpenDSSEngine().load_native(FIXTURES / "no_such_feeder.dss")
    assert [d.severity for d in diagnostics] == [ERROR]
    assert "file not found" in diagnostics[0].message


def test_a_good_file_after_a_bad_one() -> None:
    engine = OpenDSSEngine()
    assert engine.load_native(BAD_REDIRECT)
    assert engine.load_native(IEEE13) == []
    assert engine.run(PowerFlowConfig()).converged


def test_not_converged_gives_an_error_diagnostic() -> None:
    _, result = _solve(IEEE34, PowerFlowConfig(max_iterations=2))
    assert not result.converged and not result.ok
    assert result.iterations == 2
    assert any(d.severity == ERROR and "did not converge" in d.message for d in result.diagnostics)


def test_control_iterations_exceeded_is_not_converged() -> None:
    # IEEE 34 needs 6 control iterations (spike S1)
    _, result = _solve(IEEE34, PowerFlowConfig(max_control_iterations=1))
    assert not result.converged
    assert any("Max Control Iterations" in d.message for d in result.diagnostics)


def test_ieee34_converges_and_matches_its_reference() -> None:
    ref = reference("ieee34")
    _, result = _solve(IEEE34)
    assert result.converged
    assert result.node_names == ref["node_names"]
    assert np.max(np.abs(result.node_voltage_pu - np.array(ref["node_pu"]))) < 1e-9
    assert result.total_losses_kw == pytest.approx(ref["losses_kw"], rel=1e-9)
    assert result.metadata["regulator_taps"] == ref["taps"]


def test_bad_settings_are_reported() -> None:
    engine = OpenDSSEngine()
    engine.load_native(IEEE13)
    result = engine.run(PowerFlowConfig(control_mode="time"))
    assert not result.converged
    assert "control mode" in result.diagnostics[0].message


def test_extract_without_a_circuit_is_an_error() -> None:
    model, diagnostics = OpenDSSEngine().extract_model()
    assert model is None
    assert diagnostics[0].severity == ERROR
    assert "no circuit" in diagnostics[0].message


# ---- G4: run() validates the circuit first


def test_run_refuses_a_model_with_a_validation_error() -> None:
    from core.grid import validate as v

    engine = OpenDSSEngine()
    assert engine.load_native(IEEE13) == []
    model, _ = engine.extract_model()
    assert model is not None
    model.loads["611"].kv = 4.16  # 1-phase wye load given the LL voltage
    assert not any(d.severity == ERROR for d in engine.load_model(model))
    result = engine.run(PowerFlowConfig())
    assert not result.converged and not result.ok
    assert result.iterations == 0 and result.node_names == []  # nothing was solved
    errors = [d for d in result.diagnostics if d.severity == ERROR]
    assert [(d.code, d.block) for d in errors] == [(v.ELEMENT_KV, "load.611")]
    engine.close()


def test_run_keeps_validation_warnings_and_solves() -> None:
    from core.grid import validate as v

    engine, result = _solve(IEEE34)
    assert result.converged and result.ok
    mismatch = [d for d in result.diagnostics if d.code == v.PHASE_NODE_MISMATCH]
    assert len(mismatch) == 6 and all(d.severity == "warning" for d in mismatch)
    # validated once per load: a second run gives the same warnings, not twice as many
    again = engine.run(PowerFlowConfig())
    assert [d for d in again.diagnostics if d.code == v.PHASE_NODE_MISMATCH] == mismatch
    engine.close()


def test_unsupported_branch_makes_reachability_a_warning() -> None:
    from core.grid import validate as v
    from core.grid.engines.opendss.extract import UNSUPPORTED_ELEMENT

    engine = OpenDSSEngine()
    assert engine.load_native(FIXTURES / "small" / "series_reactor.dss") == []
    model, found = engine.extract_model()
    assert model is not None
    unsupported = [(d.code, d.block) for d in found if d.code]  # also "not solved"
    assert unsupported == [(UNSUPPORTED_ELEMENT, "reactor.r1")]
    # Without the Reactor bus b looks unreachable: an error for a plain model ...
    assert [(d.code, d.severity) for d in v.validate(model) if d.block == "bus.b"] == [
        (v.UNREACHABLE_BUS, ERROR)
    ]
    # ... but run() knows the model is incomplete: a warning, and OpenDSS solves it.
    result = engine.run(PowerFlowConfig())
    assert result.converged and result.ok
    assert [(d.code, d.severity) for d in result.diagnostics if d.block == "bus.b"] == [
        (v.UNREACHABLE_BUS, "warning")
    ]
    assert result.voltage_pu("b.1") < result.voltage_pu("a.1") < 1.0
    engine.close()
