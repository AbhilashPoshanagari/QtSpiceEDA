"""L4: AsyParser reads .asy files; pins come out in SpiceOrder."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.netlist.model import ERROR, WARNING
from core.symbols.asy import AsyParser

ASY = Path(__file__).resolve().parents[2] / "fixtures" / "asy"


@pytest.mark.parametrize("path", sorted(ASY.glob("*.asy")), ids=lambda p: p.name)
def test_every_fixture_parses_cleanly(path: Path) -> None:
    symbol, diagnostics = AsyParser.parse(path)
    assert diagnostics == []
    assert symbol.name == path.stem
    assert symbol.path == path
    assert symbol.version == 4
    assert symbol.type == "CELL"
    assert symbol.pins
    orders = [p.spice_order for p in symbol.pins]
    assert orders == sorted(orders)


def test_opamp5_pins_follow_spice_order_not_drawing_order() -> None:
    symbol, _ = AsyParser.parse(ASY / "opamp5.asy")
    assert [p.name for p in symbol.pins] == ["In+", "In-", "V+", "V-", "OUT"]
    assert [p.index for p in symbol.pins] == [1, 3, 2, 4, 0]
    assert (symbol.pins[0].x, symbol.pins[0].y) == (-64, 80)
    assert symbol.prefix == "X"
    assert symbol.attr("modelfile") == "opamp.lib"
    assert symbol.attr("Value") == "opamp5"
    assert len(symbol.lines) == 8
    assert len(symbol.circles) == 1 and len(symbol.rects) == 1
    [arc] = symbol.arcs
    assert (arc.x1, arc.y1, arc.x2, arc.y2, arc.x3, arc.y3, arc.x4, arc.y4) == (
        -8,
        -8,
        8,
        8,
        8,
        0,
        -8,
        0,
    )
    [text] = symbol.texts
    assert (text.x, text.y, text.align, text.size, text.text) == (0, 64, "Center", 1, "op")
    assert [(w.index, w.x, w.y) for w in symbol.windows] == [(0, 16, 32), (3, 16, 96)]
    assert symbol.bbox() == (-64, -8, 64, 104)


def test_micro_sign_is_read_as_windows_1252() -> None:
    symbol, diagnostics = AsyParser.parse(ASY / "micro_cp1252.asy")
    assert diagnostics == []
    assert symbol.attr("Value") == "4.7\u00b5"
    assert symbol.attr("Description").startswith("4.7 \u00b5F cap;")


def test_real_cmos_symbols() -> None:
    n, _ = AsyParser.parse(ASY / "cmosn.asy")
    p, _ = AsyParser.parse(ASY / "cmosp.asy")
    assert [x.name for x in n.pins] == ["D", "G", "S", "B"]
    assert (n.prefix, n.attr("Value"), n.attr("ModelFile")) == ("MN", "CMOSN", "tsmc018.lib")
    assert (p.prefix, p.attr("Value")) == ("MP", "CMOSP")


def test_crlf_and_lf_give_the_same_symbol() -> None:
    text = (ASY / "res.asy").read_bytes().decode("cp1252").replace("\r\n", "\n")
    lf, _ = AsyParser.parse_text(text, "res")
    crlf, _ = AsyParser.parse_text(text.replace("\n", "\r\n"), "res")
    assert lf == crlf


def test_unknown_keyword_is_a_warning() -> None:
    symbol, diagnostics = AsyParser.parse_text(
        "Version 4\nSymbolType CELL\nFOO 1 2\nPIN 0 0 NONE 0\nPINATTR SpiceOrder 1\n"
    )
    assert [(d.line, d.severity) for d in diagnostics] == [(3, WARNING)]
    assert "FOO" in diagnostics[0].message
    assert len(symbol.pins) == 1


def test_malformed_lines_are_skipped_with_warnings() -> None:
    text = "\n".join(
        [
            "Version x",
            "SymbolType WEIRD",
            "LINE Normal 1 2 3",
            "RECTANGLE Normal a b c d",
            "TEXT 0 0 Left",
            "SYMATTR",
            "PINATTR PinName A",
            "PIN 0",
            "WINDOW 0 1",
        ]
    )
    symbol, diagnostics = AsyParser.parse_text(text)
    assert [d.line for d in diagnostics] == list(range(1, 10))
    assert all(d.severity == WARNING for d in diagnostics)
    assert symbol.lines == [] and symbol.rects == [] and symbol.pins == []
    assert symbol.type == "CELL"


def test_shape_without_pen_name_and_with_style() -> None:
    symbol, diagnostics = AsyParser.parse_text("LINE Normal 0 0 16 16 2\nCIRCLE 0 0 8 8\n")
    assert diagnostics == []
    assert symbol.lines[0].style == 2
    assert (symbol.circles[0].x2, symbol.circles[0].y2) == (8, 8)


def test_missing_and_duplicate_spice_order() -> None:
    text = (
        "PIN 0 0 NONE 0\nPINATTR PinName A\n"
        "PIN 0 16 NONE 0\nPINATTR PinName B\nPINATTR SpiceOrder 1\n"
        "PIN 0 32 NONE 0\nPINATTR PinName C\nPINATTR SpiceOrder 1\n"
    )
    symbol, diagnostics = AsyParser.parse_text(text)
    assert [p.name for p in symbol.pins] == ["B", "C", "A"]
    assert [(d.line, d.severity) for d in diagnostics] == [(1, WARNING), (6, ERROR)]


def test_duplicate_symattr_last_wins() -> None:
    symbol, diagnostics = AsyParser.parse_text("SYMATTR Value 1k\nSYMATTR value 2k\n")
    assert symbol.attr("VALUE") == "2k"
    assert len(diagnostics) == 1 and diagnostics[0].severity == WARNING


def test_symattr_value_keeps_spaces_and_may_be_empty() -> None:
    symbol, diagnostics = AsyParser.parse_text("SYMATTR Value2 AC 1  0\nSYMATTR SpiceLine\n")
    assert diagnostics == []
    assert symbol.attr("Value2") == "AC 1  0"
    assert symbol.attr("SpiceLine") == ""


def test_missing_file_is_a_diagnostic(tmp_path: Path) -> None:
    symbol, diagnostics = AsyParser.parse(tmp_path / "nope.asy")
    assert symbol.name == "nope" and symbol.pins == []
    assert [d.severity for d in diagnostics] == [ERROR]


def test_undefined_cp1252_byte_is_replaced_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "odd.asy"
    path.write_bytes(b"SYMATTR Value x\x81y\r\n")
    symbol, diagnostics = AsyParser.parse(path)
    assert symbol.attr("Value") == "x\ufffdy"
    assert [d.severity for d in diagnostics] == [WARNING]
