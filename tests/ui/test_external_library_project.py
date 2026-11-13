"""The TSMC 180 nm inverter project opens in the app and netlists with its local library."""

from __future__ import annotations

from pathlib import Path

from tests.core.project.test_external_library import LIBRARY, PROJECT
from tests.ui.test_project_manager import start_app


def test_app_opens_the_project_and_includes_the_local_library(qapp: object, tmp_path: Path) -> None:
    app = start_app(tmp_path / "settings.ini")
    try:
        asked: list[str] = []
        app.projects.trustRequested.connect(lambda name, _n: asked.append(name))
        assert app.projects.open(str(PROJECT))
        assert asked == [], "no block or component code: nothing to trust"
        status = app.projects.property("statusText")
        assert "missing" not in status, status  # both project symbols were found
        text = app.schematic.netlist_text()
        include = next(line for line in text.splitlines() if line.lower().startswith(".include"))
        assert (
            Path(include.split(None, 1)[1].strip('"')).resolve()
            == (LIBRARY / "tsmc018.lib").resolve()
        )
        assert "MN1 out in 0 0 CMOSN L=180n W=0.36u" in text
        assert "MP1 out in vdd vdd CMOSP L=180n W=0.9u" in text
    finally:
        app.close()
