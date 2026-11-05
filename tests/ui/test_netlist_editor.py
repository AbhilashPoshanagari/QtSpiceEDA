"""NetlistEditor.qml inside the real main window: pasting text shows marks on the right lines."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, Qt, QUrl
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle

from app.__main__ import create_engine
from tests.netlists import BROKEN, BROKEN_EXPECTED, GOOD_FILES, INCLUDE_FILE


@pytest.fixture(scope="module")
def editor(qapp: object) -> Iterator[QObject]:
    QQuickStyle.setStyle("Fusion")
    engine = create_engine()
    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load"
    window = roots[0]
    found = window.findChild(QObject, "netlistEditor")
    assert found is not None
    yield found
    window.close()
    engine.deleteLater()


def marked_lines(editor: QQuickItem) -> list[int]:
    """Lines with a visible margin mark. Repeater delegates are visual children only,
    so walk childItems() rather than QObject.findChildren()."""
    lines: list[int] = []
    pending = list(editor.childItems())
    while pending:
        item = pending.pop()
        if item.objectName() == "diagnosticMark" and item.isVisible():
            lines.append(item.property("line"))
        pending.extend(item.childItems())
    return sorted(lines)


def call(item: QObject, function: str, argument: object) -> object:
    """Call a QML function on ``item`` and return its result."""
    return QMetaObject.invokeMethod(
        item, function, Qt.DirectConnection, Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", argument)
    )


def paste(qtbot, editor: QObject, text: str) -> None:
    """Set the text and wait for the debounced check to run."""
    editor.setProperty("text", "")
    qtbot.wait(10)
    editor.setProperty("text", text)
    # The check runs 300 ms after the last edit; wait for its result to be drawn.
    qtbot.wait(450)


@pytest.mark.parametrize("name", sorted(BROKEN_EXPECTED))
def test_broken_example_marks_every_error_line(qtbot, editor: QObject, name: str) -> None:
    paste(qtbot, editor, (BROKEN / name).read_text())
    expected = sorted({line for line, _ in BROKEN_EXPECTED[name]})
    assert marked_lines(editor) == expected
    assert editor.property("errorCount") == len(BROKEN_EXPECTED[name])
    for line, fragment in BROKEN_EXPECTED[name]:
        message = call(editor, "messagesAt", line)
        assert fragment in message


@pytest.mark.parametrize("path", GOOD_FILES, ids=lambda p: p.name)
def test_good_example_has_no_marks(qtbot, editor: QObject, path: Path) -> None:
    paste(qtbot, editor, path.read_text())
    assert marked_lines(editor) == []
    assert editor.property("errorCount") == 0
    assert editor.property("warningCount") == 0


def test_load_url_reads_file_and_checks(qtbot, editor: QObject) -> None:
    url = QUrl.fromLocalFile(str(INCLUDE_FILE))
    assert call(editor, "loadUrl", url) is True
    assert editor.property("text") == INCLUDE_FILE.read_text()
    qtbot.wait(50)
    assert marked_lines(editor) == []
    assert editor.property("errorCount") == 0


def find_item(root: QQuickItem, name: str) -> QQuickItem:
    pending = list(root.childItems())
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f"no item {name!r}")


def test_title_line_quick_fix(qtbot, editor: QObject) -> None:
    """O1: line 1 looks like an element -> warning + "Insert title line", undoable."""
    netlist = "R1 1 0 1k\nV1 1 0 DC 1\nR2 1 0 1k\n.op\n.end\n"
    paste(qtbot, editor, netlist)
    bar = find_item(editor, "quickFixBar")
    assert bar.isVisible()
    assert marked_lines(editor) == [1]
    assert editor.property("warningCount") == 1
    button = find_item(editor, "insertTitleButton")
    QMetaObject.invokeMethod(button, "clicked", Qt.DirectConnection)
    qtbot.wait(450)
    assert editor.property("text") == "* Title\n" + netlist
    assert not bar.isVisible()
    assert editor.property("warningCount") == 0
    text_area = find_item(editor, "editorText")
    QMetaObject.invokeMethod(text_area, "undo", Qt.DirectConnection)
    qtbot.wait(450)
    assert editor.property("text") == netlist
    assert bar.isVisible()
