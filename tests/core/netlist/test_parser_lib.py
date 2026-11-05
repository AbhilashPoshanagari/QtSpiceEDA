"""L1: ``.lib file``, ``.lib file section`` / ``.endl`` and ``.param`` inside ``.subckt``."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.netlist.builder import build
from core.netlist.model import ERROR, WARNING
from core.netlist.parser import parse

LIB = Path(__file__).resolve().parents[2] / "fixtures" / "lib"


@pytest.mark.parametrize(
    ("section", "rsheet", "is_"),
    [("tt", "100", "1e-14"), ("ff", "80", "2e-14"), ("ss", "120", "5e-15"), ("TT", "100", "1e-14")],
)
def test_corner_library_selects_section(section: str, rsheet: str, is_: str) -> None:
    result = parse(f"t\n.lib corner.lib {section}\n.op\n", LIB)
    assert result.diagnostics == []
    [model] = result.circuit.models
    assert (model.name, model.type, model.params["is"]) == ("dcorner", "D", is_)
    assert result.circuit.params == {"rsheet": rsheet}
    assert model.source_line == 2  # reported at the .lib line


def test_quoted_file_and_section() -> None:
    result = parse("t\n.lib 'corner.lib' 'ff'\n", LIB)
    assert result.diagnostics == []
    assert result.circuit.params == {"rsheet": "80"}


def test_unknown_section_lists_the_sections() -> None:
    result = parse("t\n.lib corner.lib fast\n", LIB)
    [d] = result.diagnostics
    assert (d.line, d.col, d.severity) == (2, 17, ERROR)
    assert "no library section 'fast' in corner.lib (it has tt, ff, ss)" in d.message
    assert result.circuit.models == []


def test_sectioned_library_without_section_warns_and_reads_nothing() -> None:
    result = parse("t\n.lib corner.lib\n", LIB)
    [d] = result.diagnostics
    assert (d.line, d.severity) == (2, WARNING)
    assert "has library sections (tt, ff, ss)" in d.message
    assert result.circuit.models == [] and result.circuit.params == {}


def test_lib_include_form() -> None:
    result = parse("t\n.lib opamp.lib\n", LIB)
    assert result.diagnostics == []
    assert [s.name for s in result.circuit.subcircuits] == ["opamp5"]


def test_section_may_select_another_section_of_the_same_file(tmp_path: Path) -> None:
    (tmp_path / "pdk.lib").write_text(
        ".lib tt\n.lib pdk.lib mos_tt\n.param vdd=1.8\n.endl tt\n"
        ".lib mos_tt\n.model nch NMOS (level=1)\n.endl mos_tt\n"
    )
    result = parse("t\n.lib pdk.lib tt\n", tmp_path)
    assert result.diagnostics == []
    assert [m.name for m in result.circuit.models] == ["nch"]
    assert result.circuit.params == {"vdd": "1.8"}


def test_section_loop_is_reported(tmp_path: Path) -> None:
    (tmp_path / "loop.lib").write_text(".lib a\n.lib loop.lib a\n.endl a\n")
    result = parse("t\n.lib loop.lib a\n", tmp_path)
    assert any("includes itself" in d.message for d in result.diagnostics)


def test_missing_endl_and_mismatched_endl(tmp_path: Path) -> None:
    (tmp_path / "open.lib").write_text(".lib tt\n.param x=1\n")
    (tmp_path / "odd.lib").write_text(".lib tt\n.param x=1\n.endl ff\n")
    result = parse("t\n.lib open.lib tt\n.lib odd.lib tt\n", tmp_path)
    by_line = {d.line: d for d in result.diagnostics}
    assert by_line[2].severity == ERROR
    assert "open.lib line 1: missing .endl for .lib tt" in by_line[2].message
    assert by_line[3].severity == WARNING
    assert "odd.lib line 3: .endl ff closes tt" in by_line[3].message
    assert result.circuit.params == {"x": "1"}


def test_endl_without_section_and_lib_without_file() -> None:
    result = parse("t\n.endl\n.lib\n")
    messages = {d.line: d.message for d in result.diagnostics}
    assert ".endl without .lib section" in messages[2]
    assert ".lib needs a file name" in messages[3]


def test_param_inside_subckt() -> None:
    result = parse("t\n.include param_in_subckt.lib\nX1 a b 0 rdiv\n", LIB)
    assert result.diagnostics == []
    [sub] = result.circuit.subcircuits
    assert sub.local_params == {"rtop": "10k", "ratio": "0.5", "rbot": "{rtop*ratio/(1-ratio)}"}
    assert list(sub.local_params) == ["rtop", "ratio", "rbot"]
    assert result.circuit.params == {}
    deck = build(result.circuit)
    assert ".param rtop=10k\n.param ratio=0.5\n.param rbot={rtop*ratio/(1-ratio)}\n" in deck
    again = parse(deck)
    assert again.diagnostics == []
    assert again.circuit == result.circuit


@pytest.mark.parametrize(
    "card",
    [
        ".lib opamp.lib",
        ".include opamp.lib",
        ".include param_in_subckt.lib",
        ".lib tsmc018.lib",
        ".lib corner.lib tt",
        ".lib corner.lib ff",
        ".lib corner.lib ss",
    ],
)
def test_fixture_libraries_parse_without_errors(card: str) -> None:
    result = parse(f"t\n{card}\n", LIB)
    assert [d for d in result.diagnostics if d.severity == ERROR] == []
