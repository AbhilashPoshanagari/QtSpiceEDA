"""WaveformController expression traces (W7, W8) and plot definitions (W9), no QML."""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np
import pytest

from bridge.waveform_controller import WaveformController
from bridge.waveform_model import WaveformModel
from core.waveform.plotdefs import DEFAULT_TEXT
from tests.ui.fakes import tran_result
from tests.ui.test_waveform_model import ac_result


@pytest.fixture
def parts(qapp: object) -> Iterator[tuple[WaveformModel, WaveformController]]:
    model, ctl = WaveformModel(), WaveformController()
    ctl.attach(model)
    yield model, ctl


def traces(ctl: WaveformController) -> list[list[str]]:
    return [[t["name"] for t in p["traces"]] for p in ctl.property("panes")]


def info(ctl: WaveformController, name: str) -> dict:
    return next(t for p in ctl.property("panes") for t in p["traces"] if t["name"] == name)


def value(ctl: WaveformController, name: str, x: float) -> float:
    return ctl._store.value_at(name, x)  # dB for AC


def test_vector_list_with_wildcard_filter(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    assert ctl.vectorNames("") == ["V(out)", "V(in)"]
    assert ctl.vectorNames("out") == ["V(out)"]  # no wildcard: matches anywhere
    assert ctl.vectorNames("v(i*") == ["V(in)"]
    assert ctl.vectorNames("*(?n)") == ["V(in)"]
    assert ctl.vectorNames("I(*") == []


def test_plot_an_expression_into_a_pane_and_undo(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    ctl.addPane(0, False)
    assert ctl.plotExpression("V(in)-V(out)", 1) == ""
    assert traces(ctl) == [["V(out)", "V(in)"], ["V(in)-V(out)"]]
    t = info(ctl, "V(in)-V(out)")
    assert t["present"] and t["expression"] and t["error"] == ""
    assert value(ctl, "V(in)-V(out)", 1e-3) == pytest.approx(math.exp(-1), abs=1e-3)
    assert [n["name"] for n in ctl.property("resultTraces")] == ["V(out)", "V(in)"]
    assert "already" in ctl.plotExpression("V(in)-V(out)", 1)
    ctl.undo()
    assert traces(ctl) == [["V(out)", "V(in)"], []]
    ctl.redo()
    assert info(ctl, "V(in)-V(out)")["present"]


def test_bad_expressions_are_not_plotted(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    assert "unknown vector" in ctl.plotExpression("V(nope)*2", -1)
    assert "unexpected character" in ctl.plotExpression("__import__('os')", -1)
    assert ctl.checkExpression("") != ""
    assert ctl.checkExpression("V(out)^V(in)") == ""
    assert traces(ctl) == [["V(out)", "V(in)"]]
    assert not ctl.property("canUndo")


def test_edit_a_trace_expression_in_place(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    assert ctl.editTrace(0, "V(out)", "2*V(out)") == ""
    assert traces(ctl) == [["2*V(out)", "V(in)"]]
    assert "unknown" in ctl.editTrace(0, "2*V(out)", "V(zz)")
    ctl.undo()
    assert traces(ctl) == [["V(out)", "V(in)"]]


def test_expressions_are_recomputed_for_a_new_run(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    ctl.plotExpression("V(out)*1k", 0)
    model.set_result(tran_result(51))
    assert info(ctl, "V(out)*1k")["present"]
    assert value(ctl, "V(out)*1k", 5e-3) == pytest.approx(1000 * (1 - math.exp(-5)), rel=1e-3)


def test_a_layout_with_an_expression_that_does_not_fit_the_run(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    ctl.plotExpression("V(out)/V(in)", 0)
    model.set_result(ac_result())  # no V(in) in this run; .ac has its own layout
    assert traces(ctl) == [["V(out)"]]
    model.set_result(tran_result())
    assert info(ctl, "V(out)/V(in)")["present"]


def test_rc_ac_ratio_reads_minus_3db_at_159_hz(parts) -> None:
    model, ctl = parts
    result = ac_result()
    result.vectors["V(in)"] = np.ones_like(result.vectors["V(out)"])
    result.units["V(in)"] = "V"
    model.set_result(result)
    assert ctl.plotExpression("V(out)/V(in)", 0) == ""
    fc = 1 / (2 * math.pi * 1e-3)
    assert value(ctl, "V(out)/V(in)", fc) == pytest.approx(-3.01, abs=0.02)


def test_plot_defs_are_used_edited_and_undone(parts) -> None:
    model, ctl = parts
    model.set_result(tran_result())
    assert ctl.property("plotDefs") == DEFAULT_TEXT
    assert ctl.plotExpression("dB(V(out))", 0) == ""
    assert "unknown function" in ctl.checkExpression("Pythag(V(out),V(in))")
    assert ctl.checkPlotDefs(".func f(x)") and ctl.checkPlotDefs(DEFAULT_TEXT) == []
    text = DEFAULT_TEXT + ".func Pythag(x,y) sqrt(x**2+y**2)\n"
    assert ctl.setPlotDefs(text) and not ctl.setPlotDefs(text)
    assert ctl.plotExpression("Pythag(V(out),V(in))", 0) == ""
    assert value(ctl, "Pythag(V(out),V(in))", 0) == pytest.approx(1.0)
    ctl.undo()  # the Add
    ctl.undo()  # the definitions
    assert ctl.property("plotDefs") == DEFAULT_TEXT
    ctl.setPlotDefs("* nothing\n")
    t = info(ctl, "dB(V(out))")
    assert not t["present"] and "unknown function" in t["error"]
    ctl.undo()
    assert info(ctl, "dB(V(out))")["present"]
