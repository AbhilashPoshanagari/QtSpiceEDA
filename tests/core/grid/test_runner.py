from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import numpy as np
import pytest

from core.grid.config import PowerFlowConfig
from core.grid.runner import CANCELLED, OK, TIMEOUT, GridRunner
from core.netlist.model import ERROR
from tests.core.grid.helpers import (
    BAD_REDIRECT,
    IEEE13,
    IEEE123,
    UNKNOWN_LINECODE,
    solve_directly,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.opendss
def test_ieee13_in_the_child_equals_opendss_directly() -> None:
    outcome = GridRunner(IEEE13, PowerFlowConfig()).run()
    assert outcome.status == OK and outcome.ok, outcome.message
    result = outcome.result
    assert result is not None and result.converged
    direct = solve_directly(IEEE13)  # in this (the test) process
    assert result.node_names == direct.node_names
    scale = np.max(np.abs(direct.node_voltage))
    assert np.max(np.abs(result.node_voltage - direct.node_voltage)) <= 1e-9 * scale
    assert np.max(np.abs(result.node_voltage_pu - direct.node_pu)) <= 1e-9
    assert abs(result.total_losses_kw - direct.losses_kw) <= 1e-9 * abs(direct.losses_kw)
    assert abs(result.total_losses_kvar - direct.losses_kvar) <= 1e-9 * abs(direct.losses_kvar)
    assert outcome.diagnostics == []


@pytest.mark.opendss
@pytest.mark.parametrize(
    ("path", "text"),
    [(BAD_REDIRECT, "IEEELineCodez.dss"), (UNKNOWN_LINECODE, "no_such_code")],
)
def test_bad_files_come_back_as_diagnostics(path: Path, text: str) -> None:
    outcome = GridRunner(path).run()
    assert outcome.status == OK  # the child finished; the file had an error
    assert not outcome.ok and outcome.result is None
    assert [d.severity for d in outcome.diagnostics] == [ERROR]
    assert text in outcome.diagnostics[0].message


@pytest.mark.opendss
def test_not_converged_comes_back_as_a_result() -> None:
    outcome = GridRunner(IEEE13, PowerFlowConfig(max_iterations=1, tolerance=1e-12)).run()
    assert outcome.status == OK and not outcome.ok
    assert outcome.result is not None and not outcome.result.converged
    assert any("did not converge" in d.message for d in outcome.diagnostics)


@pytest.mark.opendss
def test_stop_during_a_run() -> None:
    runner = GridRunner(IEEE123, PowerFlowConfig(max_control_iterations=30))
    runner.start()
    threading.Timer(0.3, runner.cancel).start()
    outcome = runner.wait()
    assert outcome.status == CANCELLED
    # a new runner works afterwards
    assert GridRunner(IEEE13).run().ok


def test_timeout() -> None:
    outcome = GridRunner(IEEE13, timeout=0.05).run()
    assert outcome.status == TIMEOUT
    assert "timeout" in outcome.message


def test_without_dss_python() -> None:
    """dss-python 'uninstalled': the import fails, nothing raises, the app imports."""
    code = textwrap.dedent(
        f"""
        import sys
        sys.modules["dss"] = None  # makes `import dss` raise ImportError
        from core.grid.engines.opendss.engine import INSTALL_HINT, OpenDSSEngine
        from core.grid.config import PowerFlowConfig
        from core.grid.runner import run_power_flow
        assert not OpenDSSEngine.available()
        engine = OpenDSSEngine()
        diags = engine.load_native({str(IEEE13)!r})
        assert [d.message for d in diags] == [INSTALL_HINT], diags
        assert not engine.run(PowerFlowConfig()).converged
        result, diags = run_power_flow({str(IEEE13)!r}, PowerFlowConfig())
        assert result is None and diags[0].message == INSTALL_HINT
        import app.__main__, bridge.simulator, bridge.diagram_controller  # noqa
        print("ok")
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")


@pytest.mark.opendss
def test_run_sends_back_the_solved_model() -> None:
    """G6: the UI needs the model (lengths, coordinates, switches) with the result."""
    outcome = GridRunner(IEEE13).run()
    assert outcome.ok and outcome.model is not None
    model = outcome.model
    assert len(model.buses) == 16 and all(b.x is not None for b in model.buses.values())
    taps = outcome.result.metadata["regulator_taps"]
    for name, reg in model.regulators.items():
        wdg = model.transformers[reg.transformer].windings[reg.winding - 1]
        assert wdg.tap == pytest.approx(taps[name])  # the solved taps


@pytest.mark.opendss
def test_load_only_checks_and_reads_back_without_solving() -> None:
    runner = GridRunner(IEEE123, solve=False)
    outcome = runner.run()
    assert outcome.status == OK and outcome.result is None and not outcome.ok
    assert outcome.model is not None and len(outcome.model.buses) > 100
    assert not any(d.severity == ERROR for d in outcome.diagnostics)
    assert not any("not solved" in d.message for d in outcome.diagnostics)


@pytest.mark.opendss
def test_load_only_reports_errors() -> None:
    outcome = GridRunner(BAD_REDIRECT, solve=False).run()
    assert outcome.status == OK and outcome.model is None
    assert [d.severity for d in outcome.diagnostics] == [ERROR]
    assert "IEEELineCodez.dss" in outcome.diagnostics[0].message


# ---- GridModel runs (v0.5 designs, rule 23)


@pytest.mark.opendss
def test_model_run_writes_saves_and_matches_the_file_run(tmp_path: Path) -> None:
    config = PowerFlowConfig(tolerance=1e-10, max_control_iterations=30)
    model = GridRunner(IEEE13, solve=False).run().model
    assert model is not None
    script = tmp_path / "results" / "grid" / "ieee13" / "ieee13.dss"
    runner = GridRunner(model, config, script_path=script)
    assert runner.from_model
    outcome = runner.run()
    assert outcome.ok, outcome.diagnostics
    assert script.is_file() and outcome.script_path == str(script.resolve())
    assert script.read_text(encoding="utf-8") == outcome.script.replace("\r\n", "\n")
    assert outcome.model is not None  # read back after the solve
    file_run = GridRunner(IEEE13, config).run()
    a = dict(zip(file_run.result.node_names, file_run.result.node_voltage_pu, strict=True))
    b = dict(zip(outcome.result.node_names, outcome.result.node_voltage_pu, strict=True))
    assert sorted(a) == sorted(b)
    assert max(abs(float(a[n]) - float(b[n])) for n in a) <= 1e-6
    assert outcome.result.total_losses_kw == pytest.approx(
        file_run.result.total_losses_kw, rel=1e-3
    )


def test_draft_model_comes_back_as_diagnostics(tmp_path: Path) -> None:
    """Runs with and without dss-python: the writer refuses a draft before OpenDSS."""
    from core.grid.model import Bus, GridModel, Load, Source, Terminal

    model = GridModel("d", 60.0, derive_voltage_bases=True)
    model.buses["a"] = Bus("a", 0.0)
    model.sources["s"] = Source("s", Terminal("a"), Terminal("a", (0, 0, 0)), 3, 12.47, 1.0,
                                0.0, 0.1, 1.0, 0.1, 1.0)  # fmt: skip
    model.loads["l"] = Load("l", Terminal("a"), 3, "wye", 12.47, None, 1.0, "pq")
    outcome = GridRunner(model, script_path=tmp_path / "d.dss").run()
    assert outcome.status == OK and outcome.result is None and outcome.script == ""
    assert [(d.block, d.message) for d in outcome.diagnostics] == [("load.l", "kW not set")]
    assert not (tmp_path / "d.dss").exists()


def test_model_run_always_solves() -> None:
    from core.grid.model import GridModel

    with pytest.raises(ValueError):
        GridRunner(GridModel("d", 60.0), solve=False)
