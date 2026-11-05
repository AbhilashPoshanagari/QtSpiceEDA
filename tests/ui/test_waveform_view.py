from __future__ import annotations

import math
from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject
from PySide6.QtQuick import QQuickItem

from tests.ui.fakes import tran_result
from tests.ui.qml_harness import qml_window
from tests.ui.test_waveform_model import ac_result


def _find_item(item: QQuickItem, name: str) -> QQuickItem | None:
    # Items made by a Repeater nested in a delegate have no QObject parent, so
    # findChild misses them; walk the visual tree instead.
    for child in item.childItems():
        if child.objectName() == name:
            return child
        found = _find_item(child, name)
        if found is not None:
            return found
    return None


def _child(root: QObject, name: str) -> QObject:
    obj = root.findChild(QObject, name) or _find_item(root, name)
    assert obj is not None, name
    return obj


def _series(plot: QObject) -> list[dict]:
    return plot.property("seriesState").toVariant()


def _settle(qtbot) -> None:
    qtbot.wait(80)  # refresh timer (16 ms) plus a frame


def test_shows_traces_and_fits(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model = engine.singletonInstance("QtSpice", "WaveformModel")
        model.set_result(tran_result(200_000))
        _settle(qtbot)
        plot = _child(view, "magnitudePlot")
        series = _series(plot)
        assert [s["name"] for s in series] == ["V(out)", "V(in)"]
        width = plot.property("plotWidth")
        assert all(0 < s["count"] <= 2 * round(width) for s in series)
        assert view.property("x0") == 0 and view.property("x1") == pytest.approx(5e-3)
        assert _child(view, "phasePlot").property("visible") is False

        model.setVisible("V(in)", False)
        _settle(qtbot)
        hidden = _series(plot)[1]
        assert hidden["visible"] is False and hidden["count"] == 0


def test_zoom_pan_and_fit(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model = engine.singletonInstance("QtSpice", "WaveformModel")
        model.set_result(tran_result(10_001))
        _settle(qtbot)
        QMetaObject.invokeMethod(view, "zoom", Q_ARG("QVariant", 0.5), Q_ARG("QVariant", 2.5e-3))
        assert view.property("x0") == pytest.approx(1.25e-3)
        assert view.property("x1") == pytest.approx(3.75e-3)
        QMetaObject.invokeMethod(view, "pan", Q_ARG("QVariant", 1e-3))
        assert view.property("x0") == pytest.approx(2.25e-3)
        _settle(qtbot)
        first = _series(_child(view, "magnitudePlot"))[0]
        assert first["count"] > 0
        QMetaObject.invokeMethod(view, "fit")
        assert view.property("x0") == 0 and view.property("x1") == pytest.approx(5e-3)


def test_cursor_readout(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model = engine.singletonInstance("QtSpice", "WaveformModel")
        model.set_result(tran_result(5001))
        _settle(qtbot)
        view.setProperty("cursorA", 1e-3)
        view.setProperty("cursorB", 2e-3)
        assert _child(view, "cursorAx").property("text") == "1 ms"
        assert _child(view, "cursorDx").property("text") == "1 ms"
        a = _child(view, "readout_V(out)_0").property("text")
        b = _child(view, "readout_V(out)_1").property("text")
        assert a == f"{(1 - math.exp(-1)) * 1000:.4g} mV"
        assert b == f"{(1 - math.exp(-2)) * 1000:.4g} mV"


def test_ac_shows_phase_plot(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model = engine.singletonInstance("QtSpice", "WaveformModel")
        model.set_result(ac_result())
        _settle(qtbot)
        assert view.property("isAc") is True
        assert _child(view, "phasePlot").property("visible") is True
        assert view.property("x0") == pytest.approx(0) and view.property("x1") == 5
        assert _series(_child(view, "phasePlot"))[0]["count"] > 0
        view.setProperty("cursorA", math.log10(1 / (2 * math.pi * 1e-3)))
        assert _child(view, "cursorAx").property("text") == "159.2 Hz"
        assert _child(view, "readout_V(out)_0").property("text").startswith("-3.01")
        assert _child(view, "readout_V(out)_3").property("text") == "-45°"


def test_plot_area_geometry_matches_qtgraphs(qapp: object, tmp_path: Path, qtbot) -> None:
    """The cursor line is drawn by QtGraphs; it must land where toPixel() says, or
    mouse dragging and zoom anchoring would be off (GraphsView 6.8 has no plotArea)."""
    with qml_window(tmp_path, "WaveformView") as (engine, window, view):
        model = engine.singletonInstance("QtSpice", "WaveformModel")
        model.set_result(tran_result(1001))
        model.setVisible("V(out)", False)
        model.setVisible("V(in)", False)
        _settle(qtbot)
        view.setProperty("cursorA", 1.7e-3)
        _settle(qtbot)
        plot = _child(view, "magnitudePlot")
        expected = plot.property("plotLeft") + (1.7e-3 / 5e-3) * plot.property("plotWidth")
        origin = plot.mapToItem(window.contentItem(), 0, 0)
        image = window.grabWindow()
        y = int(origin.y() + plot.property("plotTop") + plot.property("plotHeight") / 2)
        dark = [
            x
            for x in range(image.width())
            if image.pixelColor(x, y).lightness() < 110
            and abs(image.pixelColor(x, y).red() - image.pixelColor(x, y).blue()) < 10
        ]
        assert dark, "cursor line not found"
        found = min(dark, key=lambda x: abs(x - (origin.x() + expected)))
        assert abs(found - (origin.x() + expected)) <= 2
