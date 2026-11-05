from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402

from app.__main__ import create_engine  # noqa: E402


def test_main_window_loads(qapp: object) -> None:
    QQuickStyle.setStyle("Fusion")
    engine = create_engine()
    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load"
    window = roots[0]

    for name in ("openButton", "saveButton", "checkButton", "runButton", "stopButton"):
        button = window.findChild(QObject, name)
        assert button is not None, name
        assert button.property("enabled") is False, name

    left = window.findChild(QObject, "leftTabs")
    bottom = window.findChild(QObject, "bottomTabs")
    assert left.property("count") == 2
    assert bottom.property("count") == 3
    window.close()
    engine.deleteLater()
