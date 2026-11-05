"""L2: LibraryIndex.scan and encrypted-file detection."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.netlist.libindex import LibraryIndex, encrypted_block_line
from core.netlist.model import ERROR
from core.netlist.parser import parse

LIB = Path(__file__).resolve().parents[2] / "fixtures" / "lib"


@pytest.fixture(scope="module")
def index() -> LibraryIndex:
    return LibraryIndex.scan([LIB])


def test_fixture_folder_scans_cleanly(index: LibraryIndex) -> None:
    assert index.diagnostics == []
    names = sorted(f.path.name for f in index.files)
    assert names == [
        "corner.lib",
        "encrypted.lib",
        "opamp.lib",
        "param_in_subckt.lib",
        "tsmc018.lib",
    ]
    for f in index.files:
        if not f.encrypted:
            assert f.diagnostics == [], f.path.name


def test_encrypted_file_is_listed_not_parsed(index: LibraryIndex) -> None:
    assert [p.name for p in index.encrypted_files] == ["encrypted.lib"]
    [entry] = [f for f in index.files if f.encrypted]
    [d] = entry.diagnostics
    assert d.line == 3 and "encrypted" in d.message
    assert index.find_subckt("secret_amp") == []


def test_subcircuits_with_pins_and_lines(index: LibraryIndex) -> None:
    [opamp] = index.find_subckt("OPAMP5")
    assert opamp.pins == ("inp", "inn", "vcc", "vee", "out")
    assert (opamp.file.name, opamp.line, opamp.section) == ("opamp.lib", 3, None)
    [rdiv] = index.find_subckt("rdiv")
    assert rdiv.pins == ("in", "out", "gnd")


def test_models_with_type_and_section(index: LibraryIndex) -> None:
    corners = index.find_model("dcorner")
    assert [(m.type, m.line, m.section) for m in corners] == [
        ("D", 5, "tt"),
        ("D", 10, "ff"),
        ("D", 15, "ss"),
    ]
    [nmos] = index.find_model("cmosn")
    [pmos] = index.find_model("CMOSP")
    assert (nmos.name, nmos.type, nmos.section) == ("CMOSN", "NMOS", None)
    assert pmos.type == "PMOS"


def test_continuations_params_and_comments(tmp_path: Path) -> None:
    (tmp_path / "a.sub").write_text(
        "* comment\n"
        ".SUBCKT amp in+ in- ; inline\n"
        "+ out vcc vee PARAMS: gain=10\n"
        ".subckt b x y z = 1\n.ends\n"
        ".model q1 npn(bf=100) $ comment\n"
        ".model\n"
        ".lib open\n"
    )
    (tmp_path / "ignored.txt").write_text(".model n NMOS\n")
    index = LibraryIndex.scan([tmp_path])
    assert [(s.name, s.pins) for s in index.subcircuits] == [
        ("amp", ("in+", "in-", "out", "vcc", "vee")),
        ("b", ("x", "y")),
    ]
    assert [(m.name, m.type) for m in index.models] == [("q1", "npn")]
    [f] = index.files
    assert [d.line for d in f.diagnostics] == [7, 8]


def test_cp1252_file_and_missing_folder(tmp_path: Path) -> None:
    (tmp_path / "micro.lib").write_bytes(b"* 4.7\xb5F\r\n.subckt c1 a b\r\n.ends\r\n")
    index = LibraryIndex.scan([tmp_path, tmp_path / "nope", tmp_path])
    assert [s.name for s in index.subcircuits] == ["c1"]
    assert len(index.files) == 1  # the folder given twice is scanned once
    [d] = index.diagnostics
    assert d.severity == ERROR and "folder not found" in d.message


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("* Begin:\n* 0A1B\n* End\n", 1),
        ("x\n  *Begin:\n*DEADBEEF\n\n* 12\n*  end\n", 2),
        ("* Begin: the description\n* this is text\n* End\n", None),
        ("* Begin:\n* End\n", None),
        ("* Begin:\n* 0A1B\n", None),
        (".model d D\n", None),
    ],
)
def test_encrypted_block_line(text: str, line: int | None) -> None:
    assert encrypted_block_line(text) == line


def test_parser_reports_encrypted_include() -> None:
    result = parse("t\n.include encrypted.lib\nX1 a 0 secret_amp\n", LIB)
    [d] = [d for d in result.diagnostics if d.line == 2]
    assert d.severity == ERROR and "encrypted model file" in d.message
    assert result.circuit.subcircuits == []


def _write_library(path: Path, i: int) -> None:
    lines = [f"* generated library {i}"]
    for j in range(10):
        lines.append(f".model m{i}_{j} NMOS (LEVEL=49")
        lines += [f"+ P{k}={k}.5e-9 Q{k}=-{k}e3" for k in range(20)]
        lines.append("+ )")
    for j in range(5):
        lines += [f".subckt s{i}_{j} a b c", "+ d e PARAMS: w=1u", "R1 a b 1k", ".ends"]
    path.write_text("\n".join(lines) + "\n")


@pytest.mark.benchmark
@pytest.mark.skipif(bool(os.environ.get("CI")), reason="timing benchmark; skipped in CI")
def test_scan_500_files_under_two_seconds(tmp_path: Path) -> None:
    for i in range(500):
        folder = tmp_path / f"d{i % 10}"
        folder.mkdir(exist_ok=True)
        _write_library(folder / f"lib{i}.lib", i)
    start = time.perf_counter()
    index = LibraryIndex.scan([tmp_path])
    elapsed = time.perf_counter() - start
    assert len(index.files) == 500
    assert len(index.models) == 5000 and len(index.subcircuits) == 2500
    assert index.subcircuits[0].pins == ("a", "b", "c", "d", "e")
    assert elapsed < 2.0, f"scan took {elapsed:.2f} s"
