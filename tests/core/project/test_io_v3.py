"""O1: .qsp format version 3 - physical blocks and wires, Solver Configuration
settings, the display-unit setting; version 1 and 2 files still open."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from core.blocks.diagram import Diagram
from core.physical.network import NetworkExtractor
from core.project.io import (
    BASE_VERSION,
    FORMAT_VERSION,
    BlockSource,
    DiagramEntry,
    Project,
    load_project,
    save_project,
)
from tests.core.project.test_io import SCHEMA, write
from tests.fixtures.physical.library import library_registry, load_diagram, new_diagram, wire

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
V01 = Path(__file__).resolve().parents[2] / "fixtures" / "v01"
V2_SAMPLES = ["rc_filter.qsp", "rlc_bandpass.qsp", "hierarchy/rc2.qsp", "buck_cpm/buck_cpm.qsp"]
COMPONENT = "@physical_component\nclass P(PhysicalComponent):\n    pass\n"
SOLVER = {"Ts": 1e-4, "reltol": 1e-4, "abstol": 1e-12, "max_step": 2e-5, "steady_state": True}


def speed_loop() -> Diagram:
    """A P speed loop: Constant -> Sum -> Gain -> torque source on an inertia."""
    d, _reg = new_diagram()
    d.add_block("MechanicalRotationalReference", block_id="rref")
    d.add_block("SolverConfiguration", block_id="solver", settings=SOLVER)
    d.add_block("Inertia", block_id="inertia", settings={"J": 0.01, "w0": 5.0})
    d.add_block("RotationalDamper", block_id="friction", settings={"b": 0.001})
    d.add_block("ControlledTorqueSource", block_id="drive")
    d.add_block("AngularVelocitySensor", block_id="ws")
    wire(d, "solver.port", "rref.R")
    wire(d, "inertia.A", "drive.B")
    wire(d, "drive.A", "rref.R")
    wire(d, "friction.A", "inertia.A")
    wire(d, "friction.B", "rref.R")
    wire(d, "ws.A", "inertia.A")
    wire(d, "ws.B", "rref.R")
    d.add_block("Constant", block_id="ref", settings={"value": 100.0})
    d.add_block("Sum", block_id="err", settings={"signs": "+-"})
    d.add_block("Gain", block_id="kp", settings={"k": 1.0})
    d.add_wire("ref", "y", "err", "a")
    d.add_wire("ws", "w", "err", "b")
    d.add_wire("err", "y", "kp", "u")
    d.add_wire("kp", "y", "drive", "tau")
    return d


def physical_project() -> Project:
    return Project(
        diagrams=[DiagramEntry("main", speed_loop().to_dict())],
        physical_sources=[BlockSource("component1", COMPONENT)],
        sweep={"block": "kp", "parameter": "k", "values": "0.5 1 2"},
        display_units=True,
    )


def test_physical_project_round_trips_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "speed.qsp"
    project = physical_project()
    save_project(path, project)
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == BASE_VERSION
    jsonschema.validate(data, SCHEMA)
    assert data["display_units"] is True
    diagram = data["diagrams"][0]["diagram"]
    assert {"a": "drive.B", "b": "inertia.A"} in diagram["physical_wires"]  # sorted ends
    solver = next(b for b in diagram["blocks"] if b["id"] == "solver")
    assert solver["settings"] == SOLVER

    loaded = load_project(path)
    assert loaded.ok and loaded.warnings == [], loaded.errors
    again = loaded.project
    assert again is not None
    assert again.to_dict() == project.to_dict()
    first = path.read_bytes()
    save_project(path, again)
    assert path.read_bytes() == first


def test_reopened_diagram_rebuilds_and_passes_the_network_checks(tmp_path: Path) -> None:
    path = tmp_path / "speed.qsp"
    save_project(path, physical_project())
    loaded = load_project(path)
    assert loaded.project is not None
    rebuilt = load_diagram(loaded.project.diagrams[0].diagram)
    assert rebuilt.to_dict() == speed_loop().to_dict()
    assert len(rebuilt.physical_wires) == 7
    extracted = NetworkExtractor(library_registry()).extract(rebuilt)
    assert extracted.ok, extracted.diagnostics
    assert len(extracted.networks) == 1


def test_display_units_off_is_left_out(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project())
    assert "display_units" not in json.loads(path.read_text("utf-8"))
    assert load_project(path).project.display_units is False  # type: ignore[union-attr]


def test_version_2_file_of_a_development_build_with_physical_wires_opens(
    tmp_path: Path,
) -> None:
    """v0.3 builds before O1 wrote physical diagrams as version 2 (bug: they could not
    be reopened). They open now, and save as the current version."""
    data = physical_project().to_dict()
    data["version"] = 2
    del data["display_units"]
    jsonschema.validate(data, SCHEMA)
    path = write(tmp_path, data)
    loaded = load_project(path)
    assert loaded.ok, loaded.errors
    assert loaded.project is not None
    assert loaded.project.diagrams[0].diagram["physical_wires"]
    save_project(path, loaded.project)
    assert json.loads(path.read_text("utf-8"))["version"] == BASE_VERSION


def test_version_2_file_may_not_have_display_units(tmp_path: Path) -> None:
    data = Project(display_units=True).to_dict() | {"version": 2}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and "unknown field 'display_units'" in loaded.errors


def test_version_1_diagram_may_not_have_physical_wires(tmp_path: Path) -> None:
    data = physical_project().to_dict()
    for key in ("simulation", "physical_sources", "sweep", "display_units"):
        del data[key]
    data["version"] = 1
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok
    assert any("exactly the fields 'blocks' and 'wires'" in e for e in loaded.errors)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"display_units": "yes"}, "'display_units' must be true or false"),
        ({"physical_wires": [{"a": "inertia.A"}]}, "physical_wires[0] needs a and b"),
        ({"physical_wires": [{"a": "inertia", "b": "rref.R"}]}, "physical_wires[0] needs a"),
        ({"physical_wires": [{"a": 1, "b": "rref.R"}]}, "physical_wires[0] needs a"),
        ({"physical_wires": {"a": "x.y"}}, "physical_wires must be lists"),
        ({"extra": []}, "may have 'physical_wires'"),
        ({"Ts": 0}, "settings.Ts must be a positive number"),
        ({"Ts": "100u"}, "settings.Ts must be a positive number"),
        ({"reltol": -1.0}, "settings.reltol must be a positive number"),
        ({"max_step": -1e-6}, "settings.max_step must be a 0 or more number"),
        ({"steady_state": 1}, "settings.steady_state must be true or false"),
        ({"bogus": 1}, "unknown Solver Configuration setting 'bogus'"),
    ],
)
def test_bad_physical_content_is_an_error_not_an_exception(
    tmp_path: Path, change: dict, message: str
) -> None:
    data = physical_project().to_dict()
    diagram = data["diagrams"][0]["diagram"]
    solver = next(b for b in diagram["blocks"] if b["spec"] == "SolverConfiguration")
    for key, value in change.items():
        if key == "display_units":
            data[key] = value
        elif key in ("physical_wires", "extra"):
            diagram[key] = value
        else:
            solver["settings"][key] = value
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and loaded.project is None
    assert any(message in e for e in loaded.errors), loaded.errors


def test_max_step_zero_means_ts_and_is_allowed(tmp_path: Path) -> None:
    project = physical_project()
    blocks = project.diagrams[0].diagram["blocks"]
    next(b for b in blocks if b["id"] == "solver")["settings"]["max_step"] = 0.0
    assert load_project(write(tmp_path, project.to_dict())).ok


def test_a_newer_version_is_refused(tmp_path: Path) -> None:
    data = Project().to_dict() | {"version": FORMAT_VERSION + 1}
    loaded = load_project(write(tmp_path, data))
    assert not loaded.ok and f"format version {FORMAT_VERSION + 1}" in loaded.errors[0]


@pytest.mark.parametrize(
    "path",
    [V01 / n for n in ("rc_filter.qsp", "diode_rectifier.qsp", "first_order_feedback.qsp")]
    + [EXAMPLES / n for n in V2_SAMPLES],
    ids=lambda p: p.name,
)
def test_v1_and_v2_projects_open_save_as_current_and_reopen_unchanged(
    path: Path, tmp_path: Path
) -> None:
    copy = tmp_path / path.name
    shutil.copyfile(path, copy)
    for sub in path.parent.glob("*.asy"):  # circuit symbols next to the project
        shutil.copyfile(sub, tmp_path / sub.name)
        if sub.with_suffix(".sub").is_file():
            shutil.copyfile(sub.with_suffix(".sub"), tmp_path / sub.with_suffix(".sub").name)
    results = path.with_name(f"{path.stem}_results")
    if results.is_dir():
        shutil.copytree(results, tmp_path / results.name)
    version = json.loads(path.read_text("utf-8"))["version"]
    assert version in (1, 2)
    before = load_project(copy)
    assert before.ok and before.warnings == [], before.errors
    assert before.project is not None
    save_project(copy, before.project)
    data = json.loads(copy.read_text("utf-8"))
    assert data["version"] == BASE_VERSION
    jsonschema.validate(data, SCHEMA)
    after = load_project(copy)
    assert after.ok and after.project is not None
    keep = lambda d: {k: v for k, v in d.items() if k not in ("version", "results")}  # noqa: E731
    assert keep(after.project.to_dict()) == keep(before.project.to_dict())
