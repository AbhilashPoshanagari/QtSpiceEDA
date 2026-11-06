"""The waveform window in QML: panes (W2), zoom (W3), trace labels (W4), log axes
(W5) and cursors (W6). The v0.1 checks (traces drawn with at most two points per
pixel, fit, AC at 159 Hz, the plot-area geometry, .ac -> .tran -> .ac) are kept,
adapted to the LTspice-style window."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from core.sim.result import SimulationResult
from tests.ui.fakes import tran_result
from tests.ui.mouse import drag
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


def _series(pane: QObject) -> list[dict]:
    value = pane.property("seriesState")
    return value.toVariant() if hasattr(value, "toVariant") else value


def _settle(qtbot) -> None:
    qtbot.wait(150)  # refresh timers (16 ms) plus a few frames


def _parts(engine):
    model = engine.singletonInstance("QtSpice", "WaveformModel")
    ctl = engine.singletonInstance("QtSpice", "WaveformController")
    return model, ctl


def _panes(view: QObject, ctl: QObject) -> list[QObject]:
    return [_child(view, f"pane{i}") for i in range(ctl.property("paneCount"))]


# ---- W2: panes


def test_shows_traces_in_one_pane(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(200_000))
        _settle(qtbot)
        pane = _child(view, "pane0")
        series = _series(pane)
        assert [s["name"] for s in series] == ["V(out)", "V(in)"]
        width = pane.property("plotWidth")
        assert all(0 < s["count"] <= 2 * round(width) for s in series)
        assert pane.property("x0") == 0 and pane.property("x1") == pytest.approx(5e-3)
        assert pane.property("yMin") < 0 < 1 < pane.property("yMax")


def test_add_and_delete_panes_follow_the_controller(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(1001))
        _settle(qtbot)
        assert ctl.addPane(0, True) == 0
        assert ctl.addPane(1, False) == 2
        ctl.dropTrace(1, "V(in)", 2, False)
        _settle(qtbot)
        panes = _panes(view, ctl)
        assert [[s["name"] for s in _series(p)] for p in panes] == [[], ["V(out)"], ["V(in)"]]
        assert panes[0].property("height") == pytest.approx(panes[2].property("height"))
        assert ctl.deletePane(0) and ctl.deletePane(0)  # removes delegates, keeps the app up
        _settle(qtbot)
        assert [s["name"] for s in _series(_child(view, "pane0"))] == ["V(in)"]
        for _ in range(3):
            ctl.undo()
        _settle(qtbot)
        assert ctl.property("paneCount") == 3
        panes = _panes(view, ctl)
        assert [[s["name"] for s in _series(p)] for p in panes] == [[], ["V(out)", "V(in)"], []]


def test_trace_list_adds_to_the_active_pane(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(101))
        _settle(qtbot)
        box = _child(view, "trace_V(in)")
        assert box.property("checked") is True
        ctl.setTraceShown("V(in)", False)
        _settle(qtbot)
        assert _child(view, "trace_V(in)").property("checked") is False
        ctl.addPane(0, False)
        ctl.setTraceShown("V(in)", True)
        _settle(qtbot)
        assert [s["name"] for s in _series(_child(view, "pane1"))] == ["V(in)"]


def test_config_dialog_titles_ranges_and_order(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(1001))
        ctl.addPane(0, False)
        _settle(qtbot)
        dialog = _child(view, "plotConfigDialog")
        dialog.openFor(0)
        _child(dialog, "paneTitleField").setProperty("text", "outputs")
        _child(dialog, "yAuto").setProperty("checked", False)
        _child(dialog, "yLo").setProperty("text", "-100m")
        _child(dialog, "yHi").setProperty("text", "1.5")
        dialog.move(1)  # the first pane goes below the empty one
        assert dialog.accept_() is True
        _settle(qtbot)
        panes = ctl.property("panes")
        assert [p["title"] for p in panes] == ["", "outputs"]
        assert [t["name"] for t in panes[1]["traces"]] == ["V(out)", "V(in)"]
        pane = _child(view, "pane1")
        assert pane.property("yMin") == pytest.approx(-0.1)
        assert pane.property("yMax") == pytest.approx(1.5)
        assert ctl.property("undoText") == "Configure plot"
        ctl.undo()
        assert [p["title"] for p in ctl.property("panes")] == ["", ""]

        dialog.openFor(0)
        _child(dialog, "xAuto").setProperty("checked", False)
        _child(dialog, "xLo").setProperty("text", "3m")
        _child(dialog, "xHi").setProperty("text", "1m")
        assert dialog.accept_() is False
        assert "below" in _child(dialog, "configProblem").property("text")
        dialog.close()


def test_plot_area_geometry_matches_qtgraphs(qapp: object, tmp_path: Path, qtbot) -> None:
    """A vertical edge at t = 1.7 ms must land where toPixelX() says, or zoom boxes
    and cursors would be off (GraphsView 6.8 has no plotArea)."""
    t = np.linspace(0, 5e-3, 5001)
    result = SimulationResult(
        "time", {"time": t, "V(step)": (t >= 1.7e-3).astype(float)}, {"time": "s"}, "tran"
    )
    with qml_window(tmp_path, "WaveformView") as (engine, window, view):
        model, _ = _parts(engine)
        model.set_result(result)
        _settle(qtbot)
        pane = _child(view, "pane0")
        expected = pane.property("plotLeft") + (1.7e-3 / 5e-3) * pane.property("plotWidth")
        origin = pane.mapToItem(window.contentItem(), 0, 0)
        image = window.grabWindow()
        y = int(origin.y() + pane.property("plotTop") + pane.property("plotHeight") / 2)

        def blue(x: int) -> bool:
            c = image.pixelColor(x, y)
            return c.blue() > c.red() + 60

        found = [x for x in range(image.width()) if blue(x)]
        assert found, "trace edge not found"
        best = min(found, key=lambda x: abs(x - (origin.x() + expected)))
        assert abs(best - (origin.x() + expected)) <= 2


def test_ac_then_tran_and_back(qapp: object, tmp_path: Path, qtbot, qtlog) -> None:
    # .ac then .tran used to abort the app: the trace series were destroyed while
    # still in the GraphsView (QtGraphs 6.8 pure virtual call). The series are a pool
    # that is reused, and trace colours never come out undefined.
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, _ = _parts(engine)
        for result, names in (
            (tran_result(1_001), ["V(out)", "V(in)"]),
            (ac_result(), ["V(out)", "V(out)"]),  # magnitude and phase
            (tran_result(2_001), ["V(out)", "V(in)"]),
            (ac_result(), ["V(out)", "V(out)"]),
        ):
            model.set_result(result)
            _settle(qtbot)
            series = _series(_child(view, "pane0"))
            assert [s["name"] for s in series] == names
            assert all(s["count"] > 0 for s in series)
    warnings = [r.message for r in qtlog.records if "Unable to assign" in r.message]
    assert warnings == []


# ---- W3: zoom box, zoom back, status bar


def _plot_point(pane: QObject, fx: float, fy: float):
    """Window point at fractions (fx, fy) of the pane's plot area."""
    p = pane.mapToScene(
        QPointF(
            pane.property("plotLeft") + fx * pane.property("plotWidth"),
            pane.property("plotTop") + fy * pane.property("plotHeight"),
        )
    )
    return QPoint(round(p.x()), round(p.y()))


def test_drag_box_zooms_and_esc_cancels(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, window, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(5001))
        ctl.addPane(0, False)
        _settle(qtbot)
        top, bottom = _panes(view, ctl)
        status = _child(view, "statusBar")
        seen: list[str] = []
        drag(
            window,
            _plot_point(top, 0.2, 0.2),
            _plot_point(top, 0.6, 0.8),
            before_release=lambda: seen.append(status.property("text")),
        )
        _settle(qtbot)
        assert ctl.property("xMin") == pytest.approx(1e-3, rel=0.02)
        assert ctl.property("xMax") == pytest.approx(3e-3, rel=0.02)
        assert bottom.property("x0") == top.property("x0")  # one X for all panes
        assert "Δx = " in seen[0] and "1/Δ = " in seen[0]  # 2 ms -> 500 Hz
        freq = float(re.search(r"1/Δ = ([\d.]+) Hz", seen[0]).group(1))
        assert freq == pytest.approx(500, rel=0.02)

        # Esc while dragging: no zoom.
        before = (ctl.property("xMin"), ctl.property("xMax"))

        def press_esc() -> None:
            QTest.keyClick(window, Qt.Key.Key_Escape)

        drag(
            window, _plot_point(top, 0.1, 0.1), _plot_point(top, 0.9, 0.9), before_release=press_esc
        )
        _settle(qtbot)
        assert (ctl.property("xMin"), ctl.property("xMax")) == before
        assert not _child(view, "zoomBox").property("visible")

        # A click (no box) does not zoom either.
        drag(window, _plot_point(top, 0.5, 0.5), _plot_point(top, 0.5, 0.5))
        assert (ctl.property("xMin"), ctl.property("xMax")) == before

        _child(view, "zoomBackButton").clicked.emit()
        _settle(qtbot)
        assert ctl.property("xMax") == pytest.approx(5e-3)
        ctl.undo()
        assert ctl.property("xMax") == pytest.approx(3e-3, rel=0.02)
