"""Main-window layout: side panel, bottom panel states, presets and detaching."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Q_ARG, QMetaObject, QObject  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.__main__ import create_engine  # noqa: E402
from tests.ui.mouse import find_item  # noqa: E402


def _click(window: QObject, name: str) -> None:
    QMetaObject.invokeMethod(find_item(window, name), "clicked")


def _detached(window: QObject) -> QObject:
    """The detached-panel window of this main window (earlier tests leave theirs)."""
    windows = [
        w
        for w in QGuiApplication.topLevelWindows()
        if w.objectName() == "detachedBottomWindow" and w.transientParent() == window
    ]
    assert len(windows) == 1
    return windows[0]


def _close(engine: QObject, window: QObject) -> None:
    for name in ("Simulator", "DiagramController", "GridController"):
        engine.singletonInstance("QtSpice", name).shutdown()
    window.close()
    engine.deleteLater()


def test_side_panel_hides_and_the_strip_still_switches_views(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    try:
        side = window.findChild(QObject, "sidePanel")
        strip = window.findChild(QObject, "sideStrip")
        left = window.findChild(QObject, "leftTabs")
        assert side.property("visible") and not strip.property("visible")

        _click(window, "hideSidePanelButton")
        qtbot.waitUntil(lambda: strip.property("visible"))
        assert not side.property("visible")
        _click(window, "sideStrip_2")
        assert left.property("currentIndex") == 2  # Schematic; the canvas follows

        _click(window, "showSidePanelButton")
        qtbot.waitUntil(lambda: side.property("visible"))
        assert not strip.property("visible")
    finally:
        _close(engine, window)


def test_bottom_panel_collapses_maximizes_and_restores(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    try:
        top = window.findChild(QObject, "topArea")
        holder = window.findChild(QObject, "bottomHolder")
        bottom = window.findChild(QObject, "bottomTabs")
        qtbot.wait(100)
        normal = holder.property("height")
        assert normal > 120

        _click(window, "collapseBottomButton")
        qtbot.waitUntil(lambda: holder.property("height") < 60)
        assert window.property("bottomState") == "collapsed"
        # a run that shows a bottom tab opens the panel again
        QMetaObject.invokeMethod(window, "showBottom", Q_ARG("QVariant", 2))
        assert bottom.property("currentIndex") == 2
        qtbot.waitUntil(lambda: abs(holder.property("height") - normal) < 2)

        _click(window, "maximizeBottomButton")
        qtbot.waitUntil(lambda: not top.property("visible"))
        qtbot.waitUntil(lambda: holder.property("height") > normal + 100)
        _click(window, "maximizeBottomButton")
        qtbot.waitUntil(lambda: top.property("visible"))
        qtbot.waitUntil(lambda: abs(holder.property("height") - normal) < 2)
    finally:
        _close(engine, window)


def test_layout_presets(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    try:
        holder = window.findChild(QObject, "bottomHolder")
        side = window.findChild(QObject, "sidePanel")
        top = window.findChild(QObject, "topArea")

        _click(window, "layout_edit")
        assert window.property("bottomState") == "collapsed"
        qtbot.waitUntil(lambda: holder.property("height") < 60)
        _click(window, "layout_balanced")
        qtbot.wait(100)
        balanced = holder.property("height")
        _click(window, "layout_results")
        qtbot.waitUntil(lambda: holder.property("height") > balanced)
        assert not side.property("visible")
        _click(window, "layout_plot")
        qtbot.waitUntil(lambda: not top.property("visible"))
        _click(window, "layout_balanced")
        qtbot.waitUntil(lambda: top.property("visible") and side.property("visible"))
        assert window.property("bottomState") == "normal"
    finally:
        _close(engine, window)


def test_bottom_panel_detaches_and_docks(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    try:
        holder = window.findChild(QObject, "bottomHolder")
        detach = find_item(window, "detachBottomButton")
        detached = _detached(window)
        assert not detached.isVisible()

        QMetaObject.invokeMethod(detach, "clicked")
        qtbot.waitUntil(lambda: detached.isVisible())
        assert not holder.property("visible")
        # the same panel (not a copy) is now drawn in the detached window
        assert find_item(detached, "bottomPanel") is window.findChild(QObject, "bottomPanel")
        assert find_item(detached, "consoleView") is not None
        assert detach.property("text") == "Dock"

        detached.close()  # closing the window docks the panel
        qtbot.waitUntil(lambda: holder.property("visible"))
        assert window.property("bottomState") == "normal"
        assert find_item(window, "bottomPanel").parentItem() is holder
        assert not detached.isVisible()
    finally:
        _close(engine, window)
