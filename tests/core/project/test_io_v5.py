"""D9: .qsp format version 5 - the grid_designs list (.qgrid path, active design,
per-design PowerFlowConfig solver settings and limits). A project without designs is
still written as version 4; versions 1-4 open unchanged."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from core.grid.config import CONTROL_OFF, PowerFlowConfig
from core.project.io import (
    BASE_VERSION,
    FORMAT_VERSION,
    GridDesignSettings,
    GridSettings,
    Project,
    ProjectError,
    load_project,
    save_project,
)
from tests.core.project.test_io import SCHEMA, write

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"
V01 = ROOT / "tests" / "fixtures" / "v01"
SAMPLES = {
    1: V01 / "rc_filter.qsp",
    2: EXAMPLES / "rlc_bandpass.qsp",
    3: EXAMPLES / "dc_motor_speed.qsp",
    4: EXAMPLES / "ieee13_powerflow.qsp",
}

CONFIG = PowerFlowConfig(
    control_mode=CONTROL_OFF,
    max_iterations=50,
    max_control_iterations=None,
    tolerance=1e-6,
    voltage_min_pu=0.92,
    voltage_max_pu=1.08,
    line_loading_limit_pct=80.0,
    transformer_loading_limit_pct=120.0,
    warn_fraction=0.8,
)


def design_project() -> Project:
    return Project(
        grid_designs=[
            GridDesignSettings("designs/feeder_a.qgrid", False, CONFIG),
            GridDesignSettings("designs/feeder_b.qgrid", True),
        ]
    )


def design_data(**entry: Any) -> dict[str, Any]:
    """A valid v5 dict whose first design entry is updated with ``entry``."""
    data = design_project().to_dict()
    data["grid_designs"][0] |= entry
    return data


def test_designs_round_trip_byte_for_byte_as_version_5(tmp_path: Path) -> None:
    path = tmp_path / "designs.qsp"
    project = design_project()
    save_project(path, project)
    text = path.read_text("utf-8")
    data = json.loads(text)
    assert data["version"] == FORMAT_VERSION == 5
    jsonschema.validate(data, SCHEMA)
    assert data["grid_designs"][0] == {
        "path": "designs/feeder_a.qgrid",
        "active": False,
        "power_flow": {
            "control_mode": "off",
            "max_iterations": 50,
            "max_control_iterations": None,
            "tolerance": 1e-6,
        },
        "limits": {
            "voltage_min_pu": 0.92,
            "voltage_max_pu": 1.08,
            "line_loading_limit_pct": 80.0,
            "transformer_loading_limit_pct": 120.0,
            "warn_fraction": 0.8,
        },
    }
    assert data["grid_designs"][1]["active"] is True
    loaded = load_project(path)
    assert loaded.ok and loaded.warnings == [], loaded.errors
    assert loaded.project == project
    save_project(path, loaded.project)
    assert path.read_text("utf-8") == text


def test_every_powerflowconfig_field_is_stored_per_design() -> None:
    entry = GridDesignSettings("a.qgrid", True, CONFIG).to_dict()
    stored = set(entry["power_flow"]) | set(entry["limits"])
    assert stored == set(PowerFlowConfig().to_dict())


def test_a_project_without_designs_is_written_as_version_4(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project(grid=GridSettings("feeder.dss")))
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == BASE_VERSION == 4 and "grid_designs" not in data
    jsonschema.validate(data, SCHEMA)


def test_v4_sample_stays_v4_until_a_design_is_added(tmp_path: Path) -> None:
    folder = tmp_path / "examples"
    shutil.copytree(EXAMPLES / "grid", folder / "grid")
    path = folder / "ieee13_powerflow.qsp"
    shutil.copy(SAMPLES[4], path)
    loaded = load_project(path)
    assert loaded.ok and loaded.project is not None, loaded.errors
    save_project(path, loaded.project)
    assert path.read_bytes() == SAMPLES[4].read_bytes()  # v4, byte for byte

    loaded.project.grid_designs = [GridDesignSettings("ieee13.qgrid", True)]
    save_project(path, loaded.project)
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == 5
    assert data["grid"] == json.loads(SAMPLES[4].read_text("utf-8"))["grid"]  # v4 part kept
    jsonschema.validate(data, SCHEMA)
    again = load_project(path)
    assert again.ok and again.project == loaded.project

    loaded.project.grid_designs = []  # the design removed again: back to v4
    save_project(path, loaded.project)
    assert path.read_bytes() == SAMPLES[4].read_bytes()


@pytest.mark.parametrize("version", sorted(SAMPLES))
def test_every_older_version_opens_unchanged(version: int) -> None:
    raw = SAMPLES[version].read_text("utf-8")
    assert json.loads(raw)["version"] == version
    loaded = load_project(SAMPLES[version])
    assert loaded.ok, loaded.errors
    assert loaded.project is not None and loaded.project.grid_designs == []
    assert loaded.project.version == BASE_VERSION
    assert SAMPLES[version].read_text("utf-8") == raw  # opening never writes


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_grid_designs_are_refused_in_older_versions(tmp_path: Path, version: int) -> None:
    data = design_project().to_dict() | {"version": version}
    if version == 1:
        del data["simulation"]
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and "unknown field 'grid_designs'" in loaded.errors
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)


def test_an_empty_list_opens_and_is_saved_as_version_4(tmp_path: Path) -> None:
    data = Project().to_dict() | {"version": 5, "grid_designs": []}
    jsonschema.validate(data, SCHEMA)
    path = write(tmp_path, data)
    loaded = load_project(path)
    assert loaded.ok and loaded.project is not None and loaded.project.grid_designs == []
    save_project(path, loaded.project)
    assert json.loads(path.read_text("utf-8"))["version"] == 4


def test_a_newer_version_is_refused(tmp_path: Path) -> None:
    data = design_project().to_dict() | {"version": FORMAT_VERSION + 1}
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and f"format version {FORMAT_VERSION + 1}" in loaded.errors[0]


def test_integer_numbers_load_as_float(tmp_path: Path) -> None:
    data = design_data()
    data["grid_designs"][0]["limits"] |= {"voltage_max_pu": 1, "line_loading_limit_pct": 80}
    data["grid_designs"][0]["power_flow"]["tolerance"] = 1
    loaded = load_project(write(tmp_path, data))
    assert loaded.ok, loaded.errors
    config = loaded.project.grid_designs[0].config  # type: ignore[union-attr]
    assert type(config.voltage_max_pu) is float and type(config.line_loading_limit_pct) is float
    assert type(config.tolerance) is float


def _limits(**values: Any) -> dict[str, Any]:
    limits = copy.deepcopy(design_project().to_dict()["grid_designs"][0]["limits"])
    return limits | values


def _power_flow(**values: Any) -> dict[str, Any]:
    return copy.deepcopy(design_project().to_dict()["grid_designs"][0]["power_flow"]) | values


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"path": "a.dss"}, "is not a .qgrid file"),
        ({"path": 3}, "grid_designs[0].path must be a .qgrid file path"),
        ({"active": "yes"}, "grid_designs[0].active must be true or false"),
        ({"active": True}, "exactly one grid design must be active, not 2"),
        ({"path": "designs/FEEDER_B.qgrid"}, "two grid designs have the same file"),
        ({"limits": _limits(voltage_min_pu=1.1)}, "voltage limits must satisfy"),
        ({"limits": _limits(warn_fraction=1.5)}, "warn fraction"),
        ({"limits": _limits(transformer_loading_limit_pct=0)}, "transformer loading limit"),
        ({"limits": _limits(warn_fraction="0.9")}, "grid_designs[0].limits must be numbers"),
        (
            {"limits": {"voltage_min_pu": 0.9, "voltage_max_pu": 1.1, "loading_limit_pct": 100}},
            "grid_designs[0].limits must have exactly the fields",
        ),
        ({"power_flow": _power_flow(control_mode="snap")}, "control_mode must be one of"),
        ({"power_flow": _power_flow(max_iterations=1.5)}, "must be a whole number or null"),
        ({"power_flow": {"control_mode": "off"}}, "power_flow must have exactly the fields"),
        ({"extra": 1}, "grid_designs[0] must have exactly the fields"),
    ],
)
def test_bad_design_entries_are_refused(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    loaded = load_project(write(tmp_path, design_data(**change)))
    assert not loaded.ok and loaded.project is None
    assert any(message in e for e in loaded.errors), loaded.errors


def test_no_active_design_is_refused(tmp_path: Path) -> None:
    data = design_data()
    data["grid_designs"][1]["active"] = False
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and "exactly one grid design must be active, not 0" in loaded.errors


def test_grid_designs_must_be_a_list(tmp_path: Path) -> None:
    data = design_project().to_dict() | {"grid_designs": {"path": "a.qgrid"}}
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and loaded.errors == ["'grid_designs' must be a list"]


@pytest.mark.parametrize(
    ("designs", "message"),
    [
        ([GridDesignSettings("a.qgrid"), GridDesignSettings("b.qgrid")], "not 0"),
        ([GridDesignSettings("a.qgrid", True), GridDesignSettings("b.qgrid", True)], "not 2"),
        ([GridDesignSettings("a.qgrid", True), GridDesignSettings("A.QGRID")], "same file"),
        ([GridDesignSettings("a.json", True)], "not a .qgrid file"),
        (
            [GridDesignSettings("a.qgrid", True, PowerFlowConfig(warn_fraction=0.0))],
            "warn fraction",
        ),
    ],
)
def test_save_refuses_bad_designs_and_writes_nothing(
    tmp_path: Path, designs: list[GridDesignSettings], message: str
) -> None:
    with pytest.raises(ProjectError, match=message):
        save_project(tmp_path / "p.qsp", Project(grid_designs=designs))
    assert not (tmp_path / "p.qsp").exists()
