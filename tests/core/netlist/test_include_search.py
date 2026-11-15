"""N0: ``.include <name>`` and ``.lib <name> <section>`` found in the library folders.

Order: absolute path as written; next to the netlist (``base_dir``, unchanged from
v0.1); then ``<folder>/<name>`` in each library folder; then a recursive search of each
folder by file name.
"""

from __future__ import annotations

from pathlib import Path

from core.netlist.model import ERROR, WARNING
from core.netlist.parser import parse

MODEL = ".model {name} NMOS (LEVEL=1 VTO=0.45 KP=200u)\n"


def netlist(include: str) -> str:
    return f"title\n{include}\nM1 d g 0 0 CMOSN W=1u L=0.18u\nV1 d 0 1\nV2 g 0 1\n.op\n.end\n"


def write(path: Path, model: str = "CMOSN") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MODEL.format(name=model), encoding="utf-8")
    return path


def models(text: str, base_dir=None, search_dirs=()) -> tuple[list[str], list]:
    result = parse(text, base_dir, search_dirs)
    return [m.name.casefold() for m in result.circuit.models], result.diagnostics


def errors(diagnostics: list) -> list[str]:
    return [d.message for d in diagnostics if d.severity == ERROR]


def test_bare_name_found_in_a_library_folder_for_a_pasted_netlist(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    write(lib / "tsmc018.lib")
    found, diags = models(netlist(".include tsmc018.lib"), None, [lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_lib_form_without_section_also_searches(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    write(lib / "tsmc018.lib")
    found, diags = models(netlist(".lib tsmc018.lib"), None, [lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_lib_section_found_in_a_library_folder(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "corners.lib").write_text(
        ".lib tt\n" + MODEL.format(name="CMOSN") + ".endl tt\n"
        ".lib ff\n" + MODEL.format(name="FAST") + ".endl ff\n",
        encoding="utf-8",
    )
    found, diags = models(netlist(".lib corners.lib tt"), None, [lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_found_in_a_subfolder(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    write(lib / "tsmc" / "018" / "tsmc018.lib")
    found, diags = models(netlist(".include tsmc018.lib"), None, [lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_relative_subpath_in_a_library_folder(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    write(lib / "tsmc" / "tsmc018.lib")
    found, diags = models(netlist(".include tsmc/tsmc018.lib"), None, [lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_absolute_path_still_works(tmp_path: Path) -> None:
    path = write(tmp_path / "elsewhere" / "tsmc018.lib")
    found, diags = models(netlist(f'.include "{path}"'), None, [tmp_path / "library"])
    assert found == ["cmosn"] and errors(diags) == []


def test_file_next_to_the_netlist_wins_over_the_library(tmp_path: Path) -> None:
    here = tmp_path / "project"
    write(here / "tsmc018.lib", "LOCAL")
    lib = tmp_path / "library"
    write(lib / "tsmc018.lib", "LIBRARY")
    text = netlist(".include tsmc018.lib").replace("CMOSN W", "LOCAL W")
    found, diags = models(text, here, [lib])
    assert found == ["local"] and errors(diags) == []


def test_falls_back_to_the_library_when_not_next_to_the_netlist(tmp_path: Path) -> None:
    here = tmp_path / "project"
    here.mkdir()
    lib = tmp_path / "library"
    write(lib / "tsmc018.lib")
    found, diags = models(netlist(".include tsmc018.lib"), here, [lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_first_folder_wins_and_a_warning_names_it(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    write(first / "tsmc018.lib")
    write(second / "tsmc018.lib", "OTHER")
    found, diags = models(netlist(".include tsmc018.lib"), None, [first, second])
    assert found == ["cmosn"] and errors(diags) == []
    [warning] = [d for d in diags if d.severity == WARNING]
    assert str(first / "tsmc018.lib") in warning.message
    assert str(second / "tsmc018.lib") in warning.message
    assert warning.line == 2


def test_direct_hit_in_a_later_folder_beats_a_nested_one_in_an_earlier(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    write(first / "deep" / "tsmc018.lib", "NESTED")
    write(second / "tsmc018.lib")
    found, _ = models(netlist(".include tsmc018.lib"), None, [first, second])
    assert found == ["cmosn"]


def test_not_found_lists_the_folders(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    lib.mkdir()
    _, diags = models(netlist(".include tsmc018.lib"), tmp_path, [lib])
    [message] = errors(diags)
    assert "tsmc018.lib" in message and str(lib) in message
    assert "next to the netlist" in message


def test_pasted_without_library_folders_keeps_the_old_message() -> None:
    _, diags = models(netlist(".include tsmc018.lib"))
    [message] = errors(diags)
    assert "relative .include" in message


def test_missing_library_folder_is_not_an_error_by_itself(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    write(lib / "tsmc018.lib")
    found, diags = models(netlist(".include tsmc018.lib"), None, [tmp_path / "nope", lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_nested_include_inside_a_library_file_is_found_too(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    write(lib / "models" / "nmos.mod")
    (lib / "tsmc018.lib").write_text(".include nmos.mod\n", encoding="utf-8")
    found, diags = models(netlist(".include tsmc018.lib"), None, [lib])
    assert found == ["cmosn"] and errors(diags) == []


def test_diagnostic_points_at_the_include_line(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    lib.mkdir()
    _, diags = models(netlist(".include missing.lib"), None, [lib])
    [error] = [d for d in diags if d.severity == ERROR and "missing.lib" in d.message]
    assert (error.line, error.col) == (2, 10)
