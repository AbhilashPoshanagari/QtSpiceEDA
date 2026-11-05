from __future__ import annotations

from core.netlist.model import ERROR, WARNING, Circuit, Diagnostic, Element, ParseResult


def test_element_equality_ignores_positions() -> None:
    a = Element("R1", "R", ["in", "out"], "1k", source_line=3, node_cols=[4, 7])
    b = Element("R1", "R", ["in", "out"], "1k", source_line=9)
    assert a == b


def test_element_reference() -> None:
    assert Element("D1", "D", ["a", "k"], "dmod 2").reference() == "dmod"
    assert Element("X1", "X", ["a", "b"], "opamp").reference() == "opamp"
    assert Element("R1", "R", ["a", "b"], "1k").reference() is None
    assert Element("Q1", "Q", ["c", "b", "e"], "").reference() is None


def test_circuit_nodes_in_first_appearance_order() -> None:
    circuit = Circuit(
        elements=[
            Element("V1", "V", ["in", "0"], "DC 1"),
            Element("R1", "R", ["in", "out"], "1k"),
            Element("C1", "C", ["out", "0"], "1u"),
        ]
    )
    assert circuit.nodes() == ["in", "0", "out"]


def test_parse_result_ok() -> None:
    assert ParseResult(Circuit()).ok
    assert ParseResult(Circuit(), [Diagnostic(1, 1, WARNING, "w")]).ok
    assert not ParseResult(Circuit(), [Diagnostic(1, 1, ERROR, "e")]).ok


def test_diagnostic_to_dict_adds_fix_only_when_set() -> None:
    from core.netlist.model import Diagnostic

    plain = Diagnostic(2, 3, "error", "m")
    assert plain.to_dict() == {"line": 2, "col": 3, "severity": "error", "message": "m"}
    assert Diagnostic(1, 1, "warning", "m", fix="insert_title").to_dict()["fix"] == "insert_title"
