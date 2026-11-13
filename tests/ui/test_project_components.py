"""M5: user component code is saved in the project and is trusted like block code."""

from __future__ import annotations

from pathlib import Path

from bridge.project_manager import code_digest, user_code
from core.project.io import BlockSource, Project
from tests.core.physical.test_user import TC_RESISTOR
from tests.ui.test_project_manager import start_app


def test_fingerprint_without_component_code_is_unchanged() -> None:
    blocks = [BlockSource("function1", "x = 1\n")]
    project = Project(block_sources=blocks)
    assert code_digest(user_code(project)) == code_digest(blocks)
    project.physical_sources = [BlockSource("component1", "y = 2\n")]
    assert code_digest(user_code(project)) != code_digest(blocks)


def _author(path: Path, settings: Path) -> None:
    app = start_app(settings)
    try:
        assert app.diagram.applyComponent(TC_RESISTOR)
        assert app.diagram.addBlock("TCThermalResistor", 0, 0)
        assert app.projects.saveAs(str(path))
    finally:
        app.close()


def test_component_code_round_trips_and_asks_for_trust(qapp: object, tmp_path: Path) -> None:
    path = tmp_path / "rth.qsp"
    _author(path, tmp_path / "other-machine.ini")

    app = start_app(tmp_path / "settings.ini")
    try:
        asked: list[tuple[str, int]] = []
        app.projects.trustRequested.connect(lambda name, n: asked.append((name, n)))
        assert app.projects.open(str(path))
        assert asked == [("rth.qsp", 1)], "component code is code: ask first"

        app.projects.confirmTrust(False)
        assert not app.diagram.isUserComponent("TCThermalResistor")
        assert app.diagram.blocks.rowCount() == 0  # its block type is unknown

        assert app.projects.open(str(path))
        app.projects.confirmTrust(True)
        assert app.diagram.isUserComponent("TCThermalResistor")
        assert [r["blockId"] for r in app.diagram.blocks.rows()] == ["TCThermalResistor1"]
        saved = app.projects.current_project()
        assert [s.code for s in saved.physical_sources] == [TC_RESISTOR]

        asked.clear()
        assert app.projects.open(str(path))
        assert asked == [], "once accepted, the same code is trusted"
    finally:
        app.close()
