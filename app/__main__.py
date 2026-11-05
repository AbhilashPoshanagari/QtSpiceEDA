"""QtSpice entry point: ``python -m app``."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

QML_DIR = Path(__file__).resolve().parents[1] / "ui" / "qml"
MAIN_QML = QML_DIR / "Main.qml"


def create_engine() -> QQmlApplicationEngine:
    """Load Main.qml into a new engine. A QGuiApplication must already exist."""
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.load(MAIN_QML.as_uri())
    return engine


def main() -> int:
    QQuickStyle.setStyle("Fusion")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("QtSpice")
    app.setOrganizationName("QtSpice")
    engine = create_engine()
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
