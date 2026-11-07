"""U2: library groups, physical tiles, unit captions and SPICE numbers in settings."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject

from bridge.diagram_controller import (
    DiagramController,
    parse_physical_setting,
    physical_group,
    setting_caption,
)
from tests.ui.mouse import click, drag, find_item, find_items, item_center
from tests.ui.qml_harness import qml_scene

PHYSICAL = [
    "Physical/Electrical",
    "Physical/Rotational",
    "Physical/Translational",
    "Physical/Thermal",
    "Physical/Sensors and sources",
    "Physical/Composite",
]


@pytest.fixture
def ctl(qapp: object) -> DiagramController:
    return DiagramController()


def group_of(ctl: DiagramController, name: str) -> str:
    return next(b["group"] for b in ctl.property("library") if b["name"] == name)


def test_library_groups_in_order(ctl: DiagramController) -> None:
    assert ctl.property("libraryGroups") == ["User", "Signal", *PHYSICAL]


def test_each_part_is_in_its_group(ctl: DiagramController) -> None:
    assert group_of(ctl, "Gain") == "Signal"
    assert group_of(ctl, "Resistor") == "Physical/Electrical"
    assert group_of(ctl, "ElectricalReference") == "Physical/Electrical"
    assert group_of(ctl, "Inertia") == "Physical/Rotational"
    assert group_of(ctl, "Mass") == "Physical/Translational"
    assert group_of(ctl, "ThermalMass") == "Physical/Thermal"
    for name in ("ControlledTorqueSource", "AngularVelocitySensor", "SolverConfiguration"):
        assert group_of(ctl, name) == "Physical/Sensors and sources"


def test_user_blocks_are_in_the_user_group(ctl: DiagramController) -> None:
    assert ctl.apply("@block\ndef twice(u): return 2 * u\n")
    assert group_of(ctl, "twice") == "User"


def test_physical_group_of_an_unknown_module() -> None:
    assert physical_group("my_parts.motors") == "Physical/Other"
    assert physical_group("core.physical.library.thermal.components") == "Physical/Thermal"


def test_setting_caption() -> None:
    assert setting_caption("J", "kg*m^2") == "J [kg*m^2]"
    assert setting_caption("k", "") == "k"


@pytest.mark.parametrize(
    ("text", "default", "value"),
    [
        ("10m", 0.01, 0.01),
        ("2.2u", 1.0, 2.2e-6),
        ("1meg", 1.0, 1e6),
        ("4.7k", 1.0, 4700.0),
        ("0.5", 1.0, 0.5),
        ("1e-3", 1.0, 1e-3),
        ("3", 1, 3),
        ("2k", 1, 2000),
        ("true", False, True),
        ("label", "x", "label"),
    ],
)
def test_parse_physical_setting(text: str, default: object, value: object) -> None:
    got = parse_physical_setting(text, default)
    assert got == pytest.approx(value) and type(got) is type(value)


@pytest.mark.parametrize(("text", "default"), [("abc", 1.0), ("", 1.0), ("1.5", 1), ("x", True)])
def test_parse_physical_setting_rejects(text: str, default: object) -> None:
    with pytest.raises(ValueError):
        parse_physical_setting(text, default)


def test_selected_physical_block_shows_units(ctl: DiagramController) -> None:
    bid = ctl.addBlock("Inertia", 0, 0)
    ctl.select(bid, False)
    sel = ctl.property("selectedBlock")
    assert sel["physical"] is True
    j = next(s for s in sel["settings"] if s["name"] == "J")
    assert j["caption"] == "J [kg*m^2]" and j["unit"] == "kg*m^2" and j["label"] == "inertia"
    gain = ctl.addBlock("Gain", 200, 0)
    ctl.select(gain, False)
    [k] = ctl.property("selectedBlock")["settings"]
    assert k == {"name": "k", "value": 1.0, "text": "1.0"}  # unchanged v0.1 row


def test_physical_setting_accepts_spice_suffixes(ctl: DiagramController) -> None:
    bid = ctl.addBlock("Inertia", 0, 0)
    assert ctl.setSetting(bid, "J", "10m") is True
    assert ctl.diagram.block(bid).settings["J"] == pytest.approx(0.01)
    assert ctl.setSetting(bid, "J", "lots") is False
    assert "10m" in ctl.property("hint")
    ctl.undo()
    assert "J" not in ctl.diagram.block(bid).settings


def test_signal_settings_keep_v01_parsing(ctl: DiagramController) -> None:
    bid = ctl.addBlock("Gain", 0, 0)
    assert ctl.setSetting(bid, "k", "10m") is False  # v0.1 blocks take plain numbers


# ---- in QML

SCENE = """
RowLayout {
    anchors.fill: parent
    spacing: 0
    BlockLibrary { objectName: "library"; Layout.preferredWidth: 320; Layout.fillHeight: true }
    BlockCanvas { objectName: "canvas"; Layout.fillWidth: true; Layout.fillHeight: true }
    PropertiesPanel { Layout.preferredWidth: 240; Layout.fillHeight: true }
}
"""


class Scene:
    def __init__(self, engine: QObject, window: QObject) -> None:
        self.window = window
        self.ctl = engine.singletonInstance("QtSpice", "DiagramController")
        self.library = find_item(window, "library")
        self.canvas = find_item(window, "canvas")

    def tiles(self) -> dict:
        return {
            t.property("blockName"): t
            for t in find_items(self.library, "blockTile")
            if t.isVisible()
        }


@pytest.fixture
def scene(qapp: object, tmp_path: Path, qtbot) -> Iterator[Scene]:
    with qml_scene(tmp_path, SCENE, width=1300, height=800) as (engine, window):
        window.requestActivate()
        qtbot.waitUntil(lambda: window.isActive(), timeout=2000)
        qtbot.wait(50)
        yield Scene(engine, window)


def test_physical_groups_start_folded_and_open(scene: Scene, qtbot) -> None:
    for group in PHYSICAL:
        assert find_item(scene.library, "group_" + group).isVisible()
    assert "Inertia" not in scene.tiles()
    assert "Gain" in scene.tiles()
    click(scene.window, item_center(find_item(scene.library, "group_Physical/Rotational")))
    qtbot.waitUntil(lambda: "Inertia" in scene.tiles(), timeout=2000)
    assert "Resistor" not in scene.tiles()
    tile = scene.tiles()["RotationalDamper"]
    ports = find_items(tile, "conservingPort")
    assert [p.property("portName") for p in ports] == ["A", "B"]
    assert {p.property("domain") for p in ports} == {"rotational"}


def test_drop_physical_block_and_edit_with_units(scene: Scene, qtbot) -> None:
    click(scene.window, item_center(find_item(scene.library, "group_Physical/Rotational")))
    qtbot.waitUntil(lambda: "Inertia" in scene.tiles(), timeout=2000)
    from tests.ui.mouse import canvas_point

    drag(
        scene.window,
        item_center(scene.tiles()["Inertia"]),
        canvas_point(scene.canvas, 200, 160),
        steps=20,
    )
    qtbot.waitUntil(lambda: scene.ctl.blocks.rowCount() == 1, timeout=2000)
    bid = scene.ctl.blocks.rows()[0]["blockId"]
    scene.ctl.select(bid, False)
    qtbot.waitUntil(
        lambda: find_item(scene.window, "caption_J").property("text") == "J [kg*m^2]",
        timeout=2000,
    )
    field = find_item(scene.window, "setting_J")
    field.setProperty("text", "20m")
    field.editingFinished.emit()
    qtbot.waitUntil(
        lambda: scene.ctl.diagram.block(bid).settings.get("J") == pytest.approx(0.02),
        timeout=2000,
    )
