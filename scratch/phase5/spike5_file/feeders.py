"""S5 spike: the extracted GridModels of IEEE 13/34/123 and a DiagramModel from their bus
coordinates.

The models are extracted after one solve (STATIC controls, MaxControlIter 30), in the
same state as the reference files (AFTER_COMPILE: bus coordinates of 34 and 123), in a
ChildProcess, so OpenDSS never runs in this process (rule 5).

The diagram reuses the S3 layout code (scratch/phase5/spike3_layout/layout.py):
scale_coordinates for buses with file coordinates, place_missing for the rest (IEEE 123
300_open, 94_open) and for coincident buses.
"""

from __future__ import annotations

import importlib.util
import pickle
import sys
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from designfile import BusGlyph, DiagramModel, ElementGlyph  # noqa: E402

from core.blocks.process import ChildProcess  # noqa: E402
from core.grid.model import GridModel  # noqa: E402

FEEDERS = ("ieee13", "ieee34", "ieee123")


def _s3_layout() -> Any:
    name = "spike3_layout_module"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "scratch" / "phase5" / "spike3_layout" / "layout.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _extract_child(conn: Connection, feeder: str) -> None:
    from core.grid.config import CONTROL_STATIC, PowerFlowConfig
    from core.grid.engines.opendss.engine import OpenDSSEngine
    from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES

    engine = OpenDSSEngine()
    try:
        assert engine.load_native(FEEDER_FILES[feeder]) == []
        for command in AFTER_COMPILE[feeder]:
            engine.context.Text.Command = command
        result = engine.run(PowerFlowConfig(control_mode=CONTROL_STATIC, max_control_iterations=30))
        assert result.converged, result.diagnostics
        model, diags = engine.extract_model()
        conn.send(pickle.dumps((model, diags)))
    finally:
        engine.close()
        conn.close()


def extracted(feeder: str) -> GridModel:
    outcome = ChildProcess(_extract_child, (feeder,), timeout=60, name="extract").wait()
    assert outcome.ok, outcome.error
    model, _diags = pickle.loads(outcome.message)
    return model


def _graph(model: GridModel) -> Any:
    lay = _s3_layout()
    edges = []
    for ln in model.lines.values():
        edges.append(lay.Edge(ln.id, "line", ln.bus1.bus, ln.bus2.bus, ln.enabled))
    for sw in model.switches.values():
        edges.append(lay.Edge(sw.id, "switch", sw.bus1.bus, sw.bus2.bus, sw.closed and sw.enabled))
    for tr in model.transformers.values():
        first = tr.windings[0].terminal.bus
        for w in tr.windings[1:]:
            edges.append(lay.Edge(tr.id, "transformer", first, w.terminal.bus, tr.enabled))
    shunts: dict[str, int] = {}
    for bus in _shunt_buses(model):
        shunts[bus] = shunts.get(bus, 0) + 1
    source = next(iter(model.sources.values())).bus1.bus
    return lay.Graph(sorted(model.buses), edges, source, shunts)


def _shunts(model: GridModel) -> list[tuple[str, str]]:
    """(element id, bus) of every shunt glyph, in model order."""
    out = [(s.id, s.bus1.bus) for s in model.sources.values()]
    for group in (model.loads, model.capacitors, model.pvsystems):
        out += [(el.id, el.terminal.bus) for el in group.values()]
    return out


def _shunt_buses(model: GridModel) -> list[str]:
    return [bus for _id, bus in _shunts(model)]


def diagram_from_coordinates(model: GridModel) -> DiagramModel:
    """One bus glyph per bus, one element glyph per drawn element.

    Drawn: sources, lines, switches, transformers, loads, capacitors, PV systems.
    Not drawn (no glyph; open decision for D3, rule 21): linecodes and regulators.
    """
    lay = _s3_layout()
    graph = _graph(model)
    coords = {n: (b.x, b.y) for n, b in model.buses.items() if b.x is not None and b.y is not None}
    fixed = lay.scale_coordinates(graph, coords)
    pos, _placed = lay.place_missing(graph, fixed)
    if len(pos) < len(model.buses):  # no coordinates at all: tree layout
        pos = lay.tree_layout(graph, "packed-mid")
    diagram = DiagramModel()
    for name in model.buses:
        x, y = pos[name]
        diagram.bus_glyphs[name] = BusGlyph(float(x), float(y), 0, float(graph.bus_length(name)))

    def mid(a: str, b: str) -> tuple[float, float]:
        return (pos[a][0] + pos[b][0]) / 2, (pos[a][1] + pos[b][1]) / 2

    for group in (model.lines, model.switches):
        for el in group.values():
            x, y = mid(el.bus1.bus, el.bus2.bus)
            diagram.element_glyphs[el.id] = ElementGlyph(x, y, 0, 0.0, -1.0)
    for tr in model.transformers.values():
        x, y = mid(tr.windings[0].terminal.bus, tr.windings[1].terminal.bus)
        diagram.element_glyphs[tr.id] = ElementGlyph(x, y, 90, 1.0, 0.0)
    per_bus: dict[str, list[str]] = {}
    for el_id, bus in _shunts(model):
        per_bus.setdefault(bus, []).append(el_id)
    for bus, ids in per_bus.items():
        n = len(ids)
        for k, el_id in enumerate(ids):
            x = pos[bus][0] + (k - (n - 1) / 2) * lay.SHUNT_PITCH
            y = pos[bus][1] + lay.SHUNT_DY
            diagram.element_glyphs[el_id] = ElementGlyph(float(x), float(y), 180, 0.0, 1.0)
    return diagram
