"""AddTraceDialog.qml (W8) and its plot definitions editor (W9) in the waveform window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject

from core.waveform.plotdefs import DEFAULT_TEXT
from tests.ui.fakes import tran_result
from tests.ui.mouse import click, item_center
from tests.ui.qml_harness import qml_window
from tests.ui.test_waveform_view import _child, _parts, _settle


def _names(ctl: QObject) -> list[list[str]]:
    return [[t["name"] for t in p["traces"]] for p in ctl.property("panes")]


def _vectors(dialog: QObject) -> list[str]:
    model = _child(dialog, "vectorList").property("model")
    return list(model.toVariant() if hasattr(model, "toVariant") else model)


def test_filter_insert_and_plot_into_the_selected_pane(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, window, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(1001))
        ctl.addPane(0, False)
        _settle(qtbot)
        dialog = _child(view, "addTraceDialog")
        dialog.openFor(1)
        _settle(qtbot)
        assert dialog.property("visible")
        assert _vectors(dialog) == ["V(out)", "V(in)"]
        _child(dialog, "vectorFilter").setProperty("text", "*in*")
        _settle(qtbot)
        assert _vectors(dialog) == ["V(in)"]
        _child(dialog, "vectorFilter").setProperty("text", "")
        _settle(qtbot)

        field = _child(dialog, "expressionField")
        click(window, item_center(_child(_child(dialog, "vectorList"), "vector_V(out)")))
        _settle(qtbot)
        assert field.property("text") == "V(out)"
        field.setProperty("text", "V(out)/")
        field.setProperty("cursorPosition", 7)
        click(window, item_center(_child(_child(dialog, "vectorList"), "vector_V(in)")))
        _settle(qtbot)
        assert field.property("text") == "V(out)/V(in)"
        assert _child(dialog, "expressionError").property("text") == ""
        assert _child(dialog, "paneBox").property("currentIndex") == 1

        assert dialog.apply() is True
        _settle(qtbot)
        assert not dialog.property("visible")
        assert _names(ctl) == [["V(out)", "V(in)"], ["V(out)/V(in)"]]


def test_live_errors_and_rejected_text(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(101))
        _settle(qtbot)
        dialog = _child(view, "addTraceDialog")
        dialog.openFor(0)
        field = _child(dialog, "expressionField")
        error = _child(dialog, "expressionError")
        field.setProperty("text", "__import__('os')")
        _settle(qtbot)
        assert "unexpected character" in error.property("text")
        assert not _child(dialog, "plotButton").property("enabled")
        field.setProperty("text", "V(nope)")
        _settle(qtbot)
        assert "unknown vector" in error.property("text")
        assert dialog.apply() is False and dialog.property("visible")
        assert _names(ctl) == [["V(out)", "V(in)"]]
        dialog.close()


def test_edit_a_trace_expression_from_its_label(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(101))
        _settle(qtbot)
        label = _child(view, "label_0_V(in)")
        label.editRequested.emit()
        _settle(qtbot)
        dialog = _child(view, "addTraceDialog")
        assert dialog.property("visible") and dialog.property("editing")
        field = _child(dialog, "expressionField")
        assert field.property("text") == "V(in)"
        field.setProperty("text", "V(in)-V(out)")
        assert dialog.apply() is True
        _settle(qtbot)
        assert _names(ctl) == [["V(out)", "V(in)-V(out)"]]
        assert ctl.property("undoText") == "Edit V(in)"


def test_plot_definitions_editor(qapp: object, tmp_path: Path, qtbot) -> None:
    with qml_window(tmp_path, "WaveformView") as (engine, _, view):
        model, ctl = _parts(engine)
        model.set_result(tran_result(101))
        _settle(qtbot)
        dialog = _child(view, "addTraceDialog")
        dialog.openFor(0)
        defs = _child(view, "plotDefsDialog")
        defs.openEditor()
        _settle(qtbot)
        text = _child(defs, "plotDefsText")
        assert text.property("text") == DEFAULT_TEXT
        text.setProperty("text", ".func f(x\n")
        _settle(qtbot)
        assert "line 1" in _child(defs, "plotDefsProblems").property("text")
        text.setProperty("text", DEFAULT_TEXT + ".func Pythag(x,y) sqrt(x**2+y**2)\n")
        _settle(qtbot)
        assert _child(defs, "plotDefsProblems").property("text") == ""
        defs.accept()
        _settle(qtbot)
        assert "Pythag" in ctl.property("plotDefs")
        field = _child(dialog, "expressionField")
        field.setProperty("text", "Pythag(V(out),V(in))")
        _settle(qtbot)
        assert _child(dialog, "expressionError").property("text") == ""
        assert dialog.apply() is True
        ctl.undo()
        ctl.undo()
        assert ctl.property("plotDefs") == DEFAULT_TEXT
