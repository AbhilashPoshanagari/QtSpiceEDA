"""ExecutionEngine: runs a block diagram with a fixed time step. Qt-free.

The engine gets live block objects (functions and stateful classes) by spec name; it
never reads or executes source code itself (the block runner does that, in its own
process).

``compile`` orders the blocks with ``graphlib.TopologicalSorter``. A plain block
depends on the blocks that feed its inputs. A stateful block's ``output()`` reads only
its state, so it depends on nothing: that is what lets a feedback loop through an
Integrator or a UnitDelay run. A loop made only of plain blocks has no start and
raises AlgebraicLoopError naming its blocks.

``run(t_end, dt)`` samples ``t = 0, dt, 2*dt, ... t_end``. Each step:

1. stateful blocks give ``output()`` and plain blocks are evaluated, in compiled order;
2. every Scope records its input;
3. every stateful block gets ``step(inputs..., dt)`` to advance its state.

The result has one vector per Scope, named after its ``label`` (or its block id when
the label is blank), plus the ``time`` axis.
"""

from __future__ import annotations

import graphlib
import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from core.blocks.diagram import Diagram, DiagramError
from core.blocks.spec import BlockSpec
from core.sim.result import SimulationResult

SCOPE = "Scope"
TIME = "time"
ANALYSIS = "blocks"


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


@dataclass
class _Node:
    id: str
    spec: BlockSpec
    obj: object
    settings: dict[str, object]
    sources: dict[str, tuple[str, str]]
    """input port -> (source block id, source output port)"""


class ExecutionEngine:
    """Compiles a diagram once, then runs it as often as needed."""

    def __init__(self, blocks: Mapping[str, object]) -> None:
        self._blocks = dict(blocks)
        self._order: list[_Node] = []
        self._scopes: dict[str, str] = {}  # scope block id -> vector name
        self._compiled = False

    @property
    def order(self) -> list[str]:
        """Block ids in the order they are evaluated each step."""
        return [n.id for n in self._order]

    def compile(self, diagram: Diagram) -> None:
        """Check the diagram and fix the evaluation order. Raises DiagramError."""
        nodes: dict[str, _Node] = {}
        unconnected: list[str] = []
        for inst in diagram.blocks:
            spec = diagram.spec(inst.id)
            obj = self._blocks.get(spec.name)
            if obj is None:
                raise DiagramError(f"no code for block type {spec.name!r} (block {inst.id})")
            sources: dict[str, tuple[str, str]] = {}
            for port in spec.inputs:
                wire = diagram.wire_into(inst.id, port)
                if wire is None:
                    unconnected.append(f"{inst.id}.{port}")
                else:
                    sources[port] = (wire.src_block, wire.src_port)
            nodes[inst.id] = _Node(inst.id, spec, obj, diagram.effective_settings(inst.id), sources)
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

    def run(self, t_end: float, dt: float) -> SimulationResult:
        """Run from t = 0 to ``t_end`` with step ``dt``."""
        if not self._compiled:
            raise RuntimeError("compile() a diagram before run()")
        if not (math.isfinite(dt) and dt > 0):
            raise ValueError(f"time step must be a positive number, not {dt!r}")
        if not (math.isfinite(t_end) and t_end >= 0):
            raise ValueError(f"end time must be zero or positive, not {t_end!r}")
        steps = round(t_end / dt) + 1
        times = np.arange(steps, dtype=float) * dt

        stateful = [n for n in self._order if n.spec.stateful]
        instances: dict[str, object] = {}
        for node in stateful:
            try:
                instances[node.id] = node.obj(**node.settings)  # type: ignore[operator]
            except Exception as err:
                raise BlockError(node.id, f"__init__ failed: {err!r}") from err

        records = {bid: np.zeros(steps) for bid in self._scopes}
        values: dict[tuple[str, str], object] = {}

        for k in range(steps):
            t = float(times[k])
            for node in self._order:
                if node.id in records:
                    src = node.sources["u"]
                    try:
                        records[node.id][k] = float(values[src])  # type: ignore[arg-type]
                    except (TypeError, ValueError) as err:
                        raise BlockError(node.id, f"cannot record {values[src]!r}") from err
                    continue
                if node.spec.stateful:
                    out = _call(node, instances[node.id].output)  # type: ignore[attr-defined]
                else:
                    kwargs = self._inputs(node, values, t, dt)
                    out = _call(node, node.obj, **kwargs, **node.settings)
                self._store(node, out, values)
            for node in stateful:
                kwargs = self._inputs(node, values, t, dt)
                _call(node, instances[node.id].step, **kwargs)  # type: ignore[attr-defined]

        vectors: dict[str, np.ndarray] = {TIME: times}
        for bid, name in self._scopes.items():
            vectors[name] = records[bid]
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


def _call(node: _Node, func: object, **kwargs: object) -> object:
    try:
        return func(**kwargs)  # type: ignore[operator]
    except BlockError:
        raise
    except Exception as err:
        raise BlockError(node.id, f"{type(err).__name__}: {err}") from err
