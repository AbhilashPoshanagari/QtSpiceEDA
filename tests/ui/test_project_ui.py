"""The editors and panels of Main.qml follow the project that is opened."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject, QSettings  # noqa: E402

from app.__main__ import create_engine  # noqa: E402

RC = (Path(__file__).resolve().parents[2] / "examples" / "rc_lowpass.cir").read_text()
CODE = "@block\ndef double(u):\n    return 2 * u\n"


@pytest.fixture
def private_settings(tmp_path: Path) -> Iterator[None]:
    """Keep the recent-files list and trusted projects out of the user's settings."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    yield
    QSettings.setDefaultFormat(QSettings.Format.NativeFormat)


def child(window: QObject, name: str) -> QObject:
    obj = window.findChild(QObject, name)
    assert obj is not None, name
    return obj


def test_open_fills_the_editors(qapp: object, qtbot, tmp_path: Path, private_settings) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    sim = engine.singletonInstance("QtSpice", "Simulator")
    diagram = engine.singletonInstance("QtSpice", "DiagramController")
    projects = engine.singletonInstance("QtSpice", "ProjectManager")
    try:
        assert window.property("title") == "Untitled — QtSpice"
        child(window, "editorText").setProperty("text", RC)
        assert sim.property("netlistText") == RC
        child(window, "functionText").setProperty("text", CODE)
        qtbot.waitUntil(lambda: any(b["name"] == "double" for b in diagram.property("library")))
        assert diagram.addBlock("double", 0, 0)
        child(window, "tEndField").setProperty("text", "0.25")
        assert diagram.property("tEnd") == 0.25
        sim.setProperty("analysisCard", ".tran 2u 5m")
        assert child(window, "tranStep").property("text") == "2u"
        assert window.property("title") == "*Untitled — QtSpice"

        path = tmp_path / "ui.qsp"
        assert projects.saveAs(str(path))
        assert window.property("title") == "ui.qsp — QtSpice"

        assert projects.newProject()
        assert child(window, "editorText").property("text") == ""
        assert child(window, "functionText").property("text") == ""
        assert child(window, "tEndField").property("text") == "1"
        assert diagram.blocks.rowCount() == 0

        assert projects.open(str(path))
        assert child(window, "editorText").property("text") == RC
        assert child(window, "functionText").property("text") == CODE
        assert child(window, "tEndField").property("text") == "0.25"
        assert child(window, "tranStep").property("text") == "2u"
        assert child(window, "analysisPanel").property("followNetlist") is False
        assert diagram.blocks.rowCount() == 1
        assert "Opened ui.qsp" in child(window, "projectStatus").property("text")
        qtbot.wait(400)  # the editors' delayed check and apply change nothing
        assert projects.property("modified") is False
    finally:
        projects.newProject()  # nothing unsaved: the window closes without asking
        sim.shutdown()
        diagram.shutdown()
        window.close()
        engine.deleteLater()
