"""S3 spike checks: `pytest -q scratch/phase5/spike3_layout` (not part of `pytest -q`).

The measurements are run_all.py; these only pin down what the README claims.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

from feeders import FEEDERS, build_graph, file_coordinates, load_model, measure  # noqa: E402
from layout import (  # noqa: E402
    VARIANTS,
    Edge,
    Graph,
    place_missing,
    scale_coordinates,
    tree_layout,
)


def _small(open_switch: bool) -> Graph:
    #  src - a - b - c         a -[sw]- d - e    (sw open or closed)
    #            \ tx - lv     f (no branch at all)
    edges = [
        Edge("line.1", "line", "src", "a"),
        Edge("line.2", "line", "a", "b"),
        Edge("line.3", "line", "b", "c"),
        Edge("transformer.t", "transformer", "b", "lv"),
        Edge("line.sw", "switch", "a", "d", closed=not open_switch),
        Edge("line.4", "line", "d", "e"),
    ]
    buses = ["a", "b", "c", "d", "e", "f", "lv", "src"]
    return Graph(buses, edges, "src", {"src": 1, "c": 2, "e": 1})


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("open_switch", [False, True])
def test_small_graph(variant: str, open_switch: bool) -> None:
    g = _small(open_switch)
    pos = tree_layout(g, variant)
    assert set(pos) == set(g.buses)
    assert len(set(pos.values())) == len(pos)  # no two buses on one point
    assert all(float(v).is_integer() for p in pos.values() for v in p)
    # depth gives x; the transformer skips a column
    assert pos["src"][0] < pos["a"][0] < pos["b"][0] < pos["c"][0] < pos["lv"][0]
    # behind the open switch: still next to the bus that reaches it
    assert pos["d"][0] == pos["b"][0] and pos["e"][0] == pos["c"][0]
    # the isolated bus goes below everything else
    assert pos["f"][1] > max(p[1] for b, p in pos.items() if b != "f")
    m = measure(g, pos)
    assert (m.crossings, m.overlaps, m.through) == (0, 0, 0)


def test_deterministic() -> None:
    g = _small(True)
    assert tree_layout(g) == tree_layout(
        Graph(list(reversed(g.buses)), g.edges[::-1], "src", g.shunts)
    )


@pytest.fixture(scope="module")
def feeders():
    pytest.importorskip("dss", reason="dss-python not installed (pip install -e '.[grid]')")
    out = {}
    for f in FEEDERS:
        model = load_model(f)
        out[f] = (build_graph(model), file_coordinates(model))
    return out


@pytest.mark.opendss
@pytest.mark.parametrize("feeder", FEEDERS)
def test_auto_layout_on_ieee(feeders, feeder: str) -> None:
    g, _ = feeders[feeder]
    pos = tree_layout(g, "packed-mid")
    assert set(pos) == set(g.buses)
    m = measure(g, pos)
    assert (m.crossings, m.overlaps) == (0, 0)
    assert m.through <= 1  # IEEE 123: one fan edge grazes a sibling's load


@pytest.mark.opendss
def test_mixed_ieee123(feeders) -> None:
    g, coords = feeders["ieee123"]
    fixed = scale_coordinates(g, coords)
    pos, placed = place_missing(g, fixed)
    assert set(pos) == set(g.buses)
    assert {"300_open", "94_open"} <= set(placed)
    # coincident pairs: the bus nearer the source stays, the other one moves
    assert {"150r", "9r", "25r", "160r"} <= set(placed)
    for b in ("150", "9", "25", "160"):
        assert pos[b] == fixed[b]
    for b, p in fixed.items():
        if b not in placed:
            assert pos[b] == p
    before, after = measure(g, fixed), measure(g, pos)
    assert after.crossings <= before.crossings and after.overlaps <= before.overlaps
    assert after.overlap_buses == ["15", "34"]  # from the file itself, kept
