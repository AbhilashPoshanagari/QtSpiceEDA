"""Spike D: drag a trace name from one pane header into another pane (Drag/DropArea).

Throwaway.
Run:        .venv/Scripts/python.exe scratch/spike_d_dnd/run.py
Self-test:  .venv/Scripts/python.exe scratch/spike_d_dnd/run.py --selftest
            (drives the mouse with QTest; works with QT_QPA_PLATFORM=offscreen)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem, QQuickView
from PySide6.QtTest import QTest

HERE = Path(__file__).resolve().parent


def find(root: QQuickItem, name: str) -> QQuickItem:
    todo = [root]
    while todo:
        item = todo.pop(0)
        if item.objectName() == name:
            return item
        todo.extend(item.childItems())
    raise LookupError(name)


def centre(item: QQuickItem) -> QPoint:
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(p.x()), round(p.y()))


def drag(view: QQuickView, start: QPoint, end: QPoint, ctrl: bool = False) -> None:
    mods = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    QTest.mouseMove(view, start)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, mods, start)
    for i in range(1, 21):
        p = start + (end - start) * (i / 20)
        QTest.mouseMove(view, p)  # QTest.mouseMove carries no modifiers; release does
        QTest.qWait(5)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, mods, end)
    QTest.qWait(30)


def selftest(view: QQuickView) -> int:
    root = view.rootObject()

    def traces() -> list[str]:
        return root.paneTraces().toVariant()

    steps = [
        ("move V(out) 0 -> 2", "V(out)", "pane2", False, ["V(in)", "I(R1)", "V(out)"]),
        ("copy I(R1) 1 -> 0 (Ctrl)", "I(R1)", "pane0", True, ["V(in),I(R1)", "I(R1)", "V(out)"]),
        ("move V(in) 0 -> 1", "V(in)", "pane1", False, ["I(R1)", "I(R1),V(in)", "V(out)"]),
        ("drop on own pane = no-op", "V(out)", "pane2", False, ["I(R1)", "I(R1),V(in)", "V(out)"]),
    ]
    failed = 0
    print("start:", traces())
    for title, trace, target, ctrl, expected in steps:
        label = find(root, f"trace_{trace}")
        pane = find(root, target)
        drag(view, centre(label), centre(pane), ctrl)
        got = traces()
        ok = got == expected
        failed += not ok
        print(
            f"{'ok  ' if ok else 'FAIL'} {title}: {got}",
            f"(last action: {root.property('lastAction')})",
        )
    return 1 if failed else 0


def main() -> int:
    app = QGuiApplication(sys.argv)
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setSource(QUrl.fromLocalFile(str(HERE / "dnd.qml")))
    if view.status() == QQuickView.Status.Error:
        for err in view.errors():
            print(err.toString())
        return 1
    view.resize(900, 600)
    view.setTitle("Spike D - drag trace names between panes")
    view.show()
    if "--selftest" in sys.argv:
        QTest.qWaitForWindowExposed(view)
        return selftest(view)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
