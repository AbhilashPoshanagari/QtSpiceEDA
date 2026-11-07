"""Helpers for the B1-B5 library tests: diagrams of built-in components, mapped to a
deck and run in plain ngspice."""

from __future__ import annotations

import numpy as np

from core.blocks.diagram import Diagram
from core.blocks.library import builtin_specs
from core.blocks.spec import BlockSpec
from core.physical.library import physical_registry
from core.physical.mapper import MappedNetwork, NetworkMapper
from core.physical.network import NetworkExtractor
from core.physical.registry import PhysicalRegistry
from core.sim.ngspice_adapter import NgspiceAdapter
from core.sim.result import SimulationResult


def library_registry() -> PhysicalRegistry:
    return physical_registry()


def new_diagram(reg: PhysicalRegistry | None = None) -> tuple[Diagram, PhysicalRegistry]:
    """An empty diagram that knows the signal blocks and the built-in library."""
    reg = reg or library_registry()
    signal = {s.name: s for s in builtin_specs()}

    def lookup(name: str) -> BlockSpec | None:
        spec = reg.spec(name)
        return spec.block if spec is not None else signal.get(name)

    return Diagram(lookup), reg


def wire(d: Diagram, a: str, b: str) -> None:
    d.add_physical_wire(*a.split("."), *b.split("."))


def add_solver(
    d: Diagram, reg: PhysicalRegistry, port: str, settings: dict[str, object] | None = None
) -> None:
    """Add a Solver Configuration ``solver`` and wire it to ``port`` ("block.port")."""
    d.add_block("SolverConfiguration", settings=settings or {}, block_id="solver")
    wire(d, "solver.port", port)


def mapped(
    d: Diagram, reg: PhysicalRegistry, analysis: str | None = None, **kw: object
) -> MappedNetwork:
    extracted = NetworkExtractor(reg).extract(d)
    assert extracted.ok, extracted.diagnostics
    (network,) = extracted.networks
    out = NetworkMapper(reg).map(network, analysis, **kw)  # type: ignore[arg-type]
    assert out.ok, out.diagnostics
    return out


def run(deck: str) -> SimulationResult:
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    sim.load(deck)
    sim.run()
    result = sim.results()
    assert result is not None
    return result


def simulate(
    d: Diagram, reg: PhysicalRegistry, analysis: str
) -> tuple[MappedNetwork, SimulationResult]:
    out = mapped(d, reg, analysis, external=False)
    return out, run(out.deck)


def vector(out: MappedNetwork, result: SimulationResult, name: str) -> np.ndarray:
    return np.asarray(result.vectors[out.vectors[name]], dtype=float).real


def at(t: np.ndarray, y: np.ndarray, when: float) -> float:
    return float(np.interp(when, t, y))
