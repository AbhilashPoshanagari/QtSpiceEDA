"""Load one QML component from ui/qml in a window, for UI tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine

import bridge.diagram_controller  # noqa: F401  registers the QtSpice QML types
import bridge.grid_controller  # noqa: F401
import bridge.grid_diagram_controller  # noqa: F401
import bridge.project_manager  # noqa: F401
import bridge.schematic_controller  # noqa: F401
import bridge.simulator  # noqa: F401
import bridge.waveform_controller  # noqa: F401
import bridge.waveform_model  # noqa: F401

QML_DIR = Path(__file__).resolve().parents[2] / "ui" / "qml"


@contextmanager
def qml_window(
    tmp_path: Path, component: str, width: int = 1000, height: int = 600
) -> Iterator[tuple[QQmlApplicationEngine, QObject, QObject]]:
    """Yield (engine, window, item) with ``component`` filling a visible window."""
    body = f'{component} {{ objectName: "underTest"; anchors.fill: parent }}'
    with qml_scene(tmp_path, body, width, height) as (engine, window):
        yield engine, window, window.findChild(QObject, "underTest")


@contextmanager
def qml_scene(
    tmp_path: Path, body: str, width: int = 1000, height: int = 600
) -> Iterator[tuple[QQmlApplicationEngine, QObject]]:
    """Yield (engine, window) for an ApplicationWindow holding the QML ``body``."""
    wrapper = tmp_path / "Harness.qml"
    wrapper.write_text(
        f"""import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "{QML_DIR.as_uri()}"
import "{(QML_DIR / "grid").as_uri()}"
ApplicationWindow {{
    width: {width}; height: {height}; visible: true
    {body}
}}
""",
        encoding="utf-8",
    )
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.load(QUrl.fromLocalFile(str(wrapper)))
    roots = engine.rootObjects()
    assert roots, "the test scene failed to load"
    window = roots[0]
    try:
        yield engine, window
    finally:
        for name in ("Simulator", "DiagramController", "GridController"):
            singleton = engine.singletonInstance("QtSpice", name)
            if singleton is not None:
                singleton.shutdown()
        window.close()
        engine.deleteLater()
