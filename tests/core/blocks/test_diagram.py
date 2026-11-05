from __future__ import annotations

import json

import pytest

from core.blocks.diagram import Diagram, DiagramError, Wire
from core.blocks.library import builtin_specs
from core.blocks.spec import BlockSpec

SPECS = {s.name: s for s in builtin_specs()}
# A block with an input and an output of the same name, to test the self-wire rule.
SPECS["Echo"] = BlockSpec(name="Echo", inputs=["y"], outputs=["y"])


def new_diagram() -> Diagram:
    return Diagram(SPECS.get)


def test_ids_are_numbered_per_type() -> None:
    d = new_diagram()
    assert d.add_block("Gain").id == "Gain1"
    assert d.add_block("Gain").id == "Gain2"
    assert d.add_block("Scope").id == "Scope1"
    assert [b.id for b in d.blocks] == ["Gain1", "Gain2", "Scope1"]


def test_explicit_id_must_be_unique_identifier() -> None:
    d = new_diagram()
    d.add_block("Gain", block_id="g")
    with pytest.raises(DiagramError, match="already exists"):
        d.add_block("Gain", block_id="g")
    with pytest.raises(DiagramError, match="identifier"):
        d.add_block("Gain", block_id="a.b")


def test_unknown_spec() -> None:
    with pytest.raises(DiagramError, match="unknown block type 'Nope'"):
        new_diagram().add_block("Nope")


def test_settings_overrides_and_defaults() -> None:
    d = new_diagram()
    d.add_block("Sine", settings={"frequency": 5.0})
    eff = d.effective_settings("Sine1")
    assert eff["frequency"] == 5.0 and eff["amplitude"] == 1.0
    with pytest.raises(DiagramError, match="no setting 'gain'"):
        d.set_settings("Sine1", {"gain": 2})
    with pytest.raises(DiagramError, match="plain constant"):
        d.add_block("Gain", settings={"k": object()})


def test_wire_output_to_input() -> None:
    d = new_diagram()
    d.add_block("Sine")
    d.add_block("Gain")
    w = d.add_wire("Sine1", "y", "Gain1", "u")
    assert w == Wire("Sine1", "y", "Gain1", "u")
    assert d.wire_into("Gain1", "u") == w


def test_wire_must_start_at_output_and_end_at_input() -> None:
    d = new_diagram()
    d.add_block("Gain")
    d.add_block("Gain")
    with pytest.raises(DiagramError, match="is an input"):
        d.add_wire("Gain1", "u", "Gain2", "u")
    with pytest.raises(DiagramError, match="is an output"):
        d.add_wire("Gain1", "y", "Gain2", "y")
    with pytest.raises(DiagramError, match="no output 'z'"):
        d.add_wire("Gain1", "z", "Gain2", "u")
    with pytest.raises(DiagramError, match="no block with id 'Gain9'"):
        d.add_wire("Gain1", "y", "Gain9", "u")


def test_one_wire_per_input() -> None:
    d = new_diagram()
    d.add_block("Constant")
    d.add_block("Constant")
    d.add_block("Gain")
    d.add_wire("Constant1", "y", "Gain1", "u")
    with pytest.raises(DiagramError, match="already wired from Constant1.y"):
        d.add_wire("Constant2", "y", "Gain1", "u")


def test_no_self_wire_to_same_port() -> None:
    d = new_diagram()
    d.add_block("Echo")
    with pytest.raises(DiagramError, match="to itself"):
        d.add_wire("Echo1", "y", "Echo1", "y")


def test_own_output_to_other_own_input_is_allowed() -> None:
    # The engine decides whether such a loop can run (see test_engine).
    d = new_diagram()
    d.add_block("Sum")
    d.add_wire("Sum1", "y", "Sum1", "a")
    assert len(d.wires) == 1


def test_remove_block_removes_its_wires() -> None:
    d = new_diagram()
    for name in ("Sine", "Gain", "Scope"):
        d.add_block(name)
    d.add_wire("Sine1", "y", "Gain1", "u")
    d.add_wire("Gain1", "y", "Scope1", "u")
    d.remove_block("Gain1")
    assert d.wires == []
    assert [b.id for b in d.blocks] == ["Sine1", "Scope1"]


def test_remove_wire() -> None:
    d = new_diagram()
    d.add_block("Sine")
    d.add_block("Scope")
    w = d.add_wire("Sine1", "y", "Scope1", "u")
    d.remove_wire(w)
    assert d.wires == []
    with pytest.raises(DiagramError):
        d.remove_wire(w)


def test_round_trip_through_json() -> None:
    d = new_diagram()
    d.add_block("Sine", 10, 20, {"amplitude": 3.0})
    d.add_block("Gain", 100, 20, {"k": 2.0})
    d.add_block("Scope", 200, 20, {"label": "out"})
    d.add_wire("Sine1", "y", "Gain1", "u")
    d.add_wire("Gain1", "y", "Scope1", "u")
    data = json.loads(json.dumps(d.to_dict()))
    assert data["wires"] == [
        {"src": "Sine1.y", "dst": "Gain1.u"},
        {"src": "Gain1.y", "dst": "Scope1.u"},
    ]
    again = Diagram.from_dict(data, SPECS.get)
    assert again.to_dict() == d.to_dict()


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "JSON object"),
        ({"blocks": {}}, "must be lists"),
        ({"blocks": [{"id": "a"}]}, "string 'id' and 'spec'"),
        ({"blocks": [{"id": "a", "spec": "Gain", "x": "left"}]}, "must be numbers"),
        ({"blocks": [{"id": "a", "spec": "Gain", "settings": []}]}, "JSON object"),
        ({"wires": [{"src": "a", "dst": "b.u"}]}, "Block1.port"),
        (
            {
                "blocks": [{"id": "c", "spec": "Constant"}, {"id": "g", "spec": "Gain"}],
                "wires": [{"src": "c.y", "dst": "g.u"}, {"src": "c.y", "dst": "g.u"}],
            },
            "already wired",
        ),
    ],
)
def test_from_dict_rejects_bad_data(data: object, message: str) -> None:
    with pytest.raises(DiagramError, match=message):
        Diagram.from_dict(data, SPECS.get)
