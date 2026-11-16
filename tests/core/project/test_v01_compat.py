"""v0.1 projects still open in v0.2 (release check).

tests/fixtures/v01/ holds the three sample projects exactly as released in v0.1.0
(``git show v0.1.0:examples/<name>.qsp``). They are format version 1: no simulation
settings, schematic, symbols or plots. They must open without errors or warnings, get
the v0.2 defaults, and survive a save (now format version 3) and reopen unchanged."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.netlist.parser import parse
from core.netlist.validate import validate
from core.project.io import BASE_VERSION, Project, load_project, save_project
from core.sim.backend import DEFAULT_COMPAT_MODE

V01 = Path(__file__).resolve().parents[2] / "fixtures" / "v01"
NAMES = ["rc_filter.qsp", "diode_rectifier.qsp", "first_order_feedback.qsp"]


def open_v01(path: Path) -> Project:
    loaded = load_project(path)
    assert loaded.ok, loaded.errors
    assert loaded.warnings == []
    assert loaded.project is not None
    return loaded.project


def content(project: Project) -> dict[str, object]:
    """Everything v0.1 stored, without the format version."""
    data = project.to_dict()
    return {k: data[k] for k in ("netlists", "diagrams", "block_sources", "analysis")}


@pytest.mark.parametrize("name", NAMES)
def test_v01_fixture_is_format_version_1(name: str) -> None:
    data = json.loads((V01 / name).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert not {"simulation", "schematic", "symbols", "plots", "plot_defs"} & set(data)


@pytest.mark.parametrize("name", NAMES)
def test_v01_project_opens_with_v02_defaults(name: str) -> None:
    project = open_v01(V01 / name)
    assert project.simulation.compat_mode == DEFAULT_COMPAT_MODE
    assert project.schematic is None
    assert project.symbols == []
    assert project.plots == {}
    assert project.plot_defs is None
    assert project.results == {}
    for entry in (n for n in project.netlists if n.text.strip()):  # diagram-only: empty
        result = parse(entry.text)
        assert result.diagnostics == []
        assert validate(result.circuit) == []


@pytest.mark.parametrize("name", NAMES)
def test_v01_project_saves_as_v3_and_reopens_unchanged(name: str, tmp_path: Path) -> None:
    copy = tmp_path / name
    shutil.copyfile(V01 / name, copy)
    before = open_v01(copy)
    save_project(copy, before)
    assert json.loads(copy.read_text(encoding="utf-8"))["version"] == BASE_VERSION
    after = open_v01(copy)
    assert content(after) == content(before)
    assert not (tmp_path / f"{copy.stem}_results").exists()
