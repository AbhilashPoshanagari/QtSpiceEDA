"""G7: the v0.4 grid sample examples/ieee13_powerflow.qsp. Its feeder files are the
test fixtures, copied unchanged, and its power flow matches the OpenDSS result of the
original files (tests/fixtures/grid/ieee13/opendss_reference.json)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.grid.config import PowerFlowConfig
from core.netlist.model import ERROR
from core.project.io import load_project
from tests.core.grid.helpers import FIXTURES, reference

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SAMPLE = EXAMPLES / "ieee13_powerflow.qsp"
COPIES = {
    "grid/ieee13/IEEE13Nodeckt.dss": "ieee13/IEEE13Nodeckt.dss",
    "grid/ieee13/IEEELineCodes.DSS": "ieee13/IEEELineCodes.DSS",
    "grid/ieee13/IEEE13Node_BusXY.csv": "ieee13/IEEE13Node_BusXY.csv",
    "grid/IEEELineCodes.DSS": "IEEELineCodes.DSS",
    "grid/LICENSE": "LICENSE",
}
# IEEE 13 solves inside `compile`; the run solves it once more, which moves it by up to
# 1.3e-5 pu (see tests/ui/test_grid_controller.py). OpenDSS's own tolerance is 1e-4 pu.
PU_TOL = 1e-4


def sample_feeder() -> Path:
    loaded = load_project(SAMPLE)
    assert loaded.ok and loaded.warnings == [], loaded.errors
    project = loaded.project
    assert project is not None and project.grid is not None
    return (SAMPLE.parent / project.grid.dss_file).resolve()


def test_sample_holds_only_a_feeder_with_the_default_settings() -> None:
    project = load_project(SAMPLE).project
    assert project is not None and project.grid is not None
    assert project.grid.config == PowerFlowConfig()
    assert project.netlists == [] and project.diagrams == [] and project.results == {}
    assert sample_feeder() == (EXAMPLES / "grid" / "ieee13" / "IEEE13Nodeckt.dss").resolve()


@pytest.mark.parametrize(("copy", "original"), list(COPIES.items()))
def test_feeder_files_are_the_fixtures_unchanged(copy: str, original: str) -> None:
    assert (EXAMPLES / copy).read_bytes() == (FIXTURES / original).read_bytes()


def test_grid_folder_holds_only_the_listed_files() -> None:
    found = {p.relative_to(EXAMPLES).as_posix() for p in (EXAMPLES / "grid").rglob("*")}
    files = {f for f in found if (EXAMPLES / f).is_file()}
    assert files == set(COPIES) | {"grid/README.md"}


@pytest.mark.opendss
def test_sample_power_flow_matches_the_opendss_reference() -> None:
    from core.grid.runner import run_power_flow

    result, diagnostics = run_power_flow(sample_feeder(), PowerFlowConfig())
    assert result is not None and result.converged, diagnostics
    assert not [d for d in diagnostics if d.severity == ERROR]
    ref = reference("ieee13")
    assert result.node_names == ref["node_names"]
    assert np.max(np.abs(result.node_voltage_pu - np.array(ref["node_pu"]))) < PU_TOL
    assert abs(result.total_losses_kw - ref["losses_kw"]) < 0.05
    assert abs(result.total_power_kw + ref["total_power_kw"]) < 0.5  # source sign flipped
