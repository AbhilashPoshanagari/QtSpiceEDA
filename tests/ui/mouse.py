"""Mouse and keyboard helpers for driving QML scenes in UI tests."""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


def find_item(root: QObject, name: str) -> QQuickItem:
    """The first visual descendant called ``name`` (Repeater delegates included)."""
    start = root.contentItem() if hasattr(root, "contentItem") else root
    todo = [start]
    while todo:
        item = todo.pop(0)
        if item.objectName() == name:
            return item
        todo.extend(item.childItems())
    raise LookupError(name)


def find_items(root: QObject, name: str) -> list[QQuickItem]:
    start = root.contentItem() if hasattr(root, "contentItem") else root
    found, todo = [], [start]
    while todo:
        item = todo.pop(0)
        if item.objectName() == name:
            found.append(item)
        todo.extend(item.childItems())
    return found


def item_center(item: QQuickItem) -> QPoint:
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(p.x()), round(p.y()))


def canvas_point(canvas: QQuickItem, x: float, y: float) -> QPoint:
    """Window position of canvas point (x, y)."""
    viewport = find_item(canvas, "canvasViewport")
    zoom = canvas.property("zoom")
    vx = canvas.property("panX") + x * zoom
    vy = canvas.property("panY") + y * zoom
    p = viewport.mapToScene(QPointF(vx, vy))
    return QPoint(round(p.x()), round(p.y()))


def drag(
    window: QObject,
    start: QPoint,
    end: QPoint,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    steps: int = 10,
    before_release=None,
) -> None:
    """Press at ``start``, move to ``end`` in steps, release."""
    QTest.mouseMove(window, start)
    QTest.mousePress(window, button, modifiers, start)
    for i in range(1, steps + 1):
        x = start.x() + (end.x() - start.x()) * i // steps
        y = start.y() + (end.y() - start.y()) * i // steps
        QTest.mouseMove(window, QPoint(x, y))
        QTest.qWait(5)
    if before_release is not None:
        before_release()
    QTest.mouseRelease(window, button, modifiers, end)
    QTest.qWait(10)


def click(
    window: QObject,
    at: QPoint,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, modifiers, at)
    QTest.qWait(10)


def wheel(window: QObject, at: QPoint, delta_y: int) -> None:
    pos = QPointF(at)
    event = QWheelEvent(
        pos,
        window.mapToGlobal(pos),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QGuiApplication.sendEvent(window, event)
    QTest.qWait(10)


def key(window: QObject, k: Qt.Key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(window, k, modifiers)
    QTest.qWait(10)
