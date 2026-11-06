"""v0.1 projects open through the ProjectManager the way File > Open does (release
check): the netlist, diagram and settings reach the bridge objects, nothing asks for
trust, and the project is not marked modified."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.project.io import load_project
from tests.ui.test_project_manager import App, start_app

V01 = Path(__file__).resolve().parents[1] / "fixtures" / "v01"


@pytest.fixture
def app(qapp: object, tmp_path: Path) -> Iterator[App]:
    a = start_app(tmp_path / "settings.ini")
    yield a
    a.close()


@pytest.mark.parametrize(
    "name", ["rc_filter.qsp", "diode_rectifier.qsp", "first_order_feedback.qsp"]
)
def test_v01_project_opens_in_the_app(app: App, tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    shutil.copyfile(V01 / name, path)
    expected = load_project(path).project
    asked: list[object] = []
    app.projects.trustRequested.connect(lambda *a: asked.append(a))

    assert app.projects.open(str(path))
    assert asked == []
    assert app.projects.property("errorString") == ""
    assert app.projects.property("filePath") == str(path.resolve())
    assert app.projects.property("modified") is False
    text = expected.netlists[0].text if expected.netlists else ""
    assert app.sim.property("netlistText") == text
    assert app.sim.property("compatMode") == expected.simulation.compat_mode
    blocks = len(expected.diagrams[0].diagram["blocks"]) if expected.diagrams else 0
    assert app.diagram.blocks.rowCount() == blocks
