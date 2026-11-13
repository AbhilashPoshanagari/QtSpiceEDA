"""M5: user physical component code in the .qsp file (optional ``physical_sources``)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.project.io import BlockSource, Project, load_project, save_project
from tests.core.project.test_io import SCHEMA, sample_project

COMPONENT = "@physical_component\nclass P(PhysicalComponent):\n    pass\n"


def test_component_code_round_trip(tmp_path: Path) -> None:
    project = sample_project()
    project.physical_sources = [BlockSource("component1", COMPONENT)]
    path = tmp_path / "p.qsp"
    save_project(path, project)
    data = json.loads(path.read_text("utf-8"))
    assert data["physical_sources"] == [{"document": "component1", "code": COMPONENT}]
    jsonschema.validate(data, SCHEMA)
    loaded = load_project(path)
    assert loaded.ok, loaded.errors
    assert loaded.project.physical_sources == project.physical_sources  # type: ignore[union-attr]


def test_no_component_code_leaves_the_field_out(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, sample_project())
    data = json.loads(path.read_text("utf-8"))
    assert "physical_sources" not in data
    assert load_project(path).project.physical_sources == []  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([{"document": "c1"}], "missing field physical_sources[0].code"),
        ([{"document": "c1", "code": 1}], "physical_sources[0].code has the wrong type"),
        (
            [{"document": "c1", "code": ""}, {"document": "c1", "code": ""}],
            "physical_sources need distinct",
        ),
        ("x", "'physical_sources' must be a list"),
    ],
)
def test_bad_component_entries(tmp_path: Path, value: object, message: str) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, sample_project())
    data = json.loads(path.read_text("utf-8"))
    data["physical_sources"] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_project(path)
    assert not loaded.ok and any(message in e for e in loaded.errors), loaded.errors


def test_version_1_file_may_not_have_component_code(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, sample_project())
    data = json.loads(path.read_text("utf-8"))
    data["version"] = 1
    del data["simulation"]
    data["physical_sources"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "unknown field 'physical_sources'" in load_project(path).errors


def test_empty_project_default() -> None:
    assert Project().physical_sources == []
