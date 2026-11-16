"""D9: the designer check of ``--smoke-test`` (app/smoke.py _check_designer): a
two-bus design built with edit commands only, checked and written as a DSS script;
run only when dss-python is available (skipped from source without it)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

import bridge.grid_controller as grid_controller  # noqa: E402
from app.smoke import _check_designer  # noqa: E402
from bridge.grid_controller import GridController  # noqa: E402
from bridge.grid_diagram_controller import GridDiagramController  # noqa: E402


@pytest.fixture
def designer(qapp: object) -> Iterator[GridDiagramController]:
    grid = GridController()
    d = GridDiagramController(grid_controller=grid)
    yield d
    d.shutdown()
    grid.shutdown()


def test_without_opendss_the_design_is_built_written_and_the_run_skipped(
    designer: GridDiagramController, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grid_controller, "engine_available", lambda: False)
    lines: list[str] = []
    assert _check_designer(designer, tmp_path, lines.append), lines
    assert lines[0] == "ok: designer opened ieee13_design.qgrid: 16 buses, 45 elements"
    assert lines[1].startswith("ok: designer: two-bus design from 6 edit commands")
    assert "ok: designer: design saved and reopened (smoke_design.qgrid)" in lines
    assert lines[-1] == "skipped: design power flow (dss-python is not installed)"
    assert designer.busCount == 2 and designer.filePath.endswith("smoke_design.qgrid")
    assert designer.design().grid.loads["ld1"].kw == 1155.0  # copied from IEEE 13 load 671
    assert "new load." in (tmp_path / "smoke_design.dss").read_text("utf-8").lower()


def test_a_missing_designer_fails(tmp_path: Path) -> None:
    lines: list[str] = []
    assert not _check_designer(None, tmp_path, lines.append)
    assert lines == ["FAIL: the Grid Network Designer is not in Main.qml"]


@pytest.mark.opendss
def test_with_opendss_the_design_runs(
    qtbot, designer: GridDiagramController, tmp_path: Path
) -> None:
    lines: list[str] = []
    assert _check_designer(designer, tmp_path, lines.append), lines
    assert any(line.startswith("ok: design power flow: Finished") for line in lines), lines
    assert designer.result() is not None and designer.result().converged
