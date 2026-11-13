from __future__ import annotations

import pickle

import numpy as np
import pytest

from core.grid.result import BranchResult, GridResult
from core.netlist.model import ERROR, WARNING
from core.physical.component import NetworkDiagnostic


def _result() -> GridResult:
    branch = BranchResult(
        element="line.a",
        bus1="b1",
        bus2="b2",
        phases=1,
        current=np.array([3 + 4j]),
        p_kw=1.0,
        q_kvar=0.5,
        losses_kw=0.01,
        losses_kvar=0.02,
        normal_amps=10.0,
        loading_pct=50.0,
    )
    return GridResult(
        converged=True,
        node_names=["b1.1", "b2.1"],
        node_voltage=np.array([100 + 0j, 0 + 99j]),
        node_voltage_pu=np.array([1.0, 0.99]),
        branches={"line.a": branch},
        diagnostics=[NetworkDiagnostic("bus.b2", WARNING, "w")],
    )


def test_lookup_by_node_name() -> None:
    result = _result()
    assert result.voltage("B2.1") == 99j
    assert result.voltage_pu("b1.1") == 1.0
    with pytest.raises(KeyError):
        result.voltage("b3.1")


def test_ok_needs_convergence_and_no_errors() -> None:
    result = _result()
    assert result.ok  # a warning is fine
    result.diagnostics.append(NetworkDiagnostic("circuit", ERROR, "e"))
    assert not result.ok


def test_failed_result() -> None:
    failed = GridResult.failed([NetworkDiagnostic("circuit", ERROR, "no circuit")])
    assert not failed.converged and not failed.ok
    assert failed.node_names == [] and failed.node_voltage.size == 0


def test_pickles_through_a_pipe() -> None:
    result = _result()
    back = pickle.loads(pickle.dumps(result))
    assert back.node_names == result.node_names
    assert np.array_equal(back.node_voltage, result.node_voltage)
    assert back.branches["line.a"].current[0] == 3 + 4j
    assert back.diagnostics == result.diagnostics
