from __future__ import annotations

import pytest

from core.netlist.model import ERROR
from core.netlist.parser import parse
from core.netlist.validate import validate
from tests.netlists import BROKEN, BROKEN_EXPECTED, GOOD_FILES, INCLUDE_FILE


def check(text: str) -> list[tuple[int, str]]:
    result = parse(text)
    return [(d.line, d.message) for d in result.diagnostics + validate(result.circuit)]


@pytest.mark.parametrize("path", [*GOOD_FILES, INCLUDE_FILE], ids=lambda p: p.name)
def test_good_examples_are_clean(path) -> None:
    result = parse(path.read_text(), path.parent)
    assert validate(result.circuit) == []


@pytest.mark.parametrize("name", sorted(BROKEN_EXPECTED))
def test_broken_examples_report_every_error_on_its_line(name: str) -> None:
    path = BROKEN / name
    result = parse(path.read_text(), path.parent)
    found = result.diagnostics + validate(result.circuit)
    expected = BROKEN_EXPECTED[name]
    assert len(found) == len(expected), found
    for diag, (line, fragment) in zip(found, expected, strict=True):
        assert diag.severity == ERROR
        assert diag.line == line, diag
        assert fragment in diag.message, diag


def test_ground_alias_gnd() -> None:
    assert check("t\nV1 a gnd 1\nR1 a gnd 1k\n.op\n") == []


def test_empty_netlist() -> None:
    assert check("t\n") == [
        (1, "the netlist has no elements"),
        (1, "no analysis card (.op, .dc, .ac or .tran)"),
    ]


def test_missing_analysis_reported_on_end_line() -> None:
    assert check("t\nR1 a 0 1k\nR2 a 0 1k\n\n.end\n") == [
        (5, "no analysis card (.op, .dc, .ac or .tran)")
    ]


def test_floating_node_column() -> None:
    result = parse("t\nR1 a 0 1k\nR2 a   lonely 1k\n.op\n")
    [diag] = validate(result.circuit)
    assert (diag.line, diag.col) == (3, 8)


def test_duplicate_names_are_case_insensitive() -> None:
    assert check("t\nR1 a 0 1k\nr1 a 0 1k\n.op\n") == [
        (3, "duplicate element name 'r1' (first used on line 2)")
    ]


def test_unknown_subcircuit_and_port_mismatch() -> None:
    text = "t\n.subckt amp in out\nR1 in out 1k\n.ends\nX1 a 0 nope\nX2 a 0 b amp\nR2 b 0 1\n.op\n"
    messages = check(text)
    assert (5, "unknown subcircuit 'nope' used by X1") in messages
    assert (6, "X2 has 3 nodes but subcircuit amp has 2 ports") in messages


def test_subcircuit_scope() -> None:
    text = (
        "t\n"
        ".subckt cell a b\n"
        ".model local D\n"
        "D1 a mid local\n"
        "R1 mid b 1k\n"
        "R2 b dangling 1k\n"
        ".ends\n"
        "X1 in 0 cell\n"
        "V1 in 0 1\n"
        "D2 in 0 local\n"
        ".op\n"
    )
    messages = check(text)
    assert (6, "node 'dangling' in subcircuit cell has only one connection (R2)") in messages
    # A model defined inside a subcircuit is not visible outside it.
    assert (10, "unknown model 'local' used by D2") in messages
    assert len(messages) == 2


def test_unconnected_subcircuit_port() -> None:
    messages = check("t\n.subckt s a b c\nR1 a b 1k\n.ends\nX1 1 0 2 s\nR2 1 2 1\nV1 1 0 1\n.op\n")
    assert (2, "port 'c' of subcircuit s is not connected inside it") in messages
