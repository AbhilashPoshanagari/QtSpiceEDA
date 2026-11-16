"""D9: the v0.5 sample examples/ieee13_design.qsp + ieee13_design.qgrid. The design is
the IEEE 13 fixture opened as a design (openDssAsDesign, made by
scratch/phase5/make_ieee13_design.py), with its diagram; run, it matches the OpenDSS
result of the original file (tests/fixtures/grid/ieee13/opendss_reference.json)."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import jsonschema
import pytest

from core.grid import design_io
from core.grid.config import PowerFlowConfig
from core.grid.design_io import GridDesign
from core.grid.diagram.layout import build_diagram
from core.grid.diagram.model import check_consistency
from core.grid.validate import validate
from core.netlist.model import ERROR
from core.project.io import load_project
from tests.core.grid.helpers import IEEE13, reference
from tests.core.project.test_io import SCHEMA

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SAMPLE = EXAMPLES / "ieee13_design.qsp"
QGRID = EXAMPLES / "ieee13_design.qgrid"
# IEEE 13 solves inside `compile`; a re-solve moves it by up to 1.4e-5 pu (S4 b, see
# the D4 proof test). OpenDSS's own tolerance is 1e-4 pu.
PU_TOL = 1e-4

opendss = pytest.mark.opendss


def sample_design() -> GridDesign:
    loaded = load_project(SAMPLE)
    assert loaded.ok and loaded.warnings == [], loaded.errors
    project = loaded.project
    assert project is not None and len(project.grid_designs) == 1
    entry = project.grid_designs[0]
    assert entry.active
    design, diags = design_io.load((SAMPLE.parent / entry.path).resolve())
    assert design is not None and diags == [], [d.message for d in diags]
    return design


def test_sample_is_a_v5_project_with_one_design_and_default_settings() -> None:
    import json

    data = json.loads(SAMPLE.read_text("utf-8"))
    assert data["version"] == 5
    jsonschema.validate(data, SCHEMA)
    project = load_project(SAMPLE).project
    assert project is not None and project.grid is None
    assert project.netlists == [] and project.diagrams == [] and project.results == {}
    entry = project.grid_designs[0]
    assert entry.path == "ieee13_design.qgrid" and entry.config == PowerFlowConfig()


def test_sample_design_is_complete_consistent_and_saves_to_the_same_bytes(tmp_path: Path) -> None:
    design = sample_design()
    g = design.grid
    assert g.base_frequency == 60.0 and not g.derive_voltage_bases  # the file's bases
    assert check_consistency(g, design.diagram) == []
    assert [d for d in validate(g) if d.severity == ERROR] == []
    assert set(design.diagram.bus_glyphs) == set(g.buses)
    copy_path = tmp_path / QGRID.name
    assert design_io.save(copy_path, design) == []
    assert copy_path.read_bytes() == QGRID.read_bytes()


def test_sample_diagram_comes_from_the_bus_coordinates() -> None:
    design = sample_design()
    assert design.diagram == build_diagram(copy.deepcopy(design.grid))


@opendss
def test_sample_design_is_the_fixture_opened_as_a_design() -> None:
    from core.grid.runner import load_grid

    model, diags = load_grid(IEEE13)
    assert model is not None and not [d for d in diags if d.severity == ERROR]
    assert sample_design().grid == model


@opendss
def test_sample_design_run_matches_the_opendss_reference(tmp_path: Path) -> None:
    """The design's own run (writer.py script, its PowerFlowConfig) against the
    reference within OpenDSS's tolerance, and - solved the same way as the original
    file (G3/D4: Tolerance 1e-10) - within 1e-6 pu on every node and 0.1 % on
    losses."""
    from core.grid.engines.opendss.engine import OpenDSSEngine
    from core.grid.runner import run_model
    from tests.core.grid.engines.opendss.test_writer import (
        CONFIG,
        _assert_same_solution,
        _original,
        _pu_by_node,
    )

    design = sample_design()
    ref = reference("ieee13")
    ref_pu = dict(zip(ref["node_names"], ref["node_pu"], strict=True))

    script = tmp_path / "grid" / "ieee13nodeckt" / "ieee13nodeckt.dss"
    result, _, diags, text, saved = run_model(design.grid, PowerFlowConfig(), script)
    assert result is not None and result.converged, [d.message for d in diags]
    assert not [d for d in diags if d.severity == ERROR]
    assert saved == str(script) and script.read_text("utf-8") == text
    got = _pu_by_node(result)
    assert sorted(got) == sorted(ref_pu)
    assert max(abs(got[n] - ref_pu[n]) for n in ref_pu) < PU_TOL
    assert math.isclose(result.total_losses_kw, ref["losses_kw"], rel_tol=1e-3)

    original, a = _original("ieee13")
    engine = OpenDSSEngine()
    try:
        assert engine.load_model(design.grid) == []
        b = engine.run(CONFIG)
        assert b.converged and b.ok, b.diagnostics
        _assert_same_solution(a, b)  # 1e-6 pu on every node, 0.1 % losses, same taps
    finally:
        engine.close()
        original.close()
