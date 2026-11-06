"""S5: drawing paths, boxes, text anchors and hit-testing for the schematic editor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.schematic.geometry import (
    GROUND,
    INSTANCE,
    LABEL,
    WIRE,
    Hit,
    bounds,
    hit_test,
    instance_box,
    instance_texts,
    items_in_rect,
    orientation_matrix,
    pin_near,
    snap,
    symbol_svg,
)
from core.schematic.model import (
    ROTATIONS,
    GroundFlag,
    Instance,
    NetLabel,
    Schematic,
    Wire,
    transform,
)
from core.symbols.asy import AsyParser, Symbol
from core.symbols.library import BUILTIN_DIR, SymbolLibrary

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
BUILTIN = SymbolLibrary(builtin_dir=BUILTIN_DIR)


def lookup(name: str) -> Symbol | None:
    return BUILTIN.lookup(name)[0]


def rc() -> Schematic:
    data = json.loads((FIXTURES / "schematics" / "rc" / "rc.json").read_text("utf-8"))
    return Schematic.from_dict(data)[0]


@pytest.mark.parametrize("name", sorted(p.stem for p in BUILTIN_DIR.glob("*.asy")))
def test_every_builtin_symbol_has_a_path(name: str) -> None:
    symbol = lookup(name)
    assert symbol is not None
    path = symbol_svg(symbol)
    assert path.startswith("M ")
    assert "nan" not in path


def test_fixture_symbols_with_arcs_and_circles_draw() -> None:
    symbol, _ = AsyParser.parse(FIXTURES / "asy" / "opamp5.asy")
    path = symbol_svg(symbol)
    assert path.count(" A ") == 3  # one circle (two arcs) and one arc
    assert path.count("Z") == 1  # the rectangle


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("mirror", [False, True])
def test_orientation_matrix_is_the_core_transform(rotation: int, mirror: bool) -> None:
    a, b, c, d = orientation_matrix(rotation, mirror)
    for x, y in ((16, 96), (-64, 80), (3, -7)):
        assert (a * x + b * y, c * x + d * y) == transform(x, y, rotation, mirror)


def test_instance_box_follows_rotation() -> None:
    res = lookup("res")
    upright = instance_box(Instance("res", "R1", 0, 0), res)
    turned = instance_box(Instance("res", "R1", 0, 0, 90), res)
    assert upright[3] - upright[1] == turned[2] - turned[0] == 96


def test_texts_follow_windows_and_stay_upright() -> None:
    res = lookup("res")
    texts = instance_texts(Instance("res", "R1", 0, 0, attrs={"Value": "10k"}), res)
    assert [(t["text"], t["x"], t["y"], t["align"]) for t in texts] == [
        ("R1", 36, 32, "left"),
        ("10k", 36, 64, "left"),
    ]
    mirrored = instance_texts(Instance("res", "R1", 0, 0, 0, True, {"Value": "10k"}), res)
    assert [(t["x"], t["align"]) for t in mirrored] == [(-36, "right"), (-36, "right")]


def test_value2_is_shown_under_value_and_net_flag_symbols_have_no_text() -> None:
    v = Instance("voltage", "V1", 0, 0, attrs={"Value": "DC 1", "Value2": "AC 1"})
    texts = instance_texts(v, lookup("voltage"))
    assert [t["text"] for t in texts] == ["V1", "DC 1", "AC 1"]
    assert instance_texts(Instance("ground", "G", 0, 0), lookup("ground")) == []
    missing = instance_texts(Instance("nosuch", "X1", 0, 0), None)
    assert missing[0]["text"] == "X1: nosuch?"


def test_hit_test_prefers_flags_then_wires_then_parts() -> None:
    s = rc()
    assert hit_test(s, lookup, 70, 112, 4) == Hit(LABEL, 0)  # the IN label
    assert hit_test(s, lookup, 100, 113, 4) == Hit(WIRE, 0)
    assert hit_test(s, lookup, 192, 112, 4) == Hit(INSTANCE, 2)  # R2 body
    assert hit_test(s, lookup, 64, 300, 4) == Hit(GROUND, 0)
    assert hit_test(s, lookup, 600, 600, 4) is None


def test_items_in_rect_takes_only_whole_items() -> None:
    s = rc()
    found = items_in_rect(s, lookup, (250, 100, 340, 300))
    assert Hit(INSTANCE, 1) in found  # C1
    assert Hit(WIRE, 1) not in found  # 304,112 -> 240,112 sticks out
    assert {h.kind for h in found} >= {INSTANCE, WIRE, LABEL, GROUND}
    assert items_in_rect(s, lookup, (340, 300, 250, 100)) == found  # any corners


def test_snap_and_pin_near() -> None:
    assert (snap(7), snap(8), snap(-9), snap(23.9)) == (0, 16, -16, 16)
    s = Schematic(instances=[Instance("res", "R1", 0, 0)])
    assert pin_near(s, lookup, 18, 3, 5) == (16, 0)
    assert pin_near(s, lookup, 30, 30, 5) is None


def test_bounds() -> None:
    assert bounds(Schematic(), lookup) is None
    s = Schematic(
        wires=[Wire(0, 0, 64, 0)], labels=[NetLabel(64, 0, "a")], grounds=[GroundFlag(0, 0)]
    )
    x1, y1, x2, y2 = bounds(s, lookup)  # type: ignore[misc]
    assert x1 == -16 and y1 == -10 and x2 > 64 and y2 == 32


def test_pin_names_show_beside_aligned_pins() -> None:
    symbol, _ = AsyParser.parse_text(
        "Version 4\nSymbolType BLOCK\nRECTANGLE Normal 0 0 64 64\nSYMATTR Prefix X\n"
        "PIN -16 32 LEFT 20\nPINATTR PinName IN\nPINATTR SpiceOrder 1\n"
        "PIN 80 32 RIGHT 20\nPINATTR PinName OUT\nPINATTR SpiceOrder 2\n"
        "PIN 32 -16 TOP 20\nPINATTR PinName VCC\nPINATTR SpiceOrder 3\n"
        "PIN 32 80 NONE 0\nPINATTR PinName HIDDEN\nPINATTR SpiceOrder 4\n",
        "blk",
    )
    texts = instance_texts(Instance("blk", "X1", 0, 0), symbol)
    pins = [(t["text"], t["x"], t["y"], t["align"]) for t in texts if t["text"] != "X1"]
    assert pins == [("IN", 4, 32, "left"), ("OUT", 60, 32, "right"), ("VCC", 32, 4, "top")]
    turned = instance_texts(Instance("blk", "X1", 0, 0, 90), symbol)
    assert [t["align"] for t in turned if t["text"] == "IN"] == ["top"]
