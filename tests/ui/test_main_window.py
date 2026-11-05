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
        assert window.findChild(QObject, name) is not None, name
    # Phase 2 enables Open and Check; the rest arrive in later phases.
    for name, enabled in (
        ("openButton", True),
        ("checkButton", True),
        ("saveButton", False),
        ("runButton", False),
        ("stopButton", False),
    ):
        assert window.findChild(QObject, name).property("enabled") is enabled, name
    assert window.findChild(QObject, "netlistEditor") is not None

    left = window.findChild(QObject, "leftTabs")
    bottom = window.findChild(QObject, "bottomTabs")
    assert left.property("count") == 2
    assert bottom.property("count") == 3
    window.close()
    engine.deleteLater()
