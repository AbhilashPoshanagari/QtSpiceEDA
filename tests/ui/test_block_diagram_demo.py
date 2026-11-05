"""The Phase 5b demo in the real main window, with the mouse and keyboard only.

Sine -> Gain(2) -> Integrator -> Scope is placed from the library, wired port to port,
the gain is set in the properties panel, and Run plots (1 - cos 2 pi t) / pi in the
Waveforms tab. Then every edit is undone with Ctrl+Z and redone with Ctrl+Y.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QPoint, Qt

from app.__main__ import create_engine
from tests.ui.mouse import canvas_point, click, drag, find_item, find_items, item_center, key


def test_build_run_undo_redo_with_the_mouse(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    ctl = engine.singletonInstance("QtSpice", "DiagramController")
    waveforms = engine.singletonInstance("QtSpice", "WaveformModel")
    try:
        window.requestActivate()
        qtbot.waitUntil(lambda: window.isActive(), timeout=2000)
        window.findChild(QObject, "leftTabs").setProperty("currentIndex", 1)
        qtbot.wait(100)
        canvas = find_item(window, "blockCanvas")
        assert canvas.isVisible()

        def at(x: float, y: float) -> QPoint:
            return canvas_point(canvas, x, y)

        def row(block_id: str) -> dict:
            return next(r for r in ctl.blocks.rows() if r["blockId"] == block_id)

        def port(address: str, output: bool) -> QPoint:
            block_id, name = address.split(".")
            r = row(block_id)
            p = next(p for p in r["outputs" if output else "inputs"] if p["name"] == name)
            return at(r["blockX"] + p["x"], r["blockY"] + p["y"])

        # Place four blocks from the library.
        names = ("Sine", "Gain", "Integrator", "Scope")
        tiles = {t.property("blockName"): t for t in find_items(window, "blockTile")}
        for i, name in enumerate(names):
            drag(window, item_center(tiles[name]), at(40 + 110 * i, 130), steps=20)
            qtbot.waitUntil(lambda n=i: ctl.blocks.rowCount() == n + 1, timeout=2000)
        assert [r["blockId"] for r in ctl.blocks.rows()] == [
            "Sine1",
            "Gain1",
            "Integrator1",
            "Scope1",
        ]

        # Wire them, output port to input port.
        for src, dst in (
            ("Sine1.y", "Gain1.u"),
            ("Gain1.y", "Integrator1.u"),
            ("Integrator1.y", "Scope1.u"),
        ):
            drag(window, port(src, True), port(dst, False))
        assert [(w.src, w.dst) for w in ctl.diagram.wires] == [
            ("Sine1.y", "Gain1.u"),
            ("Gain1.y", "Integrator1.u"),
            ("Integrator1.y", "Scope1.u"),
        ]

        # Gain k = 2 in the properties panel.
        r = row("Gain1")
        click(window, at(r["blockX"] + r["blockWidth"] / 2, r["blockY"] + r["blockHeight"] / 2))
        field = find_item(window, "setting_k")
        click(window, item_center(field))
        key(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        key(window, Qt.Key.Key_2)
        key(window, Qt.Key.Key_Return)
        assert ctl.diagram.effective_settings("Gain1")["k"] == 2.0

        # Run: the Scope trace appears in the Waveforms tab.
        bottom = window.findChild(QObject, "bottomTabs")
        bottom.setProperty("currentIndex", 2)
        with qtbot.waitSignal(ctl.resultReady, timeout=20_000):
            click(window, item_center(find_item(canvas, "diagramRunButton")))
        assert bottom.property("currentIndex") == 0
        assert waveforms.property("traceNames") == ["Scope1"]
        t = ctl.result.vectors["time"]
        y = ctl.result.vectors["Scope1"]
        np.testing.assert_allclose(y, (1 - np.cos(2 * np.pi * t)) / np.pi, atol=5e-3)
        _, drawn = waveforms.arrays("Scope1", 0.0, 1.0, 400)
        assert len(drawn) > 0 and abs(drawn.max() - 2 / np.pi) < 5e-3

        # Undo every edit with Ctrl+Z, then redo them all with Ctrl+Y.
        click(window, at(700, 500))  # empty canvas: takes focus from the text field
        undone = 0
        while ctl.property("canUndo"):
            key(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            undone += 1
            assert undone < 50
        assert undone == 8  # 4 blocks, 3 wires, 1 setting
        assert ctl.blocks.rowCount() == 0 and ctl.diagram.wires == []
        while ctl.property("canRedo"):
            key(window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        assert ctl.blocks.rowCount() == 4 and len(ctl.diagram.wires) == 3
        assert ctl.diagram.effective_settings("Gain1")["k"] == 2.0
    finally:
        ctl.shutdown()
        engine.singletonInstance("QtSpice", "Simulator").shutdown()
        window.close()
        engine.deleteLater()
