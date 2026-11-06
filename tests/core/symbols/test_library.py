"""L5: SymbolLibrary search order, lookup and cache."""

from __future__ import annotations

from pathlib import Path

from core.netlist.model import ERROR, WARNING
from core.symbols.library import BUILTIN, LTSPICE, USER, SymbolLibrary

ASY = Path(__file__).resolve().parents[2] / "fixtures" / "asy"


def _sym(folder: Path, rel: str, value: str) -> Path:
    path = folder / f"{rel}.asy"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"Version 4\r\nSymbolType CELL\r\nSYMATTR Value {value}\r\n".encode("cp1252"))
    return path


def test_search_order_builtin_user_ltspice(tmp_path: Path) -> None:
    builtin, u1, u2, lt = (tmp_path / n for n in ("b", "u1", "u2", "lt"))
    _sym(builtin, "res", "builtin")
    _sym(u1, "res", "u1")
    _sym(u1, "cap", "u1")
    _sym(u2, "cap", "u2")
    _sym(u2, "ind", "u2")
    _sym(lt / "sym", "ind", "lt")
    _sym(lt / "sym", "Opamps/opamp2", "lt")
    lib = SymbolLibrary([u1, u2], lt, builtin_dir=builtin)
    assert [s for _, s in lib.search_paths()] == [BUILTIN, USER, USER, LTSPICE]
    assert lib.search_paths()[-1][0] == lt / "sym"
    assert lib.names() == ["cap", "ind", "Opamps/opamp2", "res"]
    assert lib.lookup("res")[0].attr("Value") == "builtin"
    assert lib.lookup("CAP")[0].attr("Value") == "u1"
    assert lib.lookup("ind")[0].attr("Value") == "u2"
    entry = lib.find(r"Opamps\opamp2")
    assert entry is not None and entry.source == LTSPICE and entry.read_only
    assert lib.lookup("opamps/opamp2.asy")[0].attr("Value") == "lt"
    assert lib.lookup("opamp2")[0] is not None  # bare name finds it in a subfolder
    assert lib.diagnostics == []


def test_ltspice_folder_without_sym_subfolder(tmp_path: Path) -> None:
    _sym(tmp_path / "lt", "diode", "lt")
    lib = SymbolLibrary([], tmp_path / "lt", builtin_dir=None)
    assert lib.names() == ["diode"]


def test_fixture_folder(tmp_path: Path) -> None:
    lib = SymbolLibrary([ASY], builtin_dir=None)
    symbol, diagnostics = lib.lookup("opamp5")
    assert diagnostics == []
    assert [p.name for p in symbol.pins] == ["In+", "In-", "V+", "V-", "OUT"]
    assert symbol.path == ASY / "opamp5.asy"


def test_not_found_and_missing_folder(tmp_path: Path) -> None:
    lib = SymbolLibrary([tmp_path / "missing"], builtin_dir=None)
    symbol, diagnostics = lib.lookup("res")
    assert symbol is None and [d.severity for d in diagnostics] == [ERROR]
    assert [d.severity for d in lib.diagnostics] == [WARNING]
    assert lib.find("sub/res") is None


def test_parsed_symbols_are_cached(tmp_path: Path) -> None:
    _sym(tmp_path / "u", "res", "1")
    lib = SymbolLibrary([tmp_path / "u"], builtin_dir=None)
    first, _ = lib.lookup("res")
    assert lib.lookup("res")[0] is first
    _sym(tmp_path / "u", "res", "2")
    _sym(tmp_path / "u", "new", "x")
    assert lib.lookup("res")[0].attr("Value") == "1"  # still cached
    assert "new" not in lib.names()
    lib.refresh()
    assert lib.lookup("res")[0].attr("Value") == "2"
    assert "new" in lib.names()


def test_changing_paths_clears_cache(tmp_path: Path) -> None:
    _sym(tmp_path / "a", "res", "a")
    _sym(tmp_path / "b", "res", "b")
    _sym(tmp_path / "b", "cap", "b")
    lib = SymbolLibrary([tmp_path / "a"], builtin_dir=None)
    assert lib.names() == ["res"]
    assert lib.set_paths([tmp_path / "a"]) is False
    assert lib.set_paths([tmp_path / "b", tmp_path / "a"]) is True
    assert lib.names() == ["cap", "res"]
    assert lib.lookup("res")[0].attr("Value") == "b"
    assert lib.set_paths([tmp_path / "b"], tmp_path / "a") is True
    assert lib.ltspice_dir == tmp_path / "a"
    assert lib.set_paths(["", " "], "") is True
    assert lib.user_dirs == () and lib.ltspice_dir is None and lib.names() == []


def test_project_symbols_come_after_builtin_and_before_user_folders(tmp_path: Path) -> None:
    from core.symbols.library import PROJECT

    proj, user = tmp_path / "proj", tmp_path / "user"
    rclp = _sym(proj, "rclp", "project")
    res = _sym(proj, "res", "shadow")
    _sym(user, "rclp", "user")
    gone = proj / "gone.asy"
    lib = SymbolLibrary([user], project_files=[rclp, res, gone])
    entry = lib.find("rclp")
    assert entry is not None and entry.source == PROJECT and entry.path == rclp
    assert lib.lookup("rclp")[0].attr("Value") == "project"
    assert lib.find("res").source == BUILTIN  # a built-in symbol always wins
    assert any("project symbol not found" in d.message for d in lib.diagnostics)

    (proj / "two.asy").write_bytes(rclp.read_bytes())
    assert lib.set_project_files([rclp, proj / "two.asy"]) is True
    assert "two" in lib.names()
    assert lib.set_project_files([rclp, proj / "two.asy"]) is False
    assert lib.set_project_files([]) is True and lib.find("rclp").source == USER
