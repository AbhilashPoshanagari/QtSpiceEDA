from __future__ import annotations

import pickle

import pytest

from core.grid.model import (
    LOAD_PQ,
    WYE,
    Bus,
    GridModel,
    Line,
    Load,
    Regulator,
    Source,
    Switch,
    Terminal,
)


@pytest.mark.parametrize(
    ("text", "bus", "nodes"),
    [
        ("671.1.3", "671", (1, 3)),
        ("671.1.2.3", "671", (1, 2, 3)),
        ("675", "675", ()),
        ("SourceBus.0.0.0", "sourcebus", (0, 0, 0)),
        ("94_open.1", "94_open", (1,)),
        ("1.1", "1", (1,)),
        ("1", "1", ()),
        (" RG60.2 ", "rg60", (2,)),
    ],
)
def test_terminal_parse(text: str, bus: str, nodes: tuple[int, ...]) -> None:
    t = Terminal.parse(text)
    assert t.bus == bus
    assert t.nodes == nodes


def test_terminal_str_round_trips() -> None:
    for text in ("671.1.3", "675", "sourcebus.0.0.0"):
        assert str(Terminal.parse(text)) == text


def test_element_ids_use_the_opendss_class() -> None:
    t = Terminal.parse("650")
    assert Line("650632", t, t, 3, 609.6, "mtx601").id == "line.650632"
    assert Switch("671692", t, t, 3, True, 1e-7).id == "line.671692"
    assert Regulator("reg1", "reg1", 2, 122, 2, 20, 700, 3, 9).id == "regcontrol.reg1"
    assert Source("source", t, t, 3, 115, 1, 30, 0, 0, 0, 0).id == "vsource.source"
    assert Bus("671", 2.4018).id == "bus.671"
    assert Load("671", t, 3, WYE, 4.16, 1155, 660, LOAD_PQ).id == "load.671"


def test_grid_model_needs_a_base_frequency() -> None:
    with pytest.raises(TypeError):
        GridModel("x")  # type: ignore[call-arg]


def test_grid_model_counts_and_pickles() -> None:
    m = GridModel("demo", 50.0, [11.0])
    t = Terminal.parse("a")
    m.lines["l1"] = Line("l1", t, Terminal.parse("b"), 3, 100.0, "lc")
    assert m.counts()["lines"] == 1
    assert m.counts()["switches"] == 0
    again = pickle.loads(pickle.dumps(m))
    assert again == m
