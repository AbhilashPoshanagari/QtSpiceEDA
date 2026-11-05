"""FunctionEditor and BlockLibrary inside the real main window (the Phase 4 demo).

Writing an @block function in the editor shows its block in the library; a syntax
error is marked on the right line.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle

from app.__main__ import create_engine

ADD_SUB = (
    '@block(shape="circle", outputs=["sum", "diff"])\ndef add_sub(a, b): return a + b, a - b\n'
)


@pytest.fixture(scope="module")
def window(qapp: object) -> Iterator[QObject]:
    QQuickStyle.setStyle("Fusion")
    engine = create_engine()
    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load"
    win = roots[0]
    win.findChild(QObject, "leftTabs").setProperty("currentIndex", 1)
    yield win
    win.close()
    engine.deleteLater()


@pytest.fixture
def editor(window: QObject, qtbot) -> Iterator[QObject]:
    found = window.findChild(QObject, "functionEditor")
    assert found is not None
    QMetaObject.invokeMethod(found, "newFunction", Qt.DirectConnection)
    yield found
    # Leave no blocks behind for the next test.
    type_code(qtbot, found, "")


def type_code(qtbot, editor: QObject, code: str) -> None:
    """Set the code and wait for the debounced apply."""
    editor.setProperty("text", code)
    qtbot.wait(450)


def items_named(root: QQuickItem, object_name: str) -> list[QQuickItem]:
    """Visible items with ``object_name``. Repeater delegates are visual children only."""
    found: list[QQuickItem] = []
    pending = list(root.childItems())
    while pending:
        item = pending.pop()
        if item.objectName() == object_name and item.isVisible():
            found.append(item)
        pending.extend(item.childItems())
    return found


def tile(window: QObject, name: str) -> QQuickItem | None:
    library = window.findChild(QQuickItem, "blockLibrary")
    tiles = [t for t in items_named(library, "blockTile") if t.property("blockName") == name]
    return tiles[0] if tiles else None


def ports(tile_item: QQuickItem, kind: str) -> list[str]:
    found = items_named(tile_item, kind)
    return sorted(p.property("portName") for p in found)


def test_add_sub_appears_as_circle_block(window: QObject, editor: QObject, qtbot) -> None:
    type_code(qtbot, editor, ADD_SUB)
    block = tile(window, "add_sub")
    assert block is not None
    assert block.property("shape") == "circle"
    assert block.property("inputNames") == ["a", "b"]
    assert block.property("outputNames") == ["sum", "diff"]
    assert ports(block, "inputPort") == ["a", "b"]
    assert ports(block, "outputPort") == ["diff", "sum"]
    assert editor.property("errorCount") == 0


def test_syntax_error_marked_on_right_line(window: QObject, editor: QObject, qtbot) -> None:
    type_code(qtbot, editor, "@block\ndef f(u):\n    return u +\n")
    marks = [m.property("line") for m in items_named(editor, "diagnosticMark")]
    assert marks == [3]
    assert editor.property("errorCount") == 1
    message = QMetaObject.invokeMethod(
        editor, "messagesAt", Qt.DirectConnection, Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", 3)
    )
    assert "syntax error" in message
    assert tile(window, "f") is None


def test_error_keeps_last_good_block(window: QObject, editor: QObject, qtbot) -> None:
    type_code(qtbot, editor, ADD_SUB)
    type_code(qtbot, editor, ADD_SUB.replace("a - b", "a -"))
    assert editor.property("errorCount") == 1
    assert tile(window, "add_sub") is not None


def test_removed_port_shown_in_status(window: QObject, editor: QObject, qtbot) -> None:
    type_code(qtbot, editor, ADD_SUB)
    type_code(qtbot, editor, '@block(shape="circle", outputs=["sum"])\ndef add_sub(a): return a\n')
    status = window.findChild(QObject, "functionStatus").property("text")
    assert "add_sub: input b" in status
    assert "add_sub: output diff" in status
    assert ports(tile(window, "add_sub"), "inputPort") == ["a"]  # type: ignore[arg-type]


def test_builtins_are_listed(window: QObject, editor: QObject) -> None:
    for name in ("Constant", "Sum", "Integrator", "Scope"):
        assert tile(window, name) is not None, name
    assert tile(window, "Sum").property("shape") == "circle"  # type: ignore[union-attr]
    assert ports(tile(window, "Scope"), "outputPort") == []  # type: ignore[arg-type]


def test_click_user_block_opens_its_code(window: QObject, editor: QObject, qtbot) -> None:
    type_code(qtbot, editor, ADD_SUB)
    QMetaObject.invokeMethod(editor, "newFunction", Qt.DirectConnection)
    assert editor.property("text") == ""
    assert tile(window, "add_sub") is not None  # the new document keeps old blocks
    library = window.findChild(QObject, "blockLibrary")
    library.editBlock.emit("add_sub")
    assert editor.property("text") == ADD_SUB
