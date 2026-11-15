"""N3: Tools > Schematic from Netlist in the main window."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject  # noqa: E402

from app.__main__ import create_engine  # noqa: E402
from tests.ui.test_schematic_canvas import rc  # noqa: E402

TESTS = Path(__file__).resolve().parents[1]
XOR = TESTS / "netlists" / "good" / "cmos_xor.cir"
BROKEN = TESTS / "netlists" / "broken" / "unknown_model.cir"


@pytest.fixture
def main(qapp: object) -> Iterator[tuple[QObject, QObject]]:
    engine = create_engine()
    window = engine.rootObjects()[0]
    ctl = engine.singletonInstance("QtSpice", "SchematicController")
    ctl.load_schematic(None)
    try:
        yield window, ctl
    finally:
        engine.singletonInstance("QtSpice", "Simulator").shutdown()
        window.close()
        engine.deleteLater()


def child(window: QObject, name: str) -> QObject:
    obj = window.findChild(QObject, name)
    assert obj is not None, name
    return obj


def draw(window: QObject, text: str) -> None:
    child(window, "editorText").setProperty("text", text)
    child(window, "schematicFromNetlistMenu").triggered.emit()


def instances(ctl: QObject) -> list[str]:
    return [i.name for i in ctl.schematic().instances]


def test_menu_draws_the_netlist_and_shows_the_schematic_tab(main, qtbot) -> None:
    window, ctl = main
    tabs = child(window, "leftTabs")
    assert tabs.property("currentIndex") == 0
    draw(window, XOR.read_text())
    assert len(instances(ctl)) == 11
    assert tabs.property("currentIndex") == 2
    canvas = child(window, "schematicCanvas")  # fitted to the drawing, not 5 %
    qtbot.waitUntil(lambda: canvas.property("zoom") > 0.1, timeout=2000)
    assert child(window, "netlistChoiceDialog").property("opened") is False
    assert child(window, "editorText").property("text") == XOR.read_text()  # untouched


def test_asks_before_replacing_a_sheet(main, qtbot) -> None:
    window, ctl = main
    ctl.load_schematic(rc())
    before = ctl.schematic()
    dialog = child(window, "replaceSheetDialog")

    draw(window, XOR.read_text())
    qtbot.waitUntil(lambda: dialog.property("opened") is True, timeout=2000)
    child(window, "keepSheetButton").clicked.emit()
    qtbot.waitUntil(lambda: dialog.property("visible") is False, timeout=2000)
    assert ctl.schematic() == before

    draw(window, XOR.read_text())
    qtbot.waitUntil(lambda: dialog.property("opened") is True, timeout=2000)
    child(window, "replaceSheetButton").clicked.emit()
    qtbot.waitUntil(lambda: dialog.property("visible") is False, timeout=2000)
    assert len(instances(ctl)) == 11
    ctl.undo()
    assert ctl.schematic() == before


def test_errors_are_listed_and_nothing_is_drawn(main, qtbot) -> None:
    window, ctl = main
    dialog = child(window, "netlistErrorsDialog")
    draw(window, BROKEN.read_text())
    qtbot.waitUntil(lambda: dialog.property("opened") is True, timeout=2000)
    text = child(window, "netlistErrorsText").property("text")
    assert text.startswith("✖ line ") and "fix the netlist errors" in text
    assert ctl.property("sheetEmpty") is True
    assert child(window, "leftTabs").property("currentIndex") == 0
