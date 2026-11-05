from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject  # noqa: E402

from app.__main__ import create_engine  # noqa: E402

RC_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "rc_lowpass.cir"


def test_main_window_loads(qapp: object) -> None:
    engine = create_engine()
    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load"
    window = roots[0]

    for name in ("openButton", "saveButton", "checkButton", "runButton", "stopButton"):
        assert window.findChild(QObject, name) is not None, name
    # Stop is enabled only while running.
    for name, enabled in (
        ("openButton", True),
        ("checkButton", True),
        ("saveButton", True),
        ("runButton", True),
        ("stopButton", False),
    ):
        assert window.findChild(QObject, name).property("enabled") is enabled, name
    assert window.findChild(QObject, "netlistEditor") is not None

    left = window.findChild(QObject, "leftTabs")
    bottom = window.findChild(QObject, "bottomTabs")
    assert left.property("count") == 3  # Netlist, Blocks, Schematic
    assert bottom.property("count") == 3
    window.close()
    engine.deleteLater()


@pytest.mark.ngspice
def test_run_and_stop_from_the_toolbar(qapp: object, qtbot) -> None:
    engine = create_engine()
    window = engine.rootObjects()[0]
    sim = engine.singletonInstance("QtSpice", "Simulator")
    waveforms = engine.singletonInstance("QtSpice", "WaveformModel")
    try:
        run = window.findChild(QObject, "runButton")
        stop = window.findChild(QObject, "stopButton")
        bottom = window.findChild(QObject, "bottomTabs")
        editor = window.findChild(QObject, "editorText")
        editor.setProperty("text", RC_EXAMPLE.read_text())

        with qtbot.waitSignal(sim.resultReady, timeout=10_000):
            run.clicked.emit()
            assert stop.property("enabled") is True
            assert run.property("enabled") is False
        assert stop.property("enabled") is False
        assert bottom.property("currentIndex") == 0  # Waveforms
        assert waveforms.property("traceNames") == ["I(v1)", "V(out)", "V(in)"]
        assert ".tran 1u 5m" in window.findChild(QObject, "deckText").property("text")

        sim.setProperty("analysisCard", ".tran 10u 100 0 1u")  # minutes of work
        run.clicked.emit()
        qtbot.wait(300)
        assert sim.property("running") is True
        stop.clicked.emit()
        qtbot.waitUntil(lambda: not sim.property("running"), timeout=3000)
        assert window.findChild(QObject, "runStatus").property("text") == "Stopped"
    finally:
        sim.shutdown()
        window.close()
        engine.deleteLater()
