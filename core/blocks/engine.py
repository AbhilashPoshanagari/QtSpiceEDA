"""ExecutionEngine: runs a block diagram with a fixed time step. Qt-free.

The engine gets live block objects (functions and stateful classes) by spec name; it
never reads or executes source code itself (the block runner does that, in its own
process).

``compile`` orders the blocks with ``graphlib.TopologicalSorter``. A plain block
depends on the blocks that feed its inputs. A stateful block's ``output()`` reads only
its state, so it depends on nothing: that is what lets a feedback loop through an
Integrator or a UnitDelay run. A loop made only of plain blocks has no start and
raises AlgebraicLoopError naming its blocks.

``run(t_end, dt)`` samples ``t = 0, dt, 2*dt, ... t_end``. Each step (``tick``):

1. stateful blocks give ``output()`` and plain blocks are evaluated, in compiled order;
2. every Scope records its input;
3. every stateful block gets ``step(inputs..., dt)`` to advance its state.

The result has one vector per Scope, named after its ``label`` (or its block id when
the label is blank), plus the ``time`` axis.

Physical networks (C3, rule 14). With a ``PhysicalRegistry``, ``compile`` finds the
physical blocks' networks with ``NetworkExtractor`` (an error there is a
DiagramError) and turns each network into ONE stateful node, ``networks[i].id``
(``"physical network 1"``, ...). Its inputs are the signal inputs of its blocks,
its outputs their sensor outputs, both named ``"<block>.<port>"``; wires from and to
those ports are rerouted to the node. Like every stateful block its outputs depend
only on its state -- the sensor values of the previous tick -- so a controller loop
through a network is never an algebraic loop, and the loop rule is unchanged.

The engine does not solve networks. ``begin(..., networks={id: block})`` takes one
``NetworkBlock`` per network from the caller (the CoSimulator, C2), and the caller
drives ``tick(k)`` itself; ``run()`` refuses a diagram with physical networks.
"""

from __future__ import annotations

import graphlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from core.blocks.diagram import Diagram, DiagramError
from core.blocks.spec import BlockSpec
from core.netlist.model import ERROR
from core.physical.component import NetworkDiagnostic
from core.physical.network import NetworkExtractor, PhysicalNetwork, PortRef
from core.physical.registry import PhysicalRegistry
from core.sim.result import SimulationResult

SCOPE = "Scope"
TIME = "time"
ANALYSIS = "blocks"
NETWORK_SPEC = "PhysicalNetwork"


class AlgebraicLoopError(DiagramError):
    """A feedback loop with no stateful block in it."""

    def __init__(self, blocks: list[str]) -> None:
        self.blocks = blocks
        super().__init__(
            f"algebraic loop through {', '.join(blocks)}: add a stateful block "
            "(Integrator, UnitDelay, ...) to the loop"
        )


class BlockError(RuntimeError):
    """A block raised an exception or returned the wrong number of outputs."""

    def __init__(self, block_id: str, message: str) -> None:
        self.block_id = block_id
        super().__init__(f"block {block_id}: {message}")


class NetworkError(DiagramError):
    """The physical networks of a diagram failed their checks (F5)."""

    def __init__(self, diagnostics: list[NetworkDiagnostic]) -> None:
        self.diagnostics = diagnostics
        super().__init__("; ".join(f"{d.block}: {d.message}" for d in diagnostics))


class NetworkBlock(Protocol):
    """What the caller supplies for each physical network node."""

    def output(self) -> Mapping[str, float]:
        """Sensor values by ``"<block>.<port>"`` (the previous tick's)."""
        ...

    def step(self, inputs: Mapping[str, object]) -> None:
        """Signal input values by ``"<block>.<port>"``, to hold until the next tick."""
        ...


@dataclass
class CompiledNetwork:
    """One physical network as a stateful node of the engine."""

    id: str
    network: PhysicalNetwork
    inputs: list[PortRef] = field(default_factory=list)
    """(block, signal input) in diagram order."""
    outputs: list[PortRef] = field(default_factory=list)
    """(block, sensor output) in diagram order."""


@dataclass
class _Node:
    id: str
    spec: BlockSpec
    obj: object
    settings: dict[str, object]
    sources: dict[str, tuple[str, str]]
    """input port -> (source block id, source output port)"""
    network: CompiledNetwork | None = None


def port_key(ref: PortRef) -> str:
    """``(block, port)`` -> ``"block.port"``, the port name on a network node."""
    return f"{ref[0]}.{ref[1]}"


class ExecutionEngine:
    """Compiles a diagram once, then runs it as often as needed."""

    def __init__(
        self, blocks: Mapping[str, object], physical: PhysicalRegistry | None = None
    ) -> None:
        self._blocks = dict(blocks)
        self._physical = physical
        self._order: list[_Node] = []
        self._scopes: dict[str, str] = {}  # scope block id -> vector name
        self._compiled = False
        self.networks: list[CompiledNetwork] = []
        self.warnings: list[NetworkDiagnostic] = []
        self._run: _RunState | None = None

    @property
    def order(self) -> list[str]:
        """Block ids in the order they are evaluated each step."""
        return [n.id for n in self._order]

    @property
    def scope_names(self) -> list[str]:
        return list(self._scopes.values())

    def compile(self, diagram: Diagram) -> None:
        """Check the diagram and fix the evaluation order. Raises DiagramError."""
        nodes: dict[str, _Node] = {}
        unconnected: list[str] = []
        physical: list[str] = []
        for inst in diagram.blocks:
            spec = diagram.spec(inst.id)
            if spec.is_physical:
                physical.append(inst.id)
                continue
            obj = self._blocks.get(spec.name)
            if obj is None:
                raise DiagramError(f"no code for block type {spec.name!r} (block {inst.id})")
            nodes[inst.id] = _Node(inst.id, spec, obj, diagram.effective_settings(inst.id), {})

        self.networks, self.warnings = [], []
        owner: dict[str, CompiledNetwork] = {}  # physical block id -> its network
        if physical:
            self._compile_networks(diagram, owner)

        def source_of(block_id: str, port: str) -> tuple[str, str] | None:
            wire = diagram.wire_into(block_id, port)
            if wire is None:
                return None
            net = owner.get(wire.src_block)
            if net is not None:
                return (net.id, port_key((wire.src_block, wire.src_port)))
            return (wire.src_block, wire.src_port)

        for node in nodes.values():
            for port in node.spec.inputs:
                src = source_of(node.id, port)
                if src is None:
                    unconnected.append(f"{node.id}.{port}")
                else:
                    node.sources[port] = src
        for net in self.networks:
            spec = BlockSpec(
                name=NETWORK_SPEC,
                inputs=[port_key(r) for r in net.inputs],
                outputs=[port_key(r) for r in net.outputs],
                stateful=True,
            )
            node = _Node(net.id, spec, None, {}, {}, net)
            for ref in net.inputs:
                src = source_of(*ref)
                if src is None:
                    unconnected.append(port_key(ref))
                else:
                    node.sources[port_key(ref)] = src
            nodes[net.id] = node
        if unconnected:
            raise DiagramError(f"unconnected input(s): {', '.join(unconnected)}")

        sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
        for node in nodes.values():
            deps = [] if node.spec.stateful else [src for src, _ in node.sources.values()]
            sorter.add(node.id, *deps)
        try:
            order = list(sorter.static_order())
        except graphlib.CycleError as err:
            cycle: list[str] = list(err.args[1])
            # CycleError lists each node before the node it feeds, with the first one
            # repeated at the end.
            raise AlgebraicLoopError(cycle[:-1]) from None

        self._scopes = {}
        names: dict[str, str] = {}
        for node in nodes.values():
            if node.spec.name != SCOPE:
                continue
            label = str(node.settings.get("label") or "").strip()
            name = label or node.id
            if name == TIME:
                raise DiagramError(f"Scope {node.id}: the name {TIME!r} is taken by the axis")
            if name in names:
                raise DiagramError(
                    f"Scopes {names[name]} and {node.id} both record {name!r}: "
                    "give them different labels"
                )
            names[name] = node.id
            self._scopes[node.id] = name

        self._order = [nodes[i] for i in order]
        self._compiled = True

    def _compile_networks(self, diagram: Diagram, owner: dict[str, CompiledNetwork]) -> None:
        if self._physical is None:
            first = next(b.id for b in diagram.blocks if diagram.spec(b.id).is_physical)
            raise DiagramError(
                f"block {first} is a physical block: this run has no physical components"
            )
        extracted = NetworkExtractor(self._physical).extract(diagram)
        errors = [d for d in extracted.diagnostics if d.severity == ERROR]
        if errors:
            raise NetworkError(errors)
        self.warnings = [d for d in extracted.diagnostics if d.severity != ERROR]
        for i, network in enumerate(extracted.networks, start=1):
            net = CompiledNetwork(f"physical network {i}", network)
            for block in network.blocks:
                spec = diagram.spec(block.id)
                net.inputs += [(block.id, p) for p in spec.inputs]
                net.outputs += [(block.id, p) for p in spec.outputs]
                owner[block.id] = net
            self.networks.append(net)

    # ---- running

    def run(self, t_end: float, dt: float) -> SimulationResult:
        """Run from t = 0 to ``t_end`` with step ``dt``."""
        if not self._compiled:
            raise RuntimeError("compile() a diagram before run()")
        if self.networks:
            raise DiagramError(
                "this diagram has physical networks: it needs a combined run (CoSimulator)"
            )
        self.begin(t_end, dt)
        assert self._run is not None
        for k in range(self._run.steps):
            self.tick(k)
        return self.finish()

    def begin(
        self, t_end: float, dt: float, networks: Mapping[str, NetworkBlock] | None = None
    ) -> int:
        """Set up a run of ``t_end`` with step ``dt``; returns the number of ticks.

        ``networks`` gives a NetworkBlock for every ``networks[i].id``.
        """
        if not self._compiled:
            raise RuntimeError("compile() a diagram before run()")
        if not (math.isfinite(dt) and dt > 0):
            raise ValueError(f"time step must be a positive number, not {dt!r}")
        if not (math.isfinite(t_end) and t_end >= 0):
            raise ValueError(f"end time must be zero or positive, not {t_end!r}")
        networks = networks or {}
        missing = [n.id for n in self.networks if n.id not in networks]
        if missing:
            raise ValueError(f"no NetworkBlock for {', '.join(missing)}")
        steps = round(t_end / dt) + 1
        stateful = [n for n in self._order if n.spec.stateful]
        instances: dict[str, object] = {}
        for node in stateful:
            if node.network is not None:
                instances[node.id] = networks[node.id]
                continue
            try:
                instances[node.id] = node.obj(**node.settings)  # type: ignore[operator]
            except Exception as err:
                raise BlockError(node.id, f"__init__ failed: {err!r}") from err
        self._run = _RunState(
            dt=float(dt),
            steps=steps,
            times=np.arange(steps, dtype=float) * dt,
            stateful=stateful,
            instances=instances,
            records={bid: np.zeros(steps) for bid in self._scopes},
        )
        return steps

    def tick(self, k: int) -> None:
        """Run tick ``k`` (t = k * dt): outputs and blocks, Scopes, then ``step``."""
        run = self._run
        if run is None:
            raise RuntimeError("begin() a run before tick()")
        if k != run.done or k >= run.steps:
            raise ValueError(f"tick {k} out of order (next is {run.done} of {run.steps})")
        t = float(run.times[k])
        dt = run.dt
        values = run.values
        for node in self._order:
            if node.id in run.records:
                src = node.sources["u"]
                try:
                    run.records[node.id][k] = float(values[src])  # type: ignore[arg-type]
                except (TypeError, ValueError) as err:
                    raise BlockError(node.id, f"cannot record {values[src]!r}") from err
                continue
            if node.network is not None:
                sensed = _call(node, run.instances[node.id].output)  # type: ignore[attr-defined]
                for name in node.spec.outputs:
                    try:
                        values[(node.id, name)] = sensed[name]  # type: ignore[index]
                    except (KeyError, TypeError) as err:
                        raise BlockError(node.id, f"no value for sensor {name}") from err
                continue
            if node.spec.stateful:
                out = _call(node, run.instances[node.id].output)  # type: ignore[attr-defined]
            else:
                kwargs = self._inputs(node, values, t, dt)
                out = _call(node, node.obj, **kwargs, **node.settings)
            self._store(node, out, values)
        for node in run.stateful:
            kwargs = self._inputs(node, values, t, dt)
            instance = run.instances[node.id]
            if node.network is not None:
                _call(node, instance.step, inputs=kwargs)  # type: ignore[attr-defined]
            else:
                _call(node, instance.step, **kwargs)  # type: ignore[attr-defined]
        run.done = k + 1

    @property
    def ticks_done(self) -> int:
        return self._run.done if self._run is not None else 0

    def finish(self) -> SimulationResult:
        """The Scope vectors of the ticks run so far."""
        run = self._run
        if run is None:
            raise RuntimeError("begin() a run before finish()")
        n = run.done
        vectors: dict[str, np.ndarray] = {TIME: run.times[:n].copy()}
        for bid, name in self._scopes.items():
            vectors[name] = run.records[bid][:n].copy()
        return SimulationResult(axis=TIME, vectors=vectors, units={TIME: "s"}, analysis=ANALYSIS)

    @staticmethod
    def _inputs(
        node: _Node, values: dict[tuple[str, str], object], t: float, dt: float
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {port: values[src] for port, src in node.sources.items()}
        if "t" in node.spec.time_args:
            kwargs["t"] = t
        if "dt" in node.spec.time_args:
            kwargs["dt"] = dt
        return kwargs

    @staticmethod
    def _store(node: _Node, out: object, values: dict[tuple[str, str], object]) -> None:
        outputs = node.spec.outputs
        if not outputs:
            return
        if len(outputs) == 1:
            values[(node.id, outputs[0])] = out
            return
        if not isinstance(out, tuple | list) or len(out) != len(outputs):
            raise BlockError(
                node.id,
                f"must return {len(outputs)} values ({', '.join(outputs)}), got {out!r}",
            )
        for port, value in zip(outputs, out, strict=True):
            values[(node.id, port)] = value


@dataclass
class _RunState:
    dt: float
    steps: int
    times: np.ndarray
    stateful: list[_Node]
    instances: dict[str, object]
    records: dict[str, np.ndarray]
    values: dict[tuple[str, str], object] = field(default_factory=dict)
    done: int = 0


def _call(node: _Node, func: object, **kwargs: object) -> object:
    try:
        return func(**kwargs)  # type: ignore[operator]
    except BlockError:
        raise
    except Exception as err:
        raise BlockError(node.id, f"{type(err).__name__}: {err}") from err
