"""S1 spike checks: `pytest -q scratch/phase5/spike1_canvas` (not part of `pytest -q`).

Only the Qt-free parts are tested; the measurements are bench.py / run_all.py.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

from scene import (  # noqa: E402
    SpatialIndex,
    build_scene,
    hit_linear,
    load_ieee123,
    replicate,
)

pytest.importorskip("dss", reason="dss-python not installed (pip install -e '.[grid]')")


@pytest.fixture(scope="module")
def scene():
    model, _ = load_ieee123()
    return build_scene(model)


@pytest.mark.opendss
def test_every_element_has_a_glyph(scene) -> None:
    # IEEE 123: 132 buses; 118 lines + 8 switches + 8 two-winding transformers;
    # 91 loads + 4 capacitors + 1 source.
    assert len(scene.buses) == 132
    assert len(scene.branches) == 134
    assert len(scene.shunts) == 96
    four = replicate(scene, 4)
    assert len(four.buses) == 4 * 132
    assert len({b.name for b in four.buses}) == 4 * 132


@pytest.mark.opendss
@pytest.mark.parametrize("copies", [1, 4])
def test_index_agrees_with_linear_scan(scene, copies: int) -> None:
    sc = replicate(scene, copies)
    idx = SpatialIndex(sc)
    x0, y0, x1, y1 = sc.bounds()
    rnd = random.Random(7)
    for _ in range(3000):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        for tol in (0.2, 1.0, 3.0):
            assert idx.hit(x, y, tol) == hit_linear(sc, x, y, tol)


@pytest.mark.opendss
def test_index_follows_a_moved_bus(scene) -> None:
    sc = replicate(scene, 1)
    idx = SpatialIndex(sc)
    i = next(k for k, b in enumerate(sc.buses) if b.name == "13")
    sc.buses[i].x += 40
    idx.move_bus(i, sc.branches_of()[i], sc.shunts_of().get(i, []))
    b = sc.buses[i]
    assert idx.hit(b.x, b.y, 0.1) == ("bus", i)
    rnd = random.Random(3)
    x0, y0, x1, y1 = sc.bounds()
    for _ in range(2000):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        assert idx.hit(x, y, 1.0) == hit_linear(sc, x, y, 1.0)
