from __future__ import annotations

import numpy as np
import pytest

from bridge.diagram_controller import (
    GRID,
    DiagramController,
    block_geometry,
    parse_setting,
)
from bridge.waveform_model import WaveformModel
from core.blocks.spec import BlockSpec

ADD_SUB = (
    '@block(shape="circle", outputs=["sum", "diff"])\ndef add_sub(a, b): return a + b, a - b\n'
)


@pytest.fixture
def controller(qapp: object) -> DiagramController:
    return DiagramController()


def entry(controller: DiagramController, name: str) -> dict | None:
    return next((b for b in controller.property("library") if b["name"] == name), None)


def test_library_starts_with_builtins(controller: DiagramController) -> None:
    names = [b["name"] for b in controller.property("library")]
    assert names[:3] == ["Constant", "Step", "Sine"]
    assert "Scope" in names
    gain = entry(controller, "Gain")
    assert gain is not None
    assert gain["builtin"] is True
    assert gain["settings"] == [{"name": "k", "value": 1.0}]


def test_apply_add_sub(controller: DiagramController) -> None:
    assert controller.apply(ADD_SUB) is True
    block = entry(controller, "add_sub")
    assert block is not None
    assert block["shape"] == "circle"
    assert block["inputs"] == ["a", "b"]
    assert block["outputs"] == ["sum", "diff"]
    assert block["builtin"] is False
    assert controller.property("diagnostics") == []
    assert "add_sub" in controller.property("statusText")


def test_syntax_error_keeps_library(controller: DiagramController) -> None:
    controller.apply(ADD_SUB)
    assert controller.apply("@block\ndef add_sub(a, b):\n    return a +\n") is False
    [diag] = controller.property("diagnostics")
    assert diag["line"] == 3
    assert diag["severity"] == "error"
    assert set(diag) == {"line", "col", "severity", "message"}
    assert entry(controller, "add_sub")["outputs"] == ["sum", "diff"]  # type: ignore[index]
    assert "not updated" in controller.property("statusText")


def test_removed_ports_reported(controller: DiagramController) -> None:
    controller.apply(ADD_SUB)
    controller.apply('@block(outputs=["sum"])\ndef add_sub(a): return a\n')
    assert controller.property("removedPorts") == ["add_sub: input b", "add_sub: output diff"]
    assert "removed ports" in controller.property("statusText")


def test_check_does_not_change_library(controller: DiagramController) -> None:
    assert controller.check(ADD_SUB) is True
    assert entry(controller, "add_sub") is None


def test_new_document_keeps_blocks_and_open_block_returns_code(
    controller: DiagramController,
) -> None:
    controller.apply(ADD_SUB)
    first = controller.property("document")
    second = controller.newDocument()
    assert second != first
    controller.apply("@block\ndef twice(u): return 2 * u\n")
    assert entry(controller, "add_sub") is not None
    assert entry(controller, "twice") is not None

    assert controller.openBlock("add_sub") == ADD_SUB
    assert controller.property("document") == first
    assert controller.openBlock("Gain") == ""
    assert controller.property("document") == first


def test_clearing_document_removes_its_blocks(controller: DiagramController) -> None:
    controller.apply(ADD_SUB)
    assert controller.apply("") is True
    assert entry(controller, "add_sub") is None
    assert controller.property("diagnostics") == []
    assert "removed blocks: add_sub" in controller.property("statusText")


def test_builtin_name_clash(controller: DiagramController) -> None:
    assert controller.apply("@block\ndef Gain(u): return u\n") is False
    assert controller.property("diagnostics")[0]["line"] == 2


# ---- block diagram canvas


def rows(model) -> list[dict]:
    return list(model.rows())


def block_row(controller: DiagramController, block_id: str) -> dict:
    return next(r for r in rows(controller.blocks) if r["blockId"] == block_id)


def chain(controller: DiagramController) -> None:
    """Sine1 -> Gain1 -> Integrator1 -> Scope1."""
    for i, name in enumerate(("Sine", "Gain", "Integrator", "Scope")):
        controller.addBlock(name, 160 * i, 100)
    assert controller.connectPorts("Sine1.y", "Gain1.u")
    assert controller.connectPorts("Gain1.y", "Integrator1.u")
    assert controller.connectPorts("Integrator1.y", "Scope1.u")


def test_geometry_spaces_ports_evenly() -> None:
    geo = block_geometry(BlockSpec("s", inputs=["a", "b", "c"], outputs=["y"], shape="rectangle"))
    assert geo.height % GRID == 0 and geo.width % GRID == 0
    ys = [p["y"] for p in geo.inputs]
    assert ys[1] - ys[0] == ys[2] - ys[1] > 0
    assert (ys[0] + ys[-1]) / 2 == geo.height / 2
    assert all(p["x"] == 0 for p in geo.inputs)
    assert geo.outputs == [{"name": "y", "x": geo.width, "y": geo.height / 2}]
    circle = block_geometry(BlockSpec("c", inputs=["a", "b"], outputs=["y"], shape="circle"))
    assert circle.width == circle.height
    assert all(p["x"] > 0 for p in circle.inputs)  # on the curved outline


def test_add_block_snaps_and_undoes(controller: DiagramController) -> None:
    assert controller.addBlock("Gain", 33, 47) == "Gain1"
    row = block_row(controller, "Gain1")
    assert (row["blockX"], row["blockY"]) == (40, 40)
    assert row["shape"] == "rectangle"
    assert [p["name"] for p in row["inputs"]] == ["u"]
    assert controller.property("canUndo") is True
    assert controller.property("undoText") == "add Gain1"
    controller.undo()
    assert rows(controller.blocks) == []
    assert controller.diagram.blocks == []
    controller.redo()
    assert block_row(controller, "Gain1")["blockX"] == 40


def test_add_unknown_block_sets_hint(controller: DiagramController) -> None:
    assert controller.addBlock("Nope", 0, 0) == ""
    assert "Nope" in controller.property("hint")
    assert controller.property("canUndo") is False


def test_connect_and_wire_geometry(controller: DiagramController) -> None:
    controller.addBlock("Sine", 0, 0)
    controller.addBlock("Scope", 200, 100)
    assert controller.connectPorts("Sine1.y", "Scope1.u") is True
    [wire] = rows(controller.wires)
    sine, scope = block_row(controller, "Sine1"), block_row(controller, "Scope1")
    assert wire["src"] == "Sine1.y" and wire["dst"] == "Scope1.u"
    assert wire["x1"] == sine["blockWidth"] and wire["y1"] == sine["blockHeight"] / 2
    assert wire["x2"] == 200 and wire["y2"] == 100 + scope["blockHeight"] / 2
    controller.undo()
    assert rows(controller.wires) == [] and controller.diagram.wires == []
    controller.redo()
    assert len(controller.diagram.wires) == 1


def test_invalid_connect_sets_hint_and_changes_nothing(controller: DiagramController) -> None:
    controller.addBlock("Constant", 0, 0)
    controller.addBlock("Constant", 0, 100)
    controller.addBlock("Gain", 200, 0)
    assert controller.canConnect("Constant1.y", "Gain1.u") == ""
    assert "is an output" in controller.canConnect("Constant1.y", "Constant2.y")
    assert controller.connectPorts("Constant1.y", "Gain1.u")
    undo_text = controller.property("undoText")
    assert controller.connectPorts("Constant2.y", "Gain1.u") is False
    assert "already wired from Constant1.y" in controller.property("hint")
    assert len(controller.diagram.wires) == 1
    assert controller.property("undoText") == undo_text
    # A good edit clears the hint.
    controller.addBlock("Scope", 400, 0)
    assert controller.property("hint") == ""


def test_moves_in_one_drag_are_one_undo_step(controller: DiagramController) -> None:
    controller.addBlock("Sine", 0, 0)
    controller.addBlock("Scope", 200, 0)
    controller.connectPorts("Sine1.y", "Scope1.u")
    for x in (10, 30, 55, 81):
        controller.moveBlock("Sine1", x, 0)
    controller.endMove()
    assert block_row(controller, "Sine1")["blockX"] == 80
    [wire] = rows(controller.wires)
    assert wire["x1"] == 80 + block_row(controller, "Sine1")["blockWidth"]
    controller.moveBlock("Sine1", 80, 40)
    controller.endMove()
    controller.undo()
    assert block_row(controller, "Sine1")["blockY"] == 0
    assert block_row(controller, "Sine1")["blockX"] == 80
    controller.undo()
    assert block_row(controller, "Sine1")["blockX"] == 0
    assert rows(controller.wires)[0]["x1"] == block_row(controller, "Sine1")["blockWidth"]
    controller.redo()
    assert block_row(controller, "Sine1")["blockX"] == 80


def test_moving_a_selected_block_moves_the_selection(controller: DiagramController) -> None:
    controller.addBlock("Sine", 0, 0)
    controller.addBlock("Gain", 200, 0)
    controller.addBlock("Scope", 400, 0)
    controller.selectInRect(-10, -10, 300, 100, False)
    assert [r["selected"] for r in rows(controller.blocks)] == [True, True, False]
    controller.moveBlock("Gain1", 220, 40)
    controller.endMove()
    sine = block_row(controller, "Sine1")
    assert (sine["blockX"], sine["blockY"]) == (20, 40)
    assert block_row(controller, "Gain1")["blockX"] == 220
    assert block_row(controller, "Scope1")["blockX"] == 400
    controller.undo()
    assert block_row(controller, "Sine1")["blockX"] == 0
    assert block_row(controller, "Gain1")["blockX"] == 200


def test_selection(controller: DiagramController) -> None:
    controller.addBlock("Sine", 0, 0)
    controller.addBlock("Gain", 200, 0)
    controller.select("Sine1", False)
    assert controller.property("selectedBlock")["id"] == "Sine1"
    controller.select("Gain1", True)
    assert controller.property("selectedBlock") == {}  # two selected
    controller.select("Sine1", True)
    assert controller.property("selectedBlock")["id"] == "Gain1"
    controller.clearSelection()
    assert not any(r["selected"] for r in rows(controller.blocks))
    controller.selectInRect(250, 10, -100, 20, False)  # dragged right-to-left
    assert [r["selected"] for r in rows(controller.blocks)] == [False, True]


def test_remove_selected_and_undo_restores_wires(controller: DiagramController) -> None:
    chain(controller)
    controller.select("Gain1", False)
    controller.removeSelected()
    assert [r["blockId"] for r in rows(controller.blocks)] == ["Sine1", "Integrator1", "Scope1"]
    assert [(w.src, w.dst) for w in controller.diagram.wires] == [("Integrator1.y", "Scope1.u")]
    assert controller.property("selectedBlock") == {}
    controller.undo()
    ids = {r["blockId"] for r in rows(controller.blocks)}
    assert ids == {"Sine1", "Gain1", "Integrator1", "Scope1"}
    assert {(w.src, w.dst) for w in controller.diagram.wires} == {
        ("Sine1.y", "Gain1.u"),
        ("Gain1.y", "Integrator1.u"),
        ("Integrator1.y", "Scope1.u"),
    }
    assert len(rows(controller.wires)) == 3
    controller.redo()
    assert len(rows(controller.blocks)) == 3


def test_select_wire_and_remove(controller: DiagramController) -> None:
    chain(controller)
    w = rows(controller.wires)[1]
    mid_x = (w["x1"] + w["x2"]) / 2
    mid_y = (w["y1"] + w["y2"]) / 2
    index = controller.wireAt(mid_x, mid_y, 5)
    assert index == 1
    assert controller.wireAt(mid_x, mid_y + 200, 5) == -1
    controller.selectWire(index, False)
    assert rows(controller.wires)[1]["selected"] is True
    controller.removeSelected()
    assert len(controller.diagram.wires) == 2
    assert len(controller.diagram.blocks) == 4
    controller.undo()
    assert len(controller.diagram.wires) == 3


def test_disconnect_and_undo(controller: DiagramController) -> None:
    chain(controller)
    assert controller.disconnectWire(0) is True
    assert controller.property("undoText") == "disconnect Sine1.y→Gain1.u"
    assert controller.diagram.wire_into("Gain1", "u") is None
    controller.undo()
    assert controller.diagram.wire_into("Gain1", "u") is not None
    assert controller.disconnectWire(99) is False


def test_port_at(controller: DiagramController) -> None:
    controller.addBlock("Sum", 100, 100)
    a = block_row(controller, "Sum1")["inputs"][0]
    assert controller.portAt(100 + a["x"] + 3, 100 + a["y"] - 2) == "Sum1.a"
    assert controller.portAt(100 + a["x"] + 40, 100 + a["y"]) == ""


def test_set_setting_parses_and_undoes(controller: DiagramController) -> None:
    controller.addBlock("Gain", 0, 0)
    controller.addBlock("Scope", 200, 0)
    controller.select("Gain1", False)
    assert controller.setSetting("Gain1", "k", "2") is True
    assert controller.diagram.effective_settings("Gain1")["k"] == 2.0
    [k] = controller.property("selectedBlock")["settings"]
    assert k == {"name": "k", "value": 2.0, "text": "2.0"}
    assert controller.setSetting("Gain1", "k", "two") is False
    assert "not a number" in controller.property("hint")
    assert controller.setSetting("Scope1", "label", " out ") is True
    assert controller.diagram.effective_settings("Scope1")["label"] == " out "
    controller.undo()
    assert controller.diagram.effective_settings("Scope1")["label"] == ""
    controller.undo()
    assert controller.diagram.effective_settings("Gain1")["k"] == 1.0
    assert controller.setSetting("Gain1", "nope", "1") is False


@pytest.mark.parametrize(
    "text, default, value",
    [
        ("3", 1, 3),
        ("1e-3", 1.0, 0.001),
        ("yes", False, True),
        ("False", True, False),
        ("[1, 2.5]", [0], [1, 2.5]),
        ("None", None, None),
        ("'a'", None, "a"),
    ],
)
def test_parse_setting(text: str, default: object, value: object) -> None:
    assert parse_setting(text, default) == value


@pytest.mark.parametrize(
    "text, default", [("1.5", 1), ("x", 1.0), ("maybe", True), ("nan", 1.0), ("os", None)]
)
def test_parse_setting_rejects(text: str, default: object) -> None:
    with pytest.raises(ValueError):
        parse_setting(text, default)


def test_algebraic_loop_marks_blocks_without_running(controller: DiagramController) -> None:
    controller.addBlock("Gain", 0, 0)
    controller.addBlock("Gain", 200, 0)
    controller.addBlock("Scope", 400, 0)
    controller.connectPorts("Gain1.y", "Gain2.u")
    controller.connectPorts("Gain2.y", "Gain1.u")
    controller.connectPorts("Gain2.y", "Scope1.u")
    assert controller.run(1.0, 0.01) is False
    assert controller.property("running") is False
    assert {r["blockId"] for r in rows(controller.blocks) if r["inLoop"]} == {"Gain1", "Gain2"}
    assert "algebraic loop" in controller.property("runStatus")
    # Any edit clears the marks.
    controller.disconnectWire(1)
    assert not any(r["inLoop"] for r in rows(controller.blocks))


def test_run_checks_before_starting(controller: DiagramController) -> None:
    assert controller.run(1.0, 0.01) is False
    assert "empty" in controller.property("runStatus")
    controller.addBlock("Gain", 0, 0)
    assert controller.run(1.0, 0.0) is False
    assert "time step" in controller.property("runStatus")
    assert controller.run(1.0, 0.01) is False
    assert "unconnected" in controller.property("runStatus")


def test_run_sine_gain_integrator_scope(controller: DiagramController, qtbot) -> None:
    waveforms = WaveformModel()
    controller.attachWaveforms(waveforms)
    chain(controller)
    controller.setSetting("Gain1", "k", "2")
    with qtbot.waitSignal(controller.resultReady, timeout=20_000):
        assert controller.run(1.0, 0.001) is True
        assert controller.property("running") is True
    assert controller.property("running") is False
    assert controller.property("runStatus") == "Finished: 1001 steps (Scope1)"
    assert waveforms.property("traceNames") == ["Scope1"]
    t = controller.result.vectors["time"]
    y = controller.result.vectors["Scope1"]
    # Integral of 2 sin(2 pi t) is (1 - cos(2 pi t)) / pi: a cosine-shaped curve.
    np.testing.assert_allclose(y, (1 - np.cos(2 * np.pi * t)) / np.pi, atol=5e-3)


def test_stop_cancels_a_hanging_block(controller: DiagramController, qtbot) -> None:
    assert controller.apply("@block\ndef hang(u):\n    while True:\n        pass\n")
    controller.addBlock("Constant", 0, 0)
    controller.addBlock("hang", 200, 0)
    controller.addBlock("Scope", 400, 0)
    controller.connectPorts("Constant1.y", "hang1.u")
    controller.connectPorts("hang1.y", "Scope1.u")
    assert controller.run(1.0, 0.1) is True
    qtbot.wait(500)
    controller.stop()
    qtbot.waitUntil(lambda: not controller.property("running"), timeout=10_000)
    assert controller.property("runStatus") == "Stopped"


def test_failed_run_reports_the_block(controller: DiagramController, qtbot) -> None:
    assert controller.apply('@block\ndef boom(u):\n    raise ValueError("bad")\n')
    controller.addBlock("Constant", 0, 0)
    controller.addBlock("boom", 200, 0)
    controller.addBlock("Scope", 400, 0)
    controller.connectPorts("Constant1.y", "boom1.u")
    controller.connectPorts("boom1.y", "Scope1.u")
    assert controller.run(1.0, 0.1) is True
    qtbot.waitUntil(lambda: not controller.property("running"), timeout=20_000)
    assert controller.property("runStatus").startswith("Failed:")
    assert "boom1" in controller.property("runStatus")
    assert "Traceback" in controller.property("runDetails")
