from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QUrl

from bridge.simulator import Simulator
from tests.netlists import BROKEN, BROKEN_EXPECTED, GOOD_FILES, INCLUDE_FILE


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
