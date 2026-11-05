"""Sanity checks on the v0.2 fixture files, so later parser tests can trust them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

EXPECTED = [
    "README.md",
    "asy/res.asy",
    "asy/cap.asy",
    "asy/opamp5.asy",
    "asy/micro_cp1252.asy",
    "asy/cmosn.asy",
    "asy/cmosp.asy",
    "lib/opamp.lib",
    "lib/corner.lib",
    "lib/param_in_subckt.lib",
    "lib/encrypted.lib",
    "lib/tsmc018.lib",
    "schematics/AC_RC_filter.asc",
]


@pytest.mark.parametrize("name", EXPECTED)
def test_fixture_exists(name: str) -> None:
    assert (FIXTURES / name).is_file()


def _pins(path: Path) -> list[tuple[str, int]]:
    """(PinName, SpiceOrder) in drawing order."""
    pins: list[tuple[str, int]] = []
    name = ""
    for line in path.read_text(encoding="cp1252").splitlines():
        words = line.split()
        if words[:2] == ["PINATTR", "PinName"]:
            name = words[2]
        elif words[:2] == ["PINATTR", "SpiceOrder"]:
            pins.append((name, int(words[2])))
    return pins


def test_opamp5_drawing_order_differs_from_spice_order() -> None:
    pins = _pins(FIXTURES / "asy/opamp5.asy")
    orders = [order for _, order in pins]
    assert sorted(orders) == [1, 2, 3, 4, 5]
    assert orders != sorted(orders)


def test_micro_fixture_is_cp1252_with_crlf() -> None:
    raw = (FIXTURES / "asy/micro_cp1252.asy").read_bytes()
    assert b"\xb5" in raw
    assert b"\r\n" in raw
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert "4.7µ" in raw.decode("cp1252")


def test_corner_lib_has_three_sections() -> None:
    text = (FIXTURES / "lib/corner.lib").read_text()
    assert re.findall(r"^\.lib (\w+)", text, re.M) == ["tt", "ff", "ss"]
    assert re.findall(r"^\.endl (\w+)", text, re.M) == ["tt", "ff", "ss"]


def test_param_inside_subckt() -> None:
    lines = (FIXTURES / "lib/param_in_subckt.lib").read_text().splitlines()
    start = next(i for i, s in enumerate(lines) if s.startswith(".subckt"))
    end = next(i for i, s in enumerate(lines) if s.startswith(".ends"))
    assert any(s.startswith(".param") for s in lines[start:end])


def test_encrypted_lib_has_hex_block() -> None:
    lines = (FIXTURES / "lib/encrypted.lib").read_text().splitlines()
    begin = lines.index("* Begin:")
    end = lines.index("* End")
    body = [s.removeprefix("* ") for s in lines[begin + 1 : end]]
    assert body and all(re.fullmatch(r"[0-9A-F]+", s) for s in body)


def test_real_mos_symbols_use_model_file() -> None:
    for name, prefix in (("cmosn", "MN"), ("cmosp", "MP")):
        text = (FIXTURES / f"asy/{name}.asy").read_text(encoding="cp1252")
        assert f"SYMATTR Prefix {prefix}" in text
        assert "SYMATTR ModelFile tsmc018.lib" in text
        assert [o for _, o in _pins(FIXTURES / f"asy/{name}.asy")] == [1, 2, 3, 4]
