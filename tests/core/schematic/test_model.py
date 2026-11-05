"""S1: schematic model, to_dict/from_dict and pin geometry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.netlist.model import ERROR, WARNING
from core.schematic.model import (
    GroundFlag,
    Instance,
    NetLabel,
    PortFlag,
    Schematic,
    TextItem,
    Wire,
    pin_points,
    transform,
)
from core.symbols.asy import AsyParser, Pin, Symbol

ASY = Path(__file__).resolve().parents[2] / "fixtures" / "asy"


def _sample() -> Schematic:
    return Schematic(
        title="sample",
        instances=[
            Instance("res", "R1", 128, 96, 90, True, {"Value": "10k"}),
            Instance("voltage", "V1", 64, 144, attrs={"Value": "DC 1", "Value2": "AC 1"}),
        ],
        wires=[Wire(0, 0, 64, 0), Wire(64, 0, 64, 32)],
        labels=[NetLabel(64, 0, "IN")],
        grounds=[GroundFlag(64, 240)],
        ports=[PortFlag(0, 0, "a", "in")],
        texts=[TextItem(0, 300, ".ac dec 10 1 1meg"), TextItem(0, 320, "note", False)],
    )


def test_round_trip_through_json() -> None:
    original = _sample()
    text = json.dumps(original.to_dict())
    back, diagnostics = Schematic.from_dict(json.loads(text))
    assert diagnostics == []
    assert back == original


def test_empty_dict_is_an_empty_schematic() -> None:
    assert Schematic.from_dict({}) == (Schematic(), [])


def test_defaults_for_optional_fields() -> None:
    data = {"instances": [{"symbol": "res", "name": "R1", "x": 0, "y": 16}]}
    schematic, diagnostics = Schematic.from_dict(data)
    assert diagnostics == []
    assert schematic.instances == [Instance("res", "R1", 0, 16)]
    assert schematic.instances[0].orientation == "R0"


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "text",
        {"instances": 3},
        {"instances": [{"symbol": "res", "name": "R1", "x": 0, "y": 0, "rotation": 45}]},
        {"instances": [{"symbol": "res", "name": "R1", "x": 0.5, "y": 0}]},
        {"instances": [{"symbol": "", "name": "R1", "x": 0, "y": 0}]},
        {"instances": [{"symbol": "res", "x": 0, "y": 0}]},
        {"instances": [{"symbol": "res", "name": "R1", "x": 0, "y": 0, "attrs": {"V": 1}}]},
        {"wires": [[0, 0, 16]]},
        {"wires": [[0, 0, True, 16]]},
        {"labels": [{"x": 0, "y": 0, "name": "two words"}]},
        {"ports": [{"x": 0, "y": 0, "name": "a", "direction": "up"}]},
        {"texts": [{"x": 0, "y": 0}]},
        {"title": 7},
    ],
)
def test_bad_input_gives_errors_and_never_raises(data: object) -> None:
    _, diagnostics = Schematic.from_dict(data)
    assert any(d.severity == ERROR for d in diagnostics)


def test_bad_items_are_skipped_and_good_ones_kept() -> None:
    data = {"wires": [[0, 0, 16, 0], "bad", [16, 0, 16, 16]]}
    schematic, diagnostics = Schematic.from_dict(data)
    assert schematic.wires == [Wire(0, 0, 16, 0), Wire(16, 0, 16, 16)]
    assert [d.message for d in diagnostics] == [
        "schematic: wires[1] must be [x1, y1, x2, y2] (whole numbers)"
    ]


def test_warnings_for_unknown_fields_zero_wires_and_duplicate_names() -> None:
    data = {
        "extra": 1,
        "wires": [[0, 0, 0, 0]],
        "instances": [
            {"symbol": "res", "name": "R1", "x": 0, "y": 0, "colour": "red"},
            {"symbol": "res", "name": "r1", "x": 64, "y": 0},
        ],
    }
    schematic, diagnostics = Schematic.from_dict(data)
    assert {d.severity for d in diagnostics} == {WARNING}
    assert len(diagnostics) == 4
    assert schematic.wires == [] and len(schematic.instances) == 2


def test_instance_lookup_is_case_insensitive() -> None:
    schematic = _sample()
    assert schematic.instance("r1") is schematic.instances[0]
    assert schematic.instance("X9") is None


# ---------------------------------------------------------------- orientation

# The LTspice orientations (KiCad importer docs): rotation clockwise on screen with
# y down, mirror (in X) after the rotation.
ORIENTATIONS = [
    (0, False, (16, 96)),
    (90, False, (-96, 16)),
    (180, False, (-16, -96)),
    (270, False, (96, -16)),
    (0, True, (-16, 96)),
    (90, True, (96, 16)),
    (180, True, (16, -96)),
    (270, True, (-96, -16)),
]


@pytest.mark.parametrize(("rotation", "mirror", "expected"), ORIENTATIONS)
def test_transform_matches_ltspice_orientations(
    rotation: int, mirror: bool, expected: tuple[int, int]
) -> None:
    assert transform(16, 96, rotation, mirror) == expected


def test_m90_resistor_lands_on_the_wire_ends_of_the_ltspice_rc_fixture() -> None:
    # AC_RC_filter.asc: "SYMBOL res 128 96 M90", wires end at (144,112) and (224,112).
    # LTspice's res.asy has its pins at (16,16) and (16,96).
    res = Symbol("res", pins=[Pin(16, 16, index=0), Pin(16, 96, index=1)])
    inst = Instance("res", "R2", 128, 96, 90, True)
    assert pin_points(inst, res) == [(144, 112), (224, 112)]


def test_pin_points_follow_spice_order_for_a_rotated_mirrored_opamp() -> None:
    symbol, _ = AsyParser.parse(ASY / "opamp5.asy")
    inst = Instance("opamp5", "U1", 320, 160, 180, True)
    points = pin_points(inst, symbol)
    # SpiceOrder In+, In-, V+, V-, OUT; R180 then mirror maps (x, y) to (x, -y).
    assert points == [(256, 80), (256, 112), (320, 128), (320, 64), (384, 96)]
