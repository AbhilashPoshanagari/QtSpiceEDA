"""U4: unit labels, the rpm/degC display toggle and Add Trace groups for physical runs."""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject

from bridge.waveform_controller import WaveformController, display_units
from bridge.waveform_model import WaveformModel
from core.sim.result import SimulationResult
from tests.ui.fakes import tran_result
from tests.ui.mouse import find_item, find_items
from tests.ui.qml_harness import qml_window

RPM = 60.0 / (2.0 * math.pi)


def cosim_result() -> SimulationResult:
    t = np.linspace(0.0, 1.0, 201)
    w = 100.0 * (1.0 - np.exp(-t / 0.1))
    return SimulationResult(
        axis="time",
        vectors={
            "time": t,
            "inertia.A.w": w,
            "damper.A.w": w,
            "sink.A.T": 298.15 + 10.0 * t,
            "sensor.w": w,
            "tau": 1.0 - t,
        },
        units={
            "time": "s",
            "inertia.A.w": "rad/s",
            "damper.A.w": "rad/s",
            "sink.A.T": "K",
            "sensor.w": "rad/s",
            "tau": "",
        },
        analysis="cosim",
    )


@pytest.fixture
def parts(qapp: object) -> Iterator[tuple[WaveformModel, WaveformController]]:
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    yield model, ctl


def pane_values(ctl: WaveformController, name: str) -> np.ndarray:
    return np.asarray(ctl._store.raw(name))  # noqa: SLF001 - what the panes draw


def test_display_units_come_from_the_domain_registry() -> None:
    units = display_units()
    assert set(units) == {"rad/s", "K"}
    assert units["rad/s"].name == "rpm" and units["K"].name == "degC"
    assert units["rad/s"].from_si(2 * math.pi) == pytest.approx(60.0)
    assert units["K"].from_si(273.15) == pytest.approx(0.0)


def test_combined_run_uses_the_transient_layout(parts) -> None:
    model, ctl = parts
    model.set_result(cosim_result())
    assert ctl.property("hasData") is True
    assert ctl.property("analysis") == "tran"
    assert ctl.property("xLabel") == "time [s]"


def test_block_diagram_result_is_shown_too(parts) -> None:
    model, ctl = parts
    t = np.linspace(0, 1, 11)
    model.set_result(
        SimulationResult(
            axis="time", vectors={"time": t, "Scope1": t}, units={"time": "s"}, analysis="blocks"
        )
    )
    assert ctl.property("hasData") is True


def test_y_axis_label_is_the_unit_of_the_pane(parts) -> None:
    model, ctl = parts
    model.set_result(cosim_result())
    ctl.showOnly(0, "inertia.A.w")
    [pane] = ctl.property("panes")
    assert pane["yLabel"] == "[rad/s]"
    ctl.plotExpression("sink.A.T", 0)
    assert ctl.property("panes")[0]["yLabel"] == ""  # mixed units: no single label


def test_tran_pane_keeps_its_voltage_label(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    ctl.showOnly(0, "V(out)")
    assert ctl.property("panes")[0]["yLabel"] == "[V]"


def test_rpm_toggle_converts_shown_values_and_labels(parts) -> None:
    model, ctl = parts
    model.set_result(cosim_result())
    assert ctl.property("displayUnitsAvailable") is True
    ctl.showOnly(0, "inertia.A.w")
    si = pane_values(ctl, "inertia.A.w").copy()
    ctl.setProperty("displayUnits", True)
    assert ctl.property("displayUnits") is True
    assert ctl.property("panes")[0]["yLabel"] == "[rpm]"
    np.testing.assert_allclose(pane_values(ctl, "inertia.A.w"), si * RPM)
    assert "rpm" in ctl.formatY(0, 954.9)
    # degC for temperatures, other units unchanged.
    np.testing.assert_allclose(pane_values(ctl, "sink.A.T")[0], 25.0)
    np.testing.assert_allclose(pane_values(ctl, "tau"), cosim_result().vectors["tau"])
    # The layout (traces) survives the toggle, and it goes back to SI.
    assert [t["name"] for t in ctl.property("panes")[0]["traces"]] == ["inertia.A.w"]
    ctl.setProperty("displayUnits", False)
    np.testing.assert_allclose(pane_values(ctl, "inertia.A.w"), si)
    assert ctl.property("panes")[0]["yLabel"] == "[rad/s]"


def test_expressions_use_si_values_with_the_toggle_on(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    ctl.setProperty("displayUnits", True)
    assert ctl.property("displayUnitsAvailable") is False  # nothing in rad/s or K
    assert ctl.plotExpression("V(out)*2", 0) == ""
    np.testing.assert_allclose(pane_values(ctl, "V(out)*2"), 2 * tran_result().vectors["V(out)"])


def test_vector_groups_for_a_combined_run(parts) -> None:
    model, ctl = parts
    model.set_result(cosim_result())
    assert ctl.vectorGroup("inertia.A.w") == "inertia"
    assert ctl.vectorGroup("sensor.w") == "sensor"
    assert ctl.vectorGroup("tau") == "Signals"
    names = ctl.groupedVectorNames("")
    assert names[0] == "tau"
    assert names[1:] == ["inertia.A.w", "damper.A.w", "sink.A.T", "sensor.w"]
    assert ctl.groupedVectorNames("inertia") == ["inertia.A.w"]


def test_netlist_results_have_no_groups(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    assert ctl.vectorGroup("V(out)") == ""
    assert ctl.groupedVectorNames("") == ctl.vectorNames("")


# ---- in QML


def test_add_trace_dialog_shows_component_headings(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, window, _view):
        model = engine.singletonInstance("QtSpice", "WaveformModel")
        ctl = engine.singletonInstance("QtSpice", "WaveformController")
        model.set_result(cosim_result())
        qtbot.wait(50)
        dialog = window.findChild(QObject, "addTraceDialog")
        dialog.openFor(0)
        qtbot.waitUntil(lambda: dialog.property("opened"), timeout=2000)
        qtbot.wait(50)
        root = dialog.property("contentItem")
        headings = [
            h.property("text")
            for h in find_items(root, "vectorGroup_inertia")
            + find_items(root, "vectorGroup_Signals")
            if h.isVisible()
        ]
        assert sorted(headings) == ["Signals", "inertia"]
        assert find_item(root, "vector_inertia.A.w") is not None
        dialog.close()
        assert ctl.property("displayUnitsAvailable") is True


def test_rpm_button_and_axis_label_in_the_view(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, window, view):
        model = engine.singletonInstance("QtSpice", "WaveformModel")
        ctl = engine.singletonInstance("QtSpice", "WaveformController")
        model.set_result(cosim_result())
        ctl.showOnly(0, "inertia.A.w")
        qtbot.wait(50)
        button = find_item(view, "displayUnitsButton")
        assert button.isVisible()
        label = find_item(view, "yAxisLabel")
        assert label.property("text") == "[rad/s]"
        button.toggle()
        button.toggled.emit()
        qtbot.waitUntil(lambda: ctl.property("displayUnits") is True, timeout=2000)
        qtbot.waitUntil(lambda: label.property("text") == "[rpm]", timeout=2000)
        ctl.setProperty("displayUnits", False)
