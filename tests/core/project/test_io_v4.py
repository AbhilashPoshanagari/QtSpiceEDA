"""G7: .qsp format version 4 - the grid section (.dss file, PowerFlowConfig solver
settings, voltage and loading limits); version 1, 2 and 3 files still open."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from core.grid.config import CONTROL_OFF, PowerFlowConfig
from core.project.io import (
    BASE_VERSION,
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
V2_SAMPLES = ["rlc_bandpass.qsp", "diode_log.qsp", "hierarchy/rc2.qsp"]
V3_SAMPLES = ["dc_motor_speed.qsp", "heatsink_warmup.qsp", "battery_discharge.qsp"]

CONFIG = PowerFlowConfig(
    control_mode=CONTROL_OFF,
    max_iterations=50,
    max_control_iterations=30,
    tolerance=1e-6,
    voltage_min_pu=0.9,
    voltage_max_pu=1.1,
    line_loading_limit_pct=80.0,
    transformer_loading_limit_pct=80.0,
)
FREE_SOLVER = {
    "control_mode": "off",
    "max_iterations": None,
    "max_control_iterations": None,
    "tolerance": None,
}


def grid_project(dss_file: str = "grid/ieee13/IEEE13Nodeckt.dss") -> Project:
    return Project(grid=GridSettings(dss_file, CONFIG))


def grid_data(**grid: Any) -> dict[str, Any]:
    data = grid_project().to_dict()
    data["grid"] |= grid
    return data


def test_grid_section_round_trips_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "feeder.qsp"
    project = grid_project()
    save_project(path, project)
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == BASE_VERSION == 4
    jsonschema.validate(data, SCHEMA)
    assert data["grid"] == {
        "dss_file": "grid/ieee13/IEEE13Nodeckt.dss",
        "power_flow": {
            "control_mode": "off",
            "max_iterations": 50,
            "max_control_iterations": 30,
            "tolerance": 1e-6,
        },
        "limits": {"voltage_min_pu": 0.9, "voltage_max_pu": 1.1, "loading_limit_pct": 80.0},
    }
    loaded = load_project(path)
    assert loaded.ok and loaded.warnings == [], loaded.errors
    assert loaded.project is not None and loaded.project.grid == project.grid
    first = path.read_bytes()
    save_project(path, loaded.project)
    assert path.read_bytes() == first


def test_default_settings_keep_the_file_values_as_null(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project(grid=GridSettings("feeder.dss")))
    data = json.loads(path.read_text("utf-8"))
    jsonschema.validate(data, SCHEMA)
    assert data["grid"]["power_flow"] == FREE_SOLVER | {"control_mode": "static"}
    loaded = load_project(path)
    assert loaded.project is not None and loaded.project.grid == GridSettings("feeder.dss")


def test_integer_numbers_load_as_float(tmp_path: Path) -> None:
    data = grid_data(
        power_flow=FREE_SOLVER | {"tolerance": 1},
        limits={"voltage_min_pu": 0.9, "voltage_max_pu": 1, "loading_limit_pct": 80},
    )
    loaded = load_project(write(tmp_path, data))
    assert loaded.ok, loaded.errors
    config = loaded.project.grid.config  # type: ignore[union-attr]
    assert type(config.voltage_max_pu) is float and type(config.line_loading_limit_pct) is float
    assert type(config.tolerance) is float


def test_no_grid_is_left_out(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project())
    assert "grid" not in json.loads(path.read_text("utf-8"))
    assert load_project(path).project.grid is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("grid", "message"),
    [
        ({"dss_file": ""}, "grid.dss_file must be a non-empty path"),
        ({"dss_file": 3}, "grid.dss_file must be a non-empty path"),
        ({"power_flow": {"control_mode": "static"}}, "grid.power_flow must have exactly"),
        (
            {"power_flow": FREE_SOLVER | {"control_mode": "time"}},
            "control_mode must be one of static, off",
        ),
        (
            {"power_flow": FREE_SOLVER | {"max_iterations": 2.5}},
            "grid.power_flow.max_iterations must be a whole number or null",
        ),
        (
            {"power_flow": FREE_SOLVER | {"max_control_iterations": True}},
            "grid.power_flow.max_control_iterations must be a whole number or null",
        ),
        (
            {"power_flow": FREE_SOLVER | {"max_iterations": 0}},
            "grid: max iterations must be at least 1",
        ),
        (
            {"power_flow": FREE_SOLVER | {"tolerance": "small"}},
            "grid.power_flow.tolerance must be a number or null",
        ),
        ({"limits": {"voltage_min_pu": 0.9}}, "grid.limits must have exactly"),
        (
            {"limits": {"voltage_min_pu": 1.1, "voltage_max_pu": 0.9, "loading_limit_pct": 100}},
            "grid: voltage limits must satisfy 0 < min < max",
        ),
        (
            {"limits": {"voltage_min_pu": 0.9, "voltage_max_pu": 1.1, "loading_limit_pct": True}},
            "grid.limits must be numbers",
        ),
    ],
)
def test_bad_grid_section_is_an_error_not_an_exception(
    tmp_path: Path, grid: dict[str, Any], message: str
) -> None:
    loaded = load_project(write(tmp_path, grid_data(**grid)))
    assert loaded.project is None
    assert any(message in e for e in loaded.errors), loaded.errors


@pytest.mark.parametrize("grid", [{"results": "grid/"}, "feeder.dss", None])
def test_grid_section_of_the_wrong_shape_is_refused(tmp_path: Path, grid: Any) -> None:
    data = grid_project().to_dict()
    data["grid"] = data["grid"] | grid if isinstance(grid, dict) else grid
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok
    assert "'grid' must have exactly the fields dss_file, power_flow and limits" in loaded.errors


@pytest.mark.parametrize("version", [1, 2, 3])
def test_older_versions_may_not_have_a_grid(tmp_path: Path, version: int) -> None:
    data = grid_project().to_dict() | {"version": version}
    if version == 1:
        del data["simulation"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and "unknown field 'grid'" in loaded.errors


def test_save_refuses_bad_settings(tmp_path: Path) -> None:
    bad = Project(grid=GridSettings("f.dss", PowerFlowConfig(voltage_min_pu=1.2)))
    with pytest.raises(ProjectError, match="voltage limits"):
        save_project(tmp_path / "p.qsp", bad)
    with pytest.raises(ProjectError, match=r"\.dss file"):
        save_project(tmp_path / "p.qsp", Project(grid=GridSettings(" ")))
    assert not (tmp_path / "p.qsp").exists()


@pytest.mark.parametrize(
    "path",
    [V01 / "rc_filter.qsp"]
    + [EXAMPLES / n for n in V2_SAMPLES]
    + [EXAMPLES / n for n in V3_SAMPLES],
    ids=lambda p: p.name,
)
def test_v1_v2_v3_samples_open_save_as_v4_and_reopen_unchanged(path: Path, tmp_path: Path) -> None:
    folder = tmp_path / "copy"
    shutil.copytree(path.parent, folder)  # with its symbols and results folder
    copy = folder / path.name
    assert json.loads(path.read_text("utf-8"))["version"] in (1, 2, 3)
    before = load_project(copy)
    assert before.ok and before.warnings == [], before.errors
    assert before.project is not None and before.project.grid is None
    save_project(copy, before.project)
    data = json.loads(copy.read_text("utf-8"))
    assert data["version"] == 4 and "grid" not in data
    jsonschema.validate(data, SCHEMA)
    after = load_project(copy)
    assert after.ok and after.project is not None
    keep = lambda d: {k: v for k, v in d.items() if k not in ("version", "results")}  # noqa: E731
    assert keep(after.project.to_dict()) == keep(before.project.to_dict())
