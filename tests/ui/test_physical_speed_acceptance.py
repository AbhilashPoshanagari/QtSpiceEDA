"""U1-U4 milestone (basic level), in the real main window with the mouse.

The C3 speed-control example is placed from the library (Signal, Physical >
Rotational and Physical > Sensors and sources), wired port to port (physical wires
between the round ports, signal wires between the triangles) and set up in the
properties panel with SPICE numbers (J = 10m, b = 1m). Without a reference block Run
does not start and names the block; with one, Run plots the shaft speed, shown in rpm,
which settles at kp / (kp + b) * w_set.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from app.__main__ import create_engine
from tests.ui.mouse import canvas_point, click, drag, find_item, find_items, item_center, key
from tests.ui.speed_loop import KP, W_SET, B, J, steady_speed

RPM = 60.0 / (2.0 * math.pi)


def _scroll_into_view(item) -> None:
    """Scroll the library's Flickable so ``item`` is inside its viewport."""
    flick = item.parentItem()
    while flick is not None and flick.property("contentY") is None:
        flick = flick.parentItem()
    if flick is None:
        return
    top = item.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
    height = flick.height()
    content = flick.property("contentHeight")
    flick.setProperty("contentY", max(0.0, min(top - 20, content - height)))


@pytest.mark.ngspice
def test_build_c3_with_the_mouse_run_and_plot_rpm(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    ctl = engine.singletonInstance("QtSpice", "DiagramController")
    wave = engine.singletonInstance("QtSpice", "WaveformController")
    try:
        window.requestActivate()
        qtbot.waitUntil(lambda: window.isActive(), timeout=2000)
        window.findChild(QObject, "leftTabs").setProperty("currentIndex", 1)
        qtbot.wait(100)
        canvas = find_item(window, "blockCanvas")
        library = find_item(window, "blockLibrary")
        canvas.setProperty("zoom", 0.6)
        canvas.setProperty("panX", 20)
        canvas.setProperty("panY", 20)
        qtbot.wait(50)

        def at(x: float, y: float) -> QPoint:
            return canvas_point(canvas, x, y)

        def row(block_id: str) -> dict:
            return next(r for r in ctl.blocks.rows() if r["blockId"] == block_id)

        def port(address: str) -> QPoint:
            block_id, name = address.split(".")
            r = row(block_id)
            p = next(
                p
                for kind in ("conserving", "inputs", "outputs")
                for p in r[kind]
                if p["name"] == name
            )
            return at(r["blockX"] + p["x"], r["blockY"] + p["y"])

        def open_group(group: str) -> None:
            header = find_item(library, "group_" + group)
            _scroll_into_view(header)
            qtbot.wait(30)
            click(window, item_center(header))
            qtbot.wait(50)

        def place(name: str, x: float, y: float) -> str:
            tile = next(
                t
                for t in find_items(library, "blockTile")
                if t.property("blockName") == name and t.isVisible()
            )
            _scroll_into_view(tile)
            qtbot.wait(30)
            before = ctl.blocks.rowCount()
            # The drop puts the block's top-left 30 units up and left of the cursor.
            drag(window, item_center(tile), at(x + 30, y + 30), steps=20)
            qtbot.waitUntil(lambda: ctl.blocks.rowCount() == before + 1, timeout=2000)
            return ctl.blocks.rows()[-1]["blockId"]

        def set_field(block_id: str, setting: str, text: str) -> None:
            r = row(block_id)
            click(window, at(r["blockX"] + r["blockWidth"] / 2, r["blockY"] + r["blockHeight"] / 2))
            qtbot.wait(30)
            field = find_item(window, "setting_" + setting)
            click(window, item_center(field))
            key(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            for ch in text:  # keyClicks takes widgets only, not a QWindow
                QTest.keyClick(window, ch)
            key(window, Qt.Key.Key_Return)

        # ---- place: signal blocks, then the physical groups one at a time
        setpoint = place("Constant", 40, 560)
        error = place("Sum", 220, 560)
        gain = place("Gain", 400, 560)
        scope = place("Scope", 620, 560)
        open_group("Physical/Rotational")
        inertia = place("Inertia", 260, 200)
        damper = place("RotationalDamper", 260, 360)
        open_group("Physical/Rotational")  # fold it again
        open_group("Physical/Sensors and sources")
        solver = place("SolverConfiguration", 40, 40)
        motor = place("ControlledTorqueSource", 520, 200)
        sensor = place("AngularVelocitySensor", 520, 360)

        # ---- wire: physical ports (no direction), then signals output -> input
        for a, b in (
            (f"{inertia}.A", f"{damper}.A"),
            (f"{motor}.A", f"{inertia}.A"),
            (f"{sensor}.A", f"{inertia}.A"),
            (f"{motor}.B", f"{damper}.B"),
            (f"{sensor}.B", f"{damper}.B"),
            (f"{solver}.port", f"{damper}.B"),
        ):
            before = len(ctl.diagram.physical_wires)
            drag(window, port(a), port(b), steps=15)
            assert len(ctl.diagram.physical_wires) == before + 1, (a, b, ctl.property("hint"))
        for src, dst in (
            (f"{setpoint}.y", f"{error}.a"),
            (f"{sensor}.w", f"{error}.b"),
            (f"{error}.y", f"{gain}.u"),
            (f"{gain}.y", f"{motor}.tau"),
            (f"{gain}.y", f"{scope}.u"),
        ):
            before = len(ctl.diagram.wires)
            drag(window, port(src), port(dst), steps=15)
            assert len(ctl.diagram.wires) == before + 1, (src, dst, ctl.property("hint"))
        assert ctl.property("junctions")  # inertia.A and damper.B join several wires

        # ---- settings with units and SPICE numbers
        set_field(inertia, "J", "10m")
        set_field(damper, "b", "1m")
        set_field(setpoint, "value", str(W_SET))
        set_field(error, "signs", "+-")
        assert ctl.diagram.effective_settings(inertia)["J"] == pytest.approx(J)
        assert ctl.diagram.effective_settings(damper)["b"] == pytest.approx(B)
        assert ctl.diagram.effective_settings(gain)["k"] == KP
        r = row(damper)
        click(window, at(r["blockX"] + r["blockWidth"] / 2, r["blockY"] + r["blockHeight"] / 2))
        qtbot.waitUntil(
            lambda: find_item(window, "caption_b").property("text") == "b [N*m*s/rad]",
            timeout=2000,
        )
        end_field = find_item(canvas, "tEndField")
        end_field.setProperty("text", "0.2")

        # ---- no reference: Run does not start and names the block
        status = find_item(canvas, "canvasStatus")
        click(window, item_center(find_item(canvas, "diagramRunButton")))
        qtbot.wait(50)
        assert ctl.property("running") is False
        text = status.property("text")
        assert text.startswith("Not started") and "reference" in text.lower()
        flagged = [r["blockId"] for r in ctl.blocks.rows() if r["hasError"]]
        assert flagged and any(b in text for b in flagged)
        bottom = window.findChild(QObject, "bottomTabs")
        assert bottom.property("currentIndex") == 2  # the Console has the details
        assert "Physical network check failed" in ctl.property("consoleText")

        # ---- add the reference and run
        open_group("Physical/Sensors and sources")  # fold
        open_group("Physical/Rotational")
        ref = place("MechanicalRotationalReference", 260, 40)
        drag(window, port(f"{ref}.R"), port(f"{damper}.B"), steps=15)
        assert len(ctl.diagram.physical_wires) == 7, ctl.property("hint")
        with qtbot.waitSignal(ctl.resultReady, timeout=60_000):
            click(window, item_center(find_item(canvas, "diagramRunButton")))
        assert not any(r["hasError"] for r in ctl.blocks.rows())
        assert bottom.property("currentIndex") == 0  # Waveforms
        assert ctl.property("consoleText").splitlines()[0].startswith("Combined run")

        # ---- plot the shaft speed in its own pane, then show it in rpm
        shaft = f"{inertia}.A.w"
        assert wave.property("hasData")
        pane = wave.addPane(0, False)
        dialog = window.findChild(QObject, "addTraceDialog")
        dialog.openFor(pane)
        qtbot.waitUntil(lambda: dialog.property("opened"), timeout=2000)
        qtbot.wait(50)
        content = dialog.property("contentItem")
        click(window, item_center(find_item(content, "vector_" + shaft)))
        click(window, item_center(find_item(dialog.property("footer"), "plotButton")))
        qtbot.waitUntil(lambda: not dialog.property("opened"), timeout=2000)
        panes = wave.property("panes")
        assert [t["name"] for t in panes[pane]["traces"]] == [shaft]
        assert panes[pane]["yLabel"] == "[rad/s]"

        click(window, item_center(find_item(window, "displayUnitsButton")))
        qtbot.waitUntil(lambda: wave.property("displayUnits") is True, timeout=2000)
        assert wave.property("panes")[pane]["yLabel"] == "[rpm]"
        shown = np.asarray(wave._store.raw(shaft))  # noqa: SLF001 - what the pane draws
        assert shown[-1] == pytest.approx(steady_speed() * RPM, rel=0.01)
        assert "rpm" in wave.formatY(pane, float(shown[-1]))
    finally:
        wave.setProperty("displayUnits", False)
        ctl.shutdown()
        engine.singletonInstance("QtSpice", "Simulator").shutdown()
        window.close()
        engine.deleteLater()
