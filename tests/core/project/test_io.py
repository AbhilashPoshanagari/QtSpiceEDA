from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from core.project.io import (
    FORMAT_VERSION,
    AnalysisSettings,
    BlockSource,
    DiagramEntry,
    NetlistEntry,
    Project,
    ProjectError,
    SimulationSettings,
    load_project,
    results_dir,
    save_project,
)
from core.schematic.model import Schematic
from core.sim.result import SimulationResult

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "core" / "project" / "schema.json").read_text("utf-8")
)

RC = "* RC low-pass\nV1 in 0 AC 1\nR1 in out 1k\nC1 out 0 1u\n.ac dec 10 1 1meg\n.end\n"
CODE = "@block\ndef double(u):\n    return 2 * u\n"


def sample_project() -> Project:
    freq = np.logspace(0, 6, 61)
    ac = SimulationResult(
        axis="frequency",
        vectors={"frequency": freq, "v(out)": 1 / (1 + 1j * freq * 1e-3)},
        units={"frequency": "Hz", "v(out)": "V"},
        analysis="ac",
    )
    t = np.arange(0, 1, 0.01)
    tran = SimulationResult("time", {"time": t, "Scope1.u": np.sin(t)}, {"time": "s"}, "tran")
    return Project(
        netlists=[NetlistEntry("main", RC), NetlistEntry("spare", "* ünïcode\n.end\n")],
        diagrams=[
            DiagramEntry(
                "main",
                {
                    "blocks": [
                        {"id": "Sine1", "spec": "Sine", "x": 20.0, "y": 40.0, "settings": {}},
                        {"id": "D1", "spec": "double", "x": 120.0, "y": 40.0, "settings": {}},
                        {"id": "Scope1", "spec": "Scope", "x": 240.0, "y": 40.0, "settings": {}},
                    ],
                    "wires": [
                        {"src": "Sine1.y", "dst": "D1.u"},
                        {"src": "D1.out", "dst": "Scope1.u"},
                    ],
                },
            )
        ],
        block_sources=[BlockSource("function1", CODE)],
        analysis=AnalysisSettings(".tran 1u 1m", 2.5, 0.0005),
        results={"netlist": ac, "diagram": tran},
    )


def assert_results_equal(a: SimulationResult, b: SimulationResult) -> None:
    assert (a.axis, a.units, a.analysis) == (b.axis, b.units, b.analysis)
    assert list(a.vectors) == list(b.vectors)
    for name in a.vectors:
        assert a.vectors[name].dtype == b.vectors[name].dtype
        np.testing.assert_array_equal(a.vectors[name], b.vectors[name])


def test_round_trip_is_identical(tmp_path: Path) -> None:
    path = tmp_path / "rc.qsp"
    project = sample_project()
    save_project(path, project)
    first = path.read_bytes()

    loaded = load_project(path)
    assert loaded.ok, loaded.errors
    assert loaded.warnings == []
    assert loaded.project is not None
    assert loaded.project.netlists == project.netlists
    assert loaded.project.diagrams == project.diagrams
    assert loaded.project.block_sources == project.block_sources
    assert loaded.project.analysis == project.analysis
    assert list(loaded.project.results) == ["netlist", "diagram"]
    for name, result in project.results.items():
        assert_results_equal(result, loaded.project.results[name])

    save_project(path, loaded.project)
    assert path.read_bytes() == first


def test_files_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "rc.qsp"
    save_project(path, sample_project())
    assert sorted(p.name for p in results_dir(path).iterdir()) == ["diagram.npz", "netlist.npz"]
    assert not list(tmp_path.glob("*.tmp")), "temporary files are cleaned up"
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == FORMAT_VERSION
    assert data["results"] == [
        {"name": "netlist", "file": "netlist.npz"},
        {"name": "diagram", "file": "diagram.npz"},
    ]


def test_saved_file_matches_schema(tmp_path: Path) -> None:
    for project in (sample_project(), Project()):
        path = tmp_path / "p.qsp"
        save_project(path, project)
        jsonschema.validate(json.loads(path.read_text("utf-8")), SCHEMA)


def test_dropped_results_are_removed(tmp_path: Path) -> None:
    path = tmp_path / "rc.qsp"
    project = sample_project()
    save_project(path, project)
    del project.results["diagram"]
    save_project(path, project)
    assert [p.name for p in results_dir(path).iterdir()] == ["netlist.npz"]
    project.results.clear()
    save_project(path, project)
    assert not results_dir(path).exists()


def test_save_rejects_bad_names(tmp_path: Path) -> None:
    project = Project(netlists=[NetlistEntry("../evil", "")])
    with pytest.raises(ProjectError, match="letters, digits"):
        save_project(tmp_path / "p.qsp", project)
    assert not (tmp_path / "p.qsp").exists()


def test_save_rejects_bad_step(tmp_path: Path) -> None:
    project = Project(analysis=AnalysisSettings("", 1.0, 0.0))
    with pytest.raises(ProjectError, match="positive"):
        save_project(tmp_path / "p.qsp", project)


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "bad.qsp"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def valid_dict() -> dict:
    return Project().to_dict()


def test_missing_file(tmp_path: Path) -> None:
    result = load_project(tmp_path / "nope.qsp")
    assert not result.ok
    assert result.errors == ["nope.qsp does not exist"]


def test_bad_json(tmp_path: Path) -> None:
    result = load_project(write(tmp_path, '{"format": "qtspice-project",\n  oops'))
    assert result.project is None
    assert "not valid JSON (line 2" in result.errors[0]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"format": "something-else"}, "not a QtSpice project file"),
        ({"version": "1"}, "version must be a whole number"),
        ({"version": 0}, "version must be a whole number"),
        (
            {"version": FORMAT_VERSION + 1},
            f"saved by a newer QtSpice (format version {FORMAT_VERSION + 1})",
        ),
        ({"simulation": {"compat_mode": "ltpsa; quit"}}, "simulation.compat_mode must be"),
        ({"simulation": {}}, "exactly the field 'compat_mode'"),
        ({"netlists": {}}, "'netlists' must be a list"),
        ({"netlists": [{"name": "a"}]}, "missing field netlists[0].text"),
        ({"netlists": [{"name": "a", "text": 3}]}, "netlists[0].text has the wrong type"),
        ({"netlists": [{"name": "a b", "text": ""}]}, "may only use letters"),
        ({"diagrams": [{"name": "d", "diagram": {"blocks": [{}], "wires": []}}]}, "blocks[0]"),
        ({"analysis": {"card": "", "diagram_t_end": -1, "diagram_dt": 1}}, "diagram_t_end"),
        ({"results": [{"name": "r", "file": "../x.npz"}]}, "results[0].file must be r.npz"),
        ({"extra": 1}, "unknown field 'extra'"),
    ],
)
def test_bad_content(tmp_path: Path, change: dict, message: str) -> None:
    data = valid_dict()
    data.update(change)
    result = load_project(write(tmp_path, data))
    assert result.project is None
    assert any(message in e for e in result.errors), result.errors


def test_missing_field(tmp_path: Path) -> None:
    data = valid_dict()
    del data["block_sources"]
    result = load_project(write(tmp_path, data))
    assert result.errors == ["missing field 'block_sources'"]


def test_missing_and_damaged_results_are_warnings(tmp_path: Path) -> None:
    path = tmp_path / "rc.qsp"
    save_project(path, sample_project())
    (results_dir(path) / "netlist.npz").unlink()
    (results_dir(path) / "diagram.npz").write_bytes(b"not a zip")
    result = load_project(path)
    assert result.ok
    assert result.project is not None and result.project.results == {}
    assert len(result.warnings) == 2
    assert "netlist.npz is missing" in result.warnings[0]
    assert "diagram.npz" in result.warnings[1]


# ---- L3: format version 2 adds simulation.compat_mode


def test_compat_mode_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project(simulation=SimulationSettings("")))
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == FORMAT_VERSION
    assert data["simulation"] == {"compat_mode": ""}
    loaded = load_project(path)
    assert loaded.ok and loaded.project is not None
    assert loaded.project.simulation.compat_mode == ""
    assert Project().simulation.compat_mode == "ltpsa"


def test_version_1_file_opens_with_default_compat_mode(tmp_path: Path) -> None:
    data = valid_dict()
    data["version"] = 1
    del data["simulation"]
    jsonschema.validate(data, SCHEMA)
    result = load_project(write(tmp_path, data))
    assert result.ok and result.project is not None
    assert result.project.simulation.compat_mode == "ltpsa"


def test_version_rules_for_simulation(tmp_path: Path) -> None:
    v1 = valid_dict() | {"version": 1}
    assert "unknown field 'simulation'" in load_project(write(tmp_path, v1)).errors
    v2 = valid_dict()
    del v2["simulation"]
    assert load_project(write(tmp_path, v2)).errors == ["missing field 'simulation'"]
    for bad in (v1, v2):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, SCHEMA)


def test_save_rejects_bad_compat_mode(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="compatibility mode"):
        save_project(tmp_path / "p.qsp", Project(simulation=SimulationSettings("LT PS")))


# ---- S4: one schematic per project

RC_SCHEMATIC = Path(__file__).resolve().parents[2] / "fixtures" / "schematics" / "rc" / "rc.json"


def rc_schematic() -> Schematic:
    schematic, diagnostics = Schematic.from_dict(
        json.loads(RC_SCHEMATIC.read_text(encoding="utf-8"))
    )
    assert diagnostics == []
    return schematic


def test_schematic_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    project = Project(schematic=rc_schematic())
    save_project(path, project)
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == FORMAT_VERSION == 3
    jsonschema.validate(data, SCHEMA)
    loaded = load_project(path)
    assert loaded.ok and loaded.warnings == []
    assert loaded.project is not None and loaded.project.schematic == project.schematic
    first = path.read_bytes()
    save_project(path, loaded.project)
    assert path.read_bytes() == first


def test_project_without_a_schematic_has_no_schematic_field(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project())
    assert "schematic" not in json.loads(path.read_text("utf-8"))
    loaded = load_project(path)
    assert loaded.ok and loaded.project is not None and loaded.project.schematic is None


def test_version_2_file_saved_before_schematics_still_opens(tmp_path: Path) -> None:
    data = valid_dict()
    assert "schematic" not in data
    jsonschema.validate(data, SCHEMA)
    assert load_project(write(tmp_path, data)).ok


def test_version_1_file_may_not_have_a_schematic(tmp_path: Path) -> None:
    data = valid_dict() | {"version": 1, "schematic": rc_schematic().to_dict()}
    del data["simulation"]
    assert load_project(write(tmp_path, data)).errors == ["unknown field 'schematic'"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)


def test_bad_schematic_is_a_load_error(tmp_path: Path) -> None:
    schematic = rc_schematic().to_dict()
    schematic["instances"][0]["rotation"] = 45
    result = load_project(write(tmp_path, valid_dict() | {"schematic": schematic}))
    assert result.project is None
    assert result.errors == ["schematic: instances[0].rotation must be 0, 90, 180 or 270, not 45"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(valid_dict() | {"schematic": schematic}, SCHEMA)


def test_schematic_warnings_are_load_warnings(tmp_path: Path) -> None:
    schematic = rc_schematic().to_dict() | {"colour": "blue"}
    result = load_project(write(tmp_path, valid_dict() | {"schematic": schematic}))
    assert result.ok
    assert result.warnings == ["schematic: unknown field 'colour' ignored"]


# ---------------------------------------------------------------- H3: circuit symbols


def test_circuit_symbols_round_trip_and_are_left_out_when_empty(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project(symbols=["rclp.asy", "C:/elsewhere/amp.asy"]))
    data = json.loads(path.read_text("utf-8"))
    assert data["symbols"] == ["rclp.asy", "C:/elsewhere/amp.asy"]
    jsonschema.validate(data, SCHEMA)
    loaded = load_project(path)
    assert loaded.ok and loaded.project.symbols == ["rclp.asy", "C:/elsewhere/amp.asy"]

    save_project(path, Project())
    assert "symbols" not in json.loads(path.read_text("utf-8"))


def test_bad_symbol_lists_are_load_errors(tmp_path: Path) -> None:
    for bad in ("rclp.asy", ["rclp.sub"], [3]):
        result = load_project(write(tmp_path, valid_dict() | {"symbols": bad}))
        assert result.errors == ["'symbols' must be a list of .asy file paths"], bad
    data = valid_dict() | {"version": 1, "symbols": ["a.asy"]}
    del data["simulation"]
    assert load_project(write(tmp_path, data)).errors == ["unknown field 'symbols'"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)


# ---- plot layouts (W1)


def _plots() -> dict:
    from core.waveform.layout import Pane, PlotLayout

    tran = PlotLayout.default("tran")
    tran.panes = [Pane(["V(out)"], title="out"), Pane(["I(R1)"])]
    return {"tran": tran, "ac": PlotLayout.default("ac")}


def test_plots_round_trip_and_match_schema(tmp_path: Path) -> None:
    project = sample_project()
    project.plots = _plots()
    path = tmp_path / "p.qsp"
    save_project(path, project)
    data = json.loads(path.read_text("utf-8"))
    jsonschema.validate(data, SCHEMA)
    assert list(data["plots"]) == ["tran", "ac"]
    loaded = load_project(path)
    assert loaded.ok and loaded.project is not None and loaded.warnings == []
    assert {a: p.to_dict() for a, p in loaded.project.plots.items()} == {
        a: p.to_dict() for a, p in project.plots.items()
    }


def test_plots_left_out_when_empty(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project())
    assert "plots" not in json.loads(path.read_text("utf-8"))


def test_bad_plots_only_warn(tmp_path: Path) -> None:
    data = valid_dict()
    data["plots"] = {"tran": {"panes": "x"}, "noise": {}}
    result = load_project(write(tmp_path, data))
    assert result.ok and result.project is not None
    assert set(result.project.plots) == {"tran"} and len(result.warnings) == 2
    data["plots"] = []
    assert load_project(write(tmp_path, data)).ok


def test_version_1_file_must_not_have_plots() -> None:
    data = valid_dict()
    data["version"] = 1
    del data["simulation"]
    data["plots"] = {}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)


# ---- plot definitions (W9)


def test_plot_defs_round_trip_and_match_schema(tmp_path: Path) -> None:
    project = sample_project()
    project.plot_defs = ".func Pythag(x,y) sqrt(x**2+y**2)\n"
    path = tmp_path / "p.qsp"
    save_project(path, project)
    data = json.loads(path.read_text("utf-8"))
    jsonschema.validate(data, SCHEMA)
    loaded = load_project(path)
    assert loaded.ok and loaded.project is not None
    assert loaded.project.plot_defs == project.plot_defs


def test_plot_defs_left_out_when_default(tmp_path: Path) -> None:
    path = tmp_path / "p.qsp"
    save_project(path, Project())
    assert "plot_defs" not in json.loads(path.read_text("utf-8"))
    loaded = load_project(path)
    assert loaded.project is not None and loaded.project.plot_defs is None


def test_plot_defs_must_be_text(tmp_path: Path) -> None:
    data = valid_dict()
    data["plot_defs"] = 3
    assert not load_project(write(tmp_path, data)).ok
    data["version"] = 1
    del data["simulation"]
    data["plot_defs"] = ""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
    assert not load_project(write(tmp_path, data)).ok


# ---- O4: the results folder is safe


def test_save_keeps_files_the_project_does_not_own(tmp_path: Path) -> None:
    path = tmp_path / "rc.qsp"
    folder = results_dir(path)
    folder.mkdir()
    mine = folder / "measured.npz"  # a user's own file, saved there before the project
    mine.write_bytes(b"not ours")
    (folder / "notes.txt").write_text("keep me")
    project = sample_project()
    save_project(path, project)
    del project.results["diagram"]
    save_project(path, project)
    assert sorted(p.name for p in folder.iterdir()) == ["measured.npz", "netlist.npz", "notes.txt"]
    project.results.clear()
    save_project(path, project)
    assert sorted(p.name for p in folder.iterdir()) == ["measured.npz", "notes.txt"]
    assert mine.read_bytes() == b"not ours"


def test_save_as_over_a_folder_with_no_project_deletes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "new.qsp"
    folder = results_dir(path)
    folder.mkdir()
    (folder / "diagram.npz").write_bytes(b"left by someone else")
    project = sample_project()
    del project.results["diagram"]
    save_project(path, project)
    assert (folder / "diagram.npz").read_bytes() == b"left by someone else"


def test_dropped_results_listed_by_the_old_file_are_removed(tmp_path: Path) -> None:
    """The record is the .qsp on disk: a result dropped in a later session goes too."""
    path = tmp_path / "rc.qsp"
    save_project(path, sample_project())
    reopened = load_project(path).project
    assert reopened is not None
    del reopened.results["netlist"]
    save_project(path, reopened)
    assert [p.name for p in results_dir(path).iterdir()] == ["diagram.npz"]


@pytest.mark.parametrize(
    "old_text",
    [
        "not json",
        json.dumps({"format": "something-else", "results": [{"name": "a", "file": "a.npz"}]}),
        json.dumps({"format": "qtspice-project", "results": "a.npz"}),
        json.dumps(
            {
                "format": "qtspice-project",
                "results": [
                    {"name": "x", "file": "../victim.npz"},
                    {"name": "../victim", "file": "../victim.npz"},
                    {"name": "a\n", "file": "a\n.npz"},
                    {"name": "b", "file": "c.npz"},
                    "c.npz",
                ],
            }
        ),
    ],
)
def test_a_bad_or_foreign_old_file_owns_nothing(tmp_path: Path, old_text: str) -> None:
    path = tmp_path / "p.qsp"
    path.write_text(old_text, encoding="utf-8")
    folder = results_dir(path)
    folder.mkdir()
    for name in ("a.npz", "c.npz"):
        (folder / name).write_bytes(b"x")
    victim = tmp_path / "victim.npz"
    victim.write_bytes(b"x")
    save_project(path, Project())
    assert sorted(p.name for p in folder.iterdir()) == ["a.npz", "c.npz"]
    assert victim.exists()
    assert load_project(path).ok
