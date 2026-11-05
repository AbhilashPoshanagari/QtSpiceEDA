from __future__ import annotations

import math
import random
from pathlib import Path

import pytest

from core.netlist.model import ERROR, WARNING, Analysis, Element, Model
from core.netlist.parser import comment_start, parse, parse_number
from tests.netlists import GOOD, GOOD_FILES, INCLUDE_FILE


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1", 1.0),
        ("-2.5", -2.5),
        (".5", 0.5),
        ("1e3", 1e3),
        ("2.2E-6", 2.2e-6),
        ("1f", 1e-15),
        ("3p", 3e-12),
        ("4n", 4e-9),
        ("10u", 10e-6),
        ("5m", 5e-3),
        ("5M", 5e-3),
        ("4.7k", 4.7e3),
        ("4.7K", 4.7e3),
        ("1meg", 1e6),
        ("1MEG", 1e6),
        ("1Meg", 1e6),
        ("2g", 2e9),
        ("1t", 1e12),
        ("10uF", 10e-6),
        ("1kohm", 1e3),
        ("3V", 3.0),
        ("10mil", 254e-6),
        ("1MIL", 25.4e-6),
        ("2mA", 2e-3),
    ],
)
def test_parse_number(text: str, value: float) -> None:
    result = parse_number(text)
    assert result is not None
    assert math.isclose(result, value, rel_tol=1e-12)


@pytest.mark.parametrize("text", ["", "k", "abc", "4.7.2k", "1k5", "--1", "{x}", "1.2.3"])
def test_parse_number_rejects(text: str) -> None:
    assert parse_number(text) is None


def test_title_elements_and_cards() -> None:
    text = (
        "My title\n"
        "V1 in 0 DC 0 AC 1 SIN(0 1 1k)\n"
        "R1 in out 1k\n"
        "C1 out 0 1u ic=0\n"
        ".model dmod D (is=1e-14 n=1)\n"
        ".param rload=10k\n"
        ".tran 1u 1m\n"
        ".end\n"
    )
    result = parse(text)
    c = result.circuit
    assert result.diagnostics == []
    assert c.title == "My title"
    assert c.elements[0] == Element("V1", "V", ["in", "0"], "DC 0 AC 1 SIN(0 1 1k)")
    assert c.elements[2] == Element("C1", "C", ["out", "0"], "1u", {"ic": "0"})
    assert c.models == [Model("dmod", "D", {"is": "1e-14", "n": "1"})]
    assert c.params == {"rload": "10k"}
    assert c.analyses == [Analysis("tran", ["1u", "1m"])]
    assert c.end_line == 8
    assert [e.source_line for e in c.elements] == [2, 3, 4]


def test_continuation_keeps_original_line_numbers() -> None:
    text = "t\nV1 in 0\n* comment between\n+ DC 5\nR1 in 0\n+ 4.7.2k\n.op\n"
    result = parse(text)
    assert result.circuit.elements[0].value == "DC 5"
    assert result.circuit.elements[0].source_line == 2
    [diag] = result.diagnostics
    assert (diag.line, diag.col, diag.severity) == (6, 3, ERROR)
    assert "bad number" in diag.message


def test_orphan_continuation() -> None:
    result = parse("t\n+ R1 a b 1k\n")
    assert [d.line for d in result.diagnostics] == [2]


def test_comments_are_stripped() -> None:
    text = "t\n* full line\n   * indented\nR1 a 0 1k ; trailing\nR2 a 0 2k;tight\n"
    result = parse(text)
    assert result.diagnostics == []
    assert [e.value for e in result.circuit.elements] == ["1k", "2k"]


def test_dollar_inline_comments_are_stripped() -> None:
    text = "t\n$ whole line\nV1 a 0 1 $ source\nR1 a 0 1k\t$tab\nR2 a 0 2k\n.op $ op\n"
    result = parse(text)
    assert result.diagnostics == []
    assert [e.value for e in result.circuit.elements] == ["1", "1k", "2k"]
    assert len(result.circuit.analyses) == 1


def test_dollar_inside_a_token_is_not_a_comment() -> None:
    result = parse("t\nR1 a$b 0 1k\nR2 a$b 0 1k\n.op\n")
    assert result.diagnostics == []
    assert result.circuit.elements[0].nodes == ["a$b", "0"]


def test_case_insensitive_cards_and_lowercase_nodes() -> None:
    result = parse("t\nR1 IN Out 1K\n.TRAN 1U 1M\n.END\n")
    assert result.diagnostics == []
    assert result.circuit.elements[0].nodes == ["in", "out"]
    assert result.circuit.analyses[0].kind == "tran"


def test_every_element_kind() -> None:
    text = (
        "t\n"
        "R1 a 0 1k\n"
        "C1 a 0 1p\n"
        "L1 a 0 1m\n"
        "V1 a 0 DC 1\n"
        "I1 a 0 1m\n"
        "D1 a 0 dmod\n"
        "Q1 c b e qmod\n"
        "Q2 c b e s qmod\n"
        "Q3 c b e qmod 2\n"
        "M1 d g s b nmod L=1u W=10u\n"
        "E1 o 0 a 0 10\n"
        "G1 o 0 a 0 1m\n"
        "F1 o 0 V1 2\n"
        "H1 o 0 V1 1k\n"
        "X1 a b c sub params: k=2\n"
        ".model dmod D\n.model qmod NPN\n.model nmod NMOS\n"
    )
    result = parse(text)
    assert result.diagnostics == []
    by_name = {e.name: e for e in result.circuit.elements}
    assert by_name["Q1"].nodes == ["c", "b", "e"]
    assert by_name["Q2"].nodes == ["c", "b", "e", "s"]
    assert by_name["Q3"].nodes == ["c", "b", "e"]
    assert by_name["Q3"].value == "qmod 2"
    assert by_name["M1"].params == {"l": "1u", "w": "10u"}
    assert by_name["E1"].nodes == ["o", "0", "a", "0"]
    assert by_name["F1"].value == "V1 2"
    assert by_name["X1"] == Element("X1", "X", ["a", "b", "c"], "sub", {"k": "2"})


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("R1 a 0", "has no value"),
        ("R1 a", "needs 2 nodes"),
        ("R1 a 0 abc", "bad number 'abc'"),
        ("R1 a 0 {x", "unclosed '{'"),
        ("V1 a 0 DC x1", "bad number 'x1'"),
        ("D1 a 0", "needs a model name"),
        ("Q1 c b", "needs 3 nodes"),
        ("M1 d g s", "needs 4 nodes"),
        ("E1 o 0 a 0 big", "bad number 'big'"),
        ("F1 o 0 V1", "controlling source"),
        ("X1 sub", "at least one node"),
        ("Z1 a b c", "not supported"),
        ("91 a b 1k", "unknown element type"),
        ("R1 a 0 1k tc=", "has no value"),
        (".model dmod", "needs a name and a type"),
        (".param", "needs name=value"),
        (".param 5", "expected name=value"),
        (".dc V1 0 5", ".dc needs"),
        (".dc V1 0 x 1", "bad number 'x'"),
        (".ac dec 10 1", ".ac needs"),
        (".ac log 10 1 1k", "not dec, oct or lin"),
        (".tran 1u", ".tran needs"),
        (".tran 1u 1m 0 1u 5", ".tran needs"),
        (".include", "needs a file name"),
        (".include nofile.lib", "relative .include"),
        (".ends", ".ends without .subckt"),
        (".endc", ".endc without .control"),
        (".options reltol=1e-4", "not supported"),
    ],
)
def test_bad_lines_report_on_their_line(line: str, fragment: str) -> None:
    result = parse(f"title\n* comment\n{line}\n.op\n")
    assert result.diagnostics, line
    assert all(d.line == 3 for d in result.diagnostics), result.diagnostics
    assert any(fragment in d.message for d in result.diagnostics), result.diagnostics


def test_op_with_arguments_warns() -> None:
    result = parse("t\n.op now\n")
    assert [d.severity for d in result.diagnostics] == [WARNING]
    assert result.circuit.analyses == [Analysis("op", [])]


def test_tran_uic() -> None:
    result = parse("t\n.tran 1u 1m 0 1u uic\n")
    assert result.diagnostics == []
    assert result.circuit.analyses[0].args == ["1u", "1m", "0", "1u", "uic"]


def test_subckt_block() -> None:
    text = "t\n.subckt amp in out params: g=2\nR1 in out 1k\n.model m D\n.ends amp\nX1 a b amp\n"
    result = parse(text)
    assert result.diagnostics == []
    [sub] = result.circuit.subcircuits
    assert (sub.name, sub.ports, sub.params) == ("amp", ["in", "out"], {"g": "2"})
    assert sub.source_line == 2
    assert [e.name for e in sub.elements] == ["R1"]
    assert [m.name for m in sub.models] == ["m"]
    assert [e.name for e in result.circuit.elements] == ["X1"]


def test_subckt_errors() -> None:
    result = parse("t\n.subckt a x\n.subckt b y\n.tran 1u 1m\nR1 x 0 1\n.end\n")
    by_line = {d.line: d.message for d in result.diagnostics}
    assert "nested" in by_line[3]
    assert ".tran" in by_line[4]
    assert "missing .ends" in by_line[2]
    assert [s.name for s in result.circuit.subcircuits] == ["a"]


def test_lines_after_end_are_ignored() -> None:
    result = parse("t\nR1 a 0 1k\n.end\nthis is not spice\n")
    assert result.diagnostics == []
    assert result.circuit.end_line == 3


def test_control_block_is_skipped_with_one_warning() -> None:
    result = parse("t\nR1 a 0 1k\n.control\nrun\nplot v(a)\n.endc\n.op\n")
    assert [(d.line, d.severity) for d in result.diagnostics] == [(3, WARNING)]
    assert len(result.circuit.analyses) == 1


def test_include_relative_to_base_dir() -> None:
    result = parse(INCLUDE_FILE.read_text(), INCLUDE_FILE.parent)
    assert result.diagnostics == []
    [sub] = result.circuit.subcircuits
    assert sub.name == "opamp"
    assert sub.source_line == 2  # reported at the .include line


def test_include_errors_reported_on_include_line(tmp_path: Path) -> None:
    (tmp_path / "bad.lib").write_text("* lib\nR9 a b 1q2\n.end\n")
    (tmp_path / "loop.lib").write_text(".include loop.lib\n")
    text = "t\n.include bad.lib\n.include missing.lib\n.include loop.lib\n.op\n"
    result = parse(text, tmp_path)
    by_line = {d.line: d.message for d in result.diagnostics}
    assert "bad.lib line 2: bad number '1q2'" in by_line[2]
    assert "cannot read include file 'missing.lib'" in by_line[3]
    assert "includes itself" in by_line[4]
    # The included file's .end does not end the main netlist.
    assert len(result.circuit.analyses) == 1


def test_include_absolute_path_without_base_dir() -> None:
    lib = (GOOD / "opamp.lib").resolve()
    result = parse(f't\n.include "{lib}"\n')
    assert result.diagnostics == []
    assert result.circuit.subcircuits[0].name == "opamp"


@pytest.mark.parametrize("path", GOOD_FILES, ids=lambda p: p.name)
def test_good_examples_parse_without_diagnostics(path: Path) -> None:
    assert parse(path.read_text(), path.parent).diagnostics == []


def test_never_raises_on_garbage() -> None:
    rng = random.Random(1234)
    alphabet = "RCLVIDQMEFGHXZ.+*;=(){}'\"\n \t0123456789kmeguMEGabc,:\\\x00é"
    samples = ["", "\n", "\n\n\n", ".end", "t\n.subckt", "t\n.model", "t\n=", "t\n(", "t\n{"]
    samples += [
        "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 200))) for _ in range(500)
    ]
    for text in samples:
        result = parse(text)
        assert result.circuit is not None
        assert all(not d.message.startswith("internal") for d in result.diagnostics), text


def test_non_string_input_does_not_raise() -> None:
    result = parse(None)  # type: ignore[arg-type]
    assert result.circuit is not None


@pytest.mark.parametrize(
    ("text", "index"),
    [
        ("R1 a 0 1k", -1),
        ("R1 a 0 1k ; x", 10),
        ("R1 a 0 1k $ x", 10),
        ("$ x", 0),
        ("R1 a$b 0 1k", -1),
        ("R1 a 0 1k $ y ; z", 10),
        ("R1 a 0 1k;z $ y", 9),
    ],
)
def test_comment_start(text: str, index: int) -> None:
    assert comment_start(text) == index


# ---- O1: line 1 is the title, warn when it looks like an element


@pytest.mark.parametrize(
    "first",
    [
        "R1 1 0 1k",
        "Rload out 0 1k",
        "R1 in out 1k",
        "V1 in 0 DC 5",
        "C2 a b 1u ; cap",
        "q1 c b e qm",
    ],
)
def test_element_on_line_1_warns_with_quick_fix(first: str) -> None:
    result = parse(f"{first}\nR2 1 0 1k\n.model qm NPN\n.op\n")
    [d] = [d for d in result.diagnostics if d.line == 1]
    assert (d.col, d.severity, d.fix) == (1, WARNING, "insert_title")
    assert "looks like an element" in d.message
    assert result.circuit.title == first  # still the title, as in SPICE
    assert [e.name for e in result.circuit.elements] == ["R2"]


@pytest.mark.parametrize(
    "first",
    [
        "RC low-pass filter, fc = 1.59 kHz",
        "Diode half-wave rectifier",
        "RC filter test",
        "BJT common-emitter amplifier",
        "* R1 1 0 1k",
        ".include x.lib",
        "",
        "R1 1 0",
    ],
)
def test_ordinary_titles_do_not_warn(first: str) -> None:
    result = parse(f"{first}\nR2 1 0 1k\n.op\n")
    assert [d for d in result.diagnostics if d.line == 1] == []


def test_fix_does_not_change_equality() -> None:
    from core.netlist.model import Diagnostic

    assert Diagnostic(1, 1, WARNING, "m", fix="insert_title") == Diagnostic(1, 1, WARNING, "m")
