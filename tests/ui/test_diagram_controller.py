from __future__ import annotations

import pytest

from bridge.diagram_controller import DiagramController

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
