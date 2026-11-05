"""S11: probing the schematic after a run (needs ngspice)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QSettings, Qt
from PySide6.QtTest import QTest

from bridge.schematic_controller import SchematicController
from bridge.simulator import Simulator
from bridge.waveform_model import WaveformModel
from core.schematic.model import Schematic, TextItem
from tests.ui.mouse import click, find_item
from tests.ui.qml_harness import qml_scene

pytestmark = pytest.mark.ngspice

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TOL = 4.0


def rc(card: str = ".tran 10u 2m") -> Schematic:
    data = json.loads((FIXTURES / "schematics" / "rc" / "rc.json").read_text("utf-8"))
    schematic = Schematic.from_dict(data)[0]
    schematic.texts = [TextItem(-96, 176, card)]
    schematic.instances[0].attrs = {"Value": "PULSE(0 5 0 1u 1u 1 2)"}
    return schematic


class App:
    def __init__(self, tmp_path: Path) -> None:
        settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
        self.ctl = SchematicController(settings=settings)
        self.sim = Simulator()
        self.waves = WaveformModel()
        self.sim.attachWaveforms(self.waves)
        self.ctl.attachSimulator(self.sim)
        self.ctl.attachWaveforms(self.waves)

    def run(self, qtbot) -> None:
        with qtbot.waitSignal(self.sim.resultReady, timeout=20_000):
            assert self.ctl.run() is True

    def visible(self) -> list[str]:
        return [t["name"] for t in self.waves.property("traces") if t["visible"]]

    def data(self, name: str) -> np.ndarray:
        """A trace's values as the waveform model holds them (all points)."""
        return self.waves._traces[name].y


@pytest.fixture
def app(qapp: object, tmp_path: Path, qtbot) -> Iterator[App]:
    a = App(tmp_path)
    a.ctl.load_schematic(rc())
    a.run(qtbot)
    yield a
    a.sim.shutdown()


def test_after_a_run_probing_is_ready_and_no_trace_shows(app: App) -> None:
    assert app.ctl.property("probeReady") is True
    names = app.waves.property("traceNames")
    assert {"V(in)", "V(out)", "I(v1)", "I(r2)", "I(c1)"} <= set(names)  # savecurrents
    assert app.visible() == []
    assert ".options savecurrents" in app.sim.property("sentDeck")


def test_click_a_wire_shows_its_voltage(app: App) -> None:
    assert app.ctl.probeKind(100, 112, TOL, False) == "V"
    assert app.ctl.probeClick(100, 112, TOL, False) == "V(in)"
    assert app.visible() == ["V(in)"]
    assert app.ctl.probeClick(304, 128, TOL, False) == "V(out)"  # the OUT wire above C1
    assert app.ctl.probeClick(70, 112, TOL, False) == "V(in)"  # the IN label


def test_click_a_part_shows_its_current(app: App) -> None:
    assert app.ctl.probeKind(192, 112, TOL, False) == "I"
    assert app.ctl.probeClick(192, 112, TOL, False) == "I(r2)"
    i = app.data("I(r2)")
    v = app.data("V(in)") - app.data("V(out)")
    np.testing.assert_allclose(i, v / 10e3, atol=1e-9)


def test_click_a_pin_shows_the_current_into_it(app: App) -> None:
    assert app.ctl.probeClick(304, 208, TOL, False) == "-I(c1)"  # C1 pin B
    np.testing.assert_allclose(app.data("-I(c1)"), -app.data("I(c1)"))
    assert app.ctl.probeClick(144, 112, TOL, False) == "I(r2)"  # R2 pin A


def test_drag_from_net_to_net_shows_the_difference(app: App) -> None:
    assert app.ctl.probeBetween(100, 112, 304, 128, TOL) == "V(in,out)"
    np.testing.assert_allclose(app.data("V(in,out)"), app.data("V(in)") - app.data("V(out)"))
    assert "same net" in app.ctl.probeBetween(100, 112, 64, 140, TOL)


def test_alt_click_a_part_shows_its_power(app: App) -> None:
    assert app.ctl.probeKind(192, 112, TOL, True) == "P"
    name = app.ctl.probeClick(192, 112, TOL, True)
    assert name == "V(in,out)*I(r2)"
    expected = (app.data("V(in)") - app.data("V(out)")) * app.data("I(r2)")
    np.testing.assert_allclose(app.data(name), expected, atol=1e-12)
    assert np.all(app.data(name) >= -1e-12)  # a resistor only dissipates


def test_probed_traces_come_back_on_the_next_run(app: App, qtbot) -> None:
    app.ctl.probeClick(100, 112, TOL, False)
    app.ctl.probeBetween(100, 112, 304, 128, TOL)
    app.ctl.setAttributes(2, "R2", {"Value": "20k"})
    assert app.ctl.property("probeReady") is False  # the sheet changed
    assert app.ctl.probeClick(100, 112, TOL, False) == ""
    app.run(qtbot)
    assert app.ctl.property("probeReady") is True
    assert sorted(app.visible()) == ["V(in)", "V(in,out)"]


def test_a_netlist_run_ends_probing(app: App, qtbot) -> None:
    with qtbot.waitSignal(app.sim.resultReady, timeout=20_000):
        assert app.sim.run("rc\nV1 a 0 1\nR1 a 0 1k\n.op\n.end\n")
    assert app.ctl.property("probeReady") is False


def test_op_probes_report_the_value(qapp: object, tmp_path: Path, qtbot) -> None:
    a = App(tmp_path)
    try:
        s = rc(".op")
        s.instances[0].attrs = {"Value": "5"}
        a.ctl.load_schematic(s)
        a.run(qtbot)
        assert a.ctl.probeClick(100, 112, TOL, False) == "V(in) = 5 V"
        assert a.ctl.property("probeMessage") == "V(in) = 5 V"
    finally:
        a.sim.shutdown()


def test_probing_a_subcircuit_pin_says_why_not(app: App, qtbot) -> None:
    s = app.ctl.schematic()
    assert app.ctl.probeClick(64, 288, TOL, False) == "that is ground (0 V)"
    assert s == app.ctl.schematic()


# ---------------------------------------------------------------- with the mouse

SCENE = """
SchematicCanvas { objectName: "canvas"; anchors.fill: parent }
"""


@pytest.fixture
def canvas(qapp: object, tmp_path: Path, qtbot) -> Iterator[tuple[QObject, QObject, QObject]]:
    with qml_scene(tmp_path, SCENE, 1000, 700) as (engine, window):
        ctl = engine.singletonInstance("QtSpice", "SchematicController")
        sim = engine.singletonInstance("QtSpice", "Simulator")
        waves = engine.singletonInstance("QtSpice", "WaveformModel")
        sim.attachWaveforms(waves)
        ctl.attachSimulator(sim)
        ctl.attachWaveforms(waves)
        ctl.load_schematic(rc())
        item = find_item(window, "canvas")
        qtbot.wait(20)
        item.zoomToFit()
        with qtbot.waitSignal(sim.resultReady, timeout=20_000):
            assert ctl.run()
        qtbot.wait(20)
        yield window, item, ctl
        ctl.load_schematic(None)


def at(canvas_item: QObject, x: float, y: float) -> QPoint:
    viewport = find_item(canvas_item, "canvasViewport")
    zoom = canvas_item.property("zoom")
    p = viewport.mapToScene(
        QPointF(canvas_item.property("panX") + x * zoom, canvas_item.property("panY") + y * zoom)
    )
    return QPoint(round(p.x()), round(p.y()))


def test_canvas_switches_to_probe_mode_and_shows_a_badge(canvas, qtbot) -> None:
    window, item, ctl = canvas
    assert item.property("mode") == "probe"
    QTest.mouseMove(window, at(item, 192, 112))
    qtbot.wait(20)
    assert item.property("probeBadge") == "I"
    assert find_item(item, "probeBadge").property("visible") is True
    QTest.mouseMove(window, at(item, 100, 112))
    qtbot.wait(20)
    assert item.property("probeBadge") == "V"


def test_canvas_click_and_drag_probe(canvas, qtbot) -> None:
    window, item, ctl = canvas
    with qtbot.waitSignal(ctl.probed):
        click(window, at(item, 100, 112))
    assert ctl.property("probeMessage") == "V(in)"
    start, end = at(item, 100, 112), at(item, 304, 128)
    QTest.mouseMove(window, start)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for k in range(1, 9):
        QTest.mouseMove(
            window,
            QPoint(
                start.x() + (end.x() - start.x()) * k // 8,
                start.y() + (end.y() - start.y()) * k // 8,
            ),
        )
        qtbot.wait(5)
    assert item.property("probeBadge") == "V(a,b)"
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    qtbot.wait(20)
    assert ctl.property("probeMessage") == "V(in,out)"
    click(window, at(item, 192, 112), Qt.KeyboardModifier.AltModifier)
    assert ctl.property("probeMessage") == "V(in,out)*I(r2)"


def test_editing_ends_probe_mode(canvas, qtbot) -> None:
    window, item, ctl = canvas
    ctl.addGround(500, 500)
    qtbot.wait(10)
    assert item.property("mode") == "select"
    assert find_item(item, "probeButton").property("enabled") is False


def test_ac_part_currents_are_not_saved_by_ngspice(qapp: object, tmp_path: Path, qtbot) -> None:
    a = App(tmp_path)
    try:
        a.ctl.load_schematic(rc(".ac dec 10 1 1meg"))
        a.run(qtbot)
        names = a.waves.property("traceNames")
        assert "I(r2)" not in names and "V(out)" in names  # empty vectors are not traces
        message = a.ctl.probeClick(192, 112, TOL, False)
        assert message == "I(r2): ngspice does not save I(r2) in a .ac analysis"
        assert a.ctl.probeClick(100, 112, TOL, False) == "V(in)"
        assert a.ctl.probeBetween(100, 112, 304, 128, TOL) == "V(in,out)"
        assert a.ctl.probeClick(64, 160, TOL, False) == "I(v1)"  # V1 pin +: a branch current
    finally:
        a.sim.shutdown()
