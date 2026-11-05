from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QTimer, QUrl

from bridge.simulator import Simulator
from tests.netlists import BROKEN, BROKEN_EXPECTED, GOOD_FILES, INCLUDE_FILE
from tests.ui.fakes import FakeBackend


@pytest.fixture
def simulator(qapp: object) -> Simulator:
    return Simulator()


@pytest.mark.parametrize("path", GOOD_FILES, ids=lambda p: p.name)
def test_good_examples_pasted(simulator: Simulator, path: Path) -> None:
    assert simulator.check(path.read_text()) is True
    assert simulator.property("diagnostics") == []
    deck = simulator.property("deck")
    assert deck.startswith(path.read_text().splitlines()[0])
    assert deck.rstrip().endswith(".end")


@pytest.mark.parametrize("name", sorted(BROKEN_EXPECTED))
def test_broken_examples_pasted(simulator: Simulator, name: str) -> None:
    assert simulator.check((BROKEN / name).read_text()) is False
    found = simulator.property("diagnostics")
    expected = BROKEN_EXPECTED[name]
    assert [d["line"] for d in found] == [line for line, _ in expected]
    for diag, (_, fragment) in zip(found, expected, strict=True):
        assert diag["severity"] == "error"
        assert fragment in diag["message"]
        assert set(diag) == {"line", "col", "severity", "message"}
    assert simulator.property("deck") == ""


def test_signals(simulator: Simulator, qtbot) -> None:
    with qtbot.waitSignals([simulator.diagnosticsChanged, simulator.deckChanged]):
        simulator.check(GOOD_FILES[0].read_text())


def test_open_file_sets_base_dir_for_include(simulator: Simulator) -> None:
    text = INCLUDE_FILE.read_text()
    assert simulator.check(text) is False  # pasted: relative .include cannot be found
    assert "relative .include" in simulator.property("diagnostics")[0]["message"]

    opened = simulator.openFile(QUrl.fromLocalFile(str(INCLUDE_FILE)))
    assert opened == text
    assert simulator.property("filePath") == str(INCLUDE_FILE.resolve())
    assert simulator.property("errorString") == ""
    assert simulator.check(opened) is True


def test_open_missing_file(simulator: Simulator, tmp_path: Path) -> None:
    assert simulator.openFile(QUrl.fromLocalFile(str(tmp_path / "nope.cir"))) == ""
    assert "nope.cir" in simulator.property("errorString")
    assert simulator.property("filePath") == ""


def test_is_netlist_file(simulator: Simulator) -> None:
    for name, ok in (("a.cir", True), ("b.NET", True), ("c.sp", True), ("d.txt", False)):
        assert simulator.isNetlistFile(QUrl.fromLocalFile(f"C:/x/{name}")) is ok, name


# ---- Phase 3: running ------------------------------------------------------

RC_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "rc_lowpass.cir"


@pytest.fixture
def fake_sim(qapp: object):
    backend = FakeBackend(lines=["Circuit: rc", "Transient Analysis"])
    sim = Simulator(backend=backend)
    yield sim, backend
    sim.shutdown()


@pytest.fixture
def real_sim(qapp: object):
    sim = Simulator()
    yield sim
    sim.shutdown()


def test_run_refuses_netlist_with_errors(fake_sim, qtbot) -> None:
    sim, backend = fake_sim
    text = (BROKEN / "missing_ground.cir").read_text()
    assert sim.run(text) is False
    assert sim.property("running") is False
    assert "error" in sim.property("statusText")
    assert backend.loaded == ""
    assert sim.property("diagnostics")  # errors are shown on their lines


def test_run_refuses_without_analysis(fake_sim) -> None:
    sim, backend = fake_sim
    assert sim.run("t\nV1 a 0 1\nR1 a 0 1k\n.end\n") is False
    assert "1 error" in sim.property("statusText")
    assert "no analysis" in sim.property("diagnostics")[0]["message"]  # from validate()
    assert backend.loaded == ""


def test_run_refuses_bad_analysis_card(fake_sim) -> None:
    sim, backend = fake_sim
    sim.setProperty("analysisCard", ".tran abc 5m")
    assert sim.run(RC_EXAMPLE.read_text()) is False
    assert ".tran abc 5m" in sim.property("statusText")
    assert backend.loaded == ""


def test_run_uses_netlist_card_then_panel_card(fake_sim, qtbot) -> None:
    sim, backend = fake_sim
    text = RC_EXAMPLE.read_text()
    with qtbot.waitSignal(sim.resultReady, timeout=3000):
        assert sim.run(text) is True
        assert sim.property("running") is True
    assert sim.property("netlistAnalysis") == ".tran 1u 5m"
    assert ".tran 1u 5m" in backend.loaded and ".ac" not in backend.loaded
    assert sim.property("sentDeck") == backend.loaded

    sim.setProperty("analysisCard", ".ac dec 10 1 1k")
    with qtbot.waitSignal(sim.resultReady, timeout=3000):
        sim.run(text)
    assert ".ac dec 10 1 1k" in backend.loaded and ".tran" not in backend.loaded
    assert sim.property("running") is False
    assert sim.property("progress") == 100
    assert sim.property("consoleText") == "Circuit: rc\nTransient Analysis"
    assert sim.property("statusText").startswith("Done: .tran")  # the fake's result


def test_run_pushes_result_to_waveforms(fake_sim, qtbot) -> None:
    sim, _ = fake_sim

    class Sink(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.results: list[object] = []

        def set_result(self, result: object) -> None:
            self.results.append(result)

    sink = Sink()
    sim.attachWaveforms(sink)
    with qtbot.waitSignal(sim.resultReady, timeout=3000):
        sim.run(RC_EXAMPLE.read_text())
    assert sink.results == [sim.result]


def test_failed_run_reports_error(qapp: object, qtbot) -> None:
    sim = Simulator(backend=FakeBackend(fail_in="run"))
    try:
        with qtbot.waitSignal(sim.runningChanged, timeout=3000):
            sim.run(RC_EXAMPLE.read_text())
        qtbot.waitUntil(lambda: not sim.property("running"), timeout=3000)
        assert sim.property("statusText") == "Failed: run failed"
        assert "Error: run failed" in sim.property("consoleText")
    finally:
        sim.shutdown()


def test_second_run_while_running_is_refused(qapp: object, qtbot) -> None:
    sim = Simulator(backend=FakeBackend(block=True))
    try:
        assert sim.run(RC_EXAMPLE.read_text()) is True
        assert sim.run(RC_EXAMPLE.read_text()) is False
        sim.stop()
        qtbot.waitUntil(lambda: not sim.property("running"), timeout=3000)
        assert sim.property("statusText") == "Stopped"
    finally:
        sim.shutdown()


@pytest.mark.ngspice
def test_real_run_fills_console_and_result(real_sim: Simulator, qtbot) -> None:
    with qtbot.waitSignal(real_sim.resultReady, timeout=10_000):
        assert real_sim.run(RC_EXAMPLE.read_text()) is True
    assert "transient" in real_sim.property("consoleText").lower()
    result = real_sim.result
    assert result is not None and result.analysis == "tran"


@pytest.mark.ngspice
def test_op_values_are_printed(real_sim: Simulator, qtbot) -> None:
    real_sim.setProperty("analysisCard", ".op")
    with qtbot.waitSignal(real_sim.resultReady, timeout=10_000):
        real_sim.run("divider\nV1 in 0 2\nR1 in out 1k\nR2 out 0 1k\n.op\n.end\n")
    assert "V(out) = 1 V" in real_sim.property("consoleText")


@pytest.mark.ngspice
def test_stop_cancels_a_long_run(real_sim: Simulator, qtbot) -> None:
    # ~100 million time steps (tmax 1u): minutes of work. ngspice preallocates
    # tstop/tstep points, so keep that ratio (1e7) within memory.
    real_sim.setProperty("analysisCard", ".tran 10u 100 0 1u")
    assert real_sim.run(RC_EXAMPLE.read_text()) is True
    qtbot.wait(300)
    assert real_sim.property("running") is True
    started = time.monotonic()
    real_sim.stop()
    qtbot.waitUntil(lambda: not real_sim.property("running"), timeout=3000)
    assert time.monotonic() - started < 2.0
    assert real_sim.property("statusText") == "Stopped"
    # ngspice is usable again afterwards
    real_sim.setProperty("analysisCard", ".tran 1u 1m")
    with qtbot.waitSignal(real_sim.resultReady, timeout=10_000):
        assert real_sim.run(RC_EXAMPLE.read_text()) is True


@pytest.mark.ngspice
def test_gui_stays_responsive_during_million_point_run(real_sim: Simulator, qtbot) -> None:
    """A 10 ms GUI timer keeps ticking while ngspice computes 1e6 points."""
    ticks: list[float] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    real_sim.setProperty("analysisCard", ".tran 5n 5m")
    with qtbot.waitSignal(real_sim.resultReady, timeout=60_000):
        assert real_sim.run(RC_EXAMPLE.read_text()) is True
    timer.stop()
    assert len(real_sim.result.vectors["time"]) >= 1_000_000
    gaps = np.diff(ticks)
    assert len(ticks) > 50
    assert gaps.max() < 0.25, f"GUI thread blocked for {gaps.max():.3f} s"


def test_panel_card_makes_netlist_without_analysis_runnable(fake_sim, qtbot) -> None:
    sim, backend = fake_sim
    text = "t\nV1 a 0 1\nR1 a 0 1k\n.end\n"
    assert sim.check(text) is False  # editor shows "no analysis card"

    with qtbot.waitSignal(sim.diagnosticsChanged):  # re-checked when the card is set
        sim.setProperty("analysisCard", ".op")
    assert sim.property("diagnostics") == []
    with qtbot.waitSignal(sim.resultReady, timeout=3000):
        assert sim.run(text) is True
    assert backend.loaded.splitlines()[-2:] == [".op", ".end"]

    sim.setProperty("analysisCard", "")  # back to the netlist: error again
    assert "no analysis" in sim.property("diagnostics")[0]["message"]
    assert sim.run(text) is False
