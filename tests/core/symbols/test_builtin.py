"""L6: QtSpice's own built-in symbols."""

from __future__ import annotations

import pytest

from core.symbols.asy import AsyParser
from core.symbols.library import BUILTIN, BUILTIN_DIR, SymbolLibrary

# name -> (Prefix, pin names in SpiceOrder)
EXPECTED = {
    "res": ("R", ["A", "B"]),
    "cap": ("C", ["A", "B"]),
    "ind": ("L", ["A", "B"]),
    "diode": ("D", ["A", "K"]),
    "voltage": ("V", ["+", "-"]),
    "current": ("I", ["+", "-"]),
    "npn": ("Q", ["C", "B", "E"]),
    "pnp": ("Q", ["C", "B", "E"]),
    "nmos": ("M", ["D", "G", "S", "B"]),
    "pmos": ("M", ["D", "G", "S", "B"]),
    "opamp": ("X", ["In+", "In-", "V+", "V-", "OUT"]),
    "ground": ("", ["GND"]),
    "label": ("", ["NET"]),
}


def test_exactly_the_planned_symbols_ship() -> None:
    lib = SymbolLibrary(builtin_dir=BUILTIN_DIR)
    assert sorted(lib.names()) == sorted(EXPECTED)
    assert {e.source for e in lib.entries()} == {BUILTIN}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_builtin_symbol(name: str) -> None:
    symbol, diagnostics = AsyParser.parse(BUILTIN_DIR / f"{name}.asy")
    assert diagnostics == []
    prefix, pins = EXPECTED[name]
    assert symbol.prefix == prefix
    assert [p.name for p in symbol.pins] == pins
    assert [p.spice_order for p in symbol.pins] == list(range(1, len(pins) + 1))
    assert all(p.x % 16 == 0 and p.y % 16 == 0 for p in symbol.pins), "pins on the 16 grid"
    assert symbol.lines, "has a drawing"
    assert "QtSpice built-in" in symbol.attr("Description")
    if prefix:
        assert symbol.attr("Value")
