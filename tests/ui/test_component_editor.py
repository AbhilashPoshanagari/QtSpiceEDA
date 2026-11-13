"""M5: ComponentEditor inside the real main window.

Writing a @physical_component class in the editor shows its block in the library group
"Physical/User"; an error is marked on its line; the library tile opens the code again.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QMetaObject, QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle

from app.__main__ import create_engine
from tests.core.physical.test_user import TC_RESISTOR
from tests.ui.test_function_editor import items_named, tile


@pytest.fixture(scope="module")
def window(qapp: object) -> Iterator[QObject]:
    QQuickStyle.setStyle("Fusion")
    engine = create_engine()
    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load"
    win = roots[0]
    win.findChild(QObject, "leftTabs").setProperty("currentIndex", 1)
    win.findChild(QObject, "editorTabs").setProperty("currentIndex", 1)
    yield win
    win.close()
    engine.deleteLater()


@pytest.fixture
def editor(window: QObject, qtbot) -> Iterator[QObject]:
    found = window.findChild(QObject, "componentEditor")
    assert found is not None
    QMetaObject.invokeMethod(found, "newComponent", Qt.DirectConnection)
    yield found
    type_code(qtbot, found, "")


def type_code(qtbot, editor: QObject, code: str) -> None:
    editor.setProperty("text", code)
    qtbot.wait(450)


def test_component_appears_in_the_user_physical_group(
    window: QObject, editor: QObject, qtbot
) -> None:
    type_code(qtbot, editor, TC_RESISTOR)
    library = window.findChild(QQuickItem, "blockLibrary")
    assert items_named(library, "group_Physical/User")  # the group header is shown
    library.setProperty("openGroups", {"Physical/User": True})  # groups start folded
    qtbot.wait(50)
    block = tile(window, "TCThermalResistor")
    assert block is not None
    assert block.property("inputNames") == []
    status = window.findChild(QObject, "componentStatus")
    assert "TCThermalResistor" in status.property("text")


def test_error_is_marked_on_its_line(window: QObject, editor: QObject, qtbot) -> None:
    code = TC_RESISTOR.replace("th = get_domain(THERMAL)", "th = open('x')")
    type_code(qtbot, editor, code)
    line = code.splitlines().index("        th = open('x')") + 1
    marks = items_named(editor, "componentDiagnosticMark")  # type: ignore[arg-type]
    assert [m.property("line") for m in marks] == [line]
    assert marks[0].property("severity") == "error"


def test_tile_opens_the_component_code(window: QObject, editor: QObject, qtbot) -> None:
    type_code(qtbot, editor, TC_RESISTOR)
    QMetaObject.invokeMethod(editor, "newComponent", Qt.DirectConnection)
    assert editor.property("text") == ""
    tabs = window.findChild(QObject, "editorTabs")
    tabs.setProperty("currentIndex", 0)
    library = window.findChild(QQuickItem, "blockLibrary")
    library.editBlock.emit("TCThermalResistor")  # what tapping a user tile sends
    assert editor.property("text") == TC_RESISTOR
    assert tabs.property("currentIndex") == 1
