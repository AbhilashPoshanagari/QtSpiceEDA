"""The sample projects open through ProjectManager like a user opens them: no trust
prompt, no warnings, and the editors show their content."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.ui.test_project_manager import App, start_app

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def app(qapp: object, tmp_path: Path) -> Iterator[App]:
    a = start_app(tmp_path / "settings.ini")
    yield a
    a.close()


def open_sample(app: App, name: str) -> None:
    asked: list[object] = []
    app.projects.trustRequested.connect(lambda *args: asked.append(args))
    assert app.projects.open(str(EXAMPLES / name))
    assert asked == []
    status = app.projects.property("statusText")
    assert ". " not in status, f"open reported warnings: {status}"
    assert app.projects.property("modified") is False


@pytest.mark.parametrize("name", ["rc_filter.qsp", "diode_rectifier.qsp", "diode_log.qsp"])
def test_netlist_samples_open_and_check_clean(app: App, name: str) -> None:
    open_sample(app, name)
    text = app.sim.property("netlistText")
    assert text.strip()
    assert app.sim.check(text) is True
    assert app.sim.property("diagnostics") == []


def test_feedback_sample_opens_and_runs(app: App, qtbot) -> None:
    open_sample(app, "first_order_feedback.qsp")
    ctl = app.diagram
    assert ctl.blocks.rowCount() == 6
    assert ctl.wires.rowCount() == 6
    assert ctl.property("tEnd") == 4.0
    assert ctl.property("dt") == 0.001
    with qtbot.waitSignal(ctl.resultReady, timeout=20000):
        assert ctl.run(ctl.property("tEnd"), ctl.property("dt"))
    assert sorted(app.waveforms.property("traceNames")) == ["r", "y"]
