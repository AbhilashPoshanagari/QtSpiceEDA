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
    load_project,
    results_dir,
    save_project,
)
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
        ({"version": FORMAT_VERSION + 1}, "saved by a newer QtSpice (format version 2)"),
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
