"""Load one QML component from ui/qml in a window, for UI tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine

import bridge.simulator  # noqa: F401  registers the QtSpice types
import bridge.waveform_model  # noqa: F401

QML_DIR = Path(__file__).resolve().parents[2] / "ui" / "qml"


@contextmanager
def qml_window(
    tmp_path: Path, component: str, width: int = 1000, height: int = 600
) -> Iterator[tuple[QQmlApplicationEngine, QObject, QObject]]:
    """Yield (engine, window, item) with ``component`` filling a visible window."""
    wrapper = tmp_path / "Harness.qml"
    wrapper.write_text(
        f"""import QtQuick
import QtQuick.Controls
import "{QML_DIR.as_uri()}"
ApplicationWindow {{
    width: {width}; height: {height}; visible: true
    {component} {{ objectName: "underTest"; anchors.fill: parent }}
}}
""",
        encoding="utf-8",
    )
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.load(QUrl.fromLocalFile(str(wrapper)))
    roots = engine.rootObjects()
    assert roots, f"{component} failed to load"
    window = roots[0]
    item = window.findChild(QObject, "underTest")
    try:
        yield engine, window, item
    finally:
        simulator = engine.singletonInstance("QtSpice", "Simulator")
        if simulator is not None:
            simulator.shutdown()
        window.close()
        engine.deleteLater()
