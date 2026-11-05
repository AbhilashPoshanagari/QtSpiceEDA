from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMetaObject, QObject

from tests.ui.qml_harness import qml_window

RC = (Path(__file__).resolve().parents[2] / "examples" / "rc_lowpass.cir").read_text()


def _field(panel: QObject, name: str) -> QObject:
    obj = panel.findChild(QObject, name)
    assert obj is not None, name
    return obj


def _edit(panel: QObject, **values: str) -> None:
    """Set fields as if typed, then notify the panel like onTextEdited does."""
    for name, text in values.items():
        _field(panel, name).setProperty("text", text)
    QMetaObject.invokeMethod(panel, "edited")


def test_follows_netlist_card_until_edited(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "AnalysisPanel") as (engine, _, panel):
        sim = engine.singletonInstance("QtSpice", "Simulator")
        sim.check(RC)
        qtbot.waitUntil(lambda: _field(panel, "tranStep").property("text") == "1u")
        assert _field(panel, "tranStop").property("text") == "5m"
        assert _field(panel, "analysisKind").property("currentIndex") == 3
        assert sim.property("analysisCard") == ""  # the netlist card is used

        _edit(panel, tranStep="2u", tranStart="1m")
        assert sim.property("analysisCard") == ".tran 2u 5m 1m"
        assert panel.property("followNetlist") is False

        sim.check(RC.replace(".tran 1u 5m", ".tran 7u 5m"))  # no longer followed
        assert _field(panel, "tranStep").property("text") == "2u"

        QMetaObject.invokeMethod(panel, "useNetlistCard")
        assert sim.property("analysisCard") == ""
        assert _field(panel, "tranStep").property("text") == "7u"


def test_each_form_builds_its_card(qapp: object, tmp_path: Path) -> None:
    with qml_window(tmp_path, "AnalysisPanel") as (engine, _, panel):
        sim = engine.singletonInstance("QtSpice", "Simulator")
        kind = _field(panel, "analysisKind")

        kind.setProperty("currentIndex", 0)
        _edit(panel)
        assert sim.property("analysisCard") == ".op"

        kind.setProperty("currentIndex", 1)
        _edit(panel, dcSource="V1", dcStart="0", dcStop="5", dcStep="0.1")
        assert sim.property("analysisCard") == ".dc V1 0 5 0.1"

        kind.setProperty("currentIndex", 2)
        _field(panel, "acVariation").setProperty("currentIndex", 1)
        _edit(panel, acPoints="10", acStart="1", acStop="1meg")
        assert sim.property("analysisCard") == ".ac oct 10 1 1meg"

        kind.setProperty("currentIndex", 3)
        _edit(panel, tranStep="1u", tranStop="5m", tranStart="")
        assert sim.property("analysisCard") == ".tran 1u 5m"
        assert _field(panel, "cardError").property("visible") is False


def test_invalid_card_shows_parser_message(qapp: object, tmp_path: Path) -> None:
    with qml_window(tmp_path, "AnalysisPanel") as (engine, _, panel):
        _field(panel, "analysisKind").setProperty("currentIndex", 3)
        _edit(panel, tranStep="abc", tranStop="5m")
        error = _field(panel, "cardError")
        assert error.property("visible") is True
        assert "abc" in error.property("text")


def test_load_ac_card_from_netlist(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "AnalysisPanel") as (engine, _, panel):
        sim = engine.singletonInstance("QtSpice", "Simulator")
        sim.check(RC.replace(".tran 1u 5m\n", ""))  # first card is now .ac dec 50 1 100k
        qtbot.waitUntil(lambda: _field(panel, "analysisKind").property("currentIndex") == 2)
        assert _field(panel, "acVariation").property("currentIndex") == 0
        assert _field(panel, "acPoints").property("text") == "50"
        assert _field(panel, "acStop").property("text") == "100k"
