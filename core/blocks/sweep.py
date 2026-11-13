"""Parameter sweep (M7): run a block diagram once per value of one block parameter.
Qt-free.

A ``SweepSpec`` names one block, one of its numeric settings and the values to give
it. ``SweepRunner`` runs the diagram once per value, one run after another, each in
its own ``BlockRunner`` child process (``make_runner``), so ngspice and user code
start fresh every time and never run in the UI process. It has the same ``run()`` /
``cancel()`` as a BlockRunner, and its RunOutcome holds ONE SimulationResult
(``combine``):

- the axis is the sorted union of every run's time points (a combined run has a
  point on every tick, so every run's ticks are kept exactly);
- run k's vectors (k = 1 ... n) are interpolated linearly onto that axis and named
  ``<name>@<k>``, e.g. ``inertia.A.w@2``;
- ``runs`` holds one label per run, e.g. ``J=0.002``; the waveform window shows it
  next to the trace and adds every run when a base name is added.

``check`` never raises on a bad sweep (rule 6): it returns diagnostics on the block.
"""

from __future__ import annotations

import copy
import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from core.blocks.diagram import Diagram, DiagramError
from core.blocks.runner import CANCELLED, OK, RunOutcome
from core.netlist.model import ERROR
from core.netlist.parser import parse_number
from core.physical.component import NetworkDiagnostic
from core.sim.result import SimulationResult

MAX_RUNS = 50
"""Most values one sweep may have."""

RUN_SEP = "@"
"""``<name>@<k>``: vector ``name`` of run k (1-based) in a combined sweep result."""

_RUN_NAME = re.compile(r"^(?P<base>.+)@(?P<k>[1-9][0-9]*)$")

_SOLVER_TS = ("SolverConfiguration", "Ts")
"""Not sweepable: a Solver Configuration's Ts is always the run's time step."""


@dataclass(frozen=True)
class SweepSpec:
    block: str
    parameter: str
    values: tuple[float, ...]

    def label(self, k: int) -> str:
        """The label of run ``k`` (0-based), e.g. ``J=0.002``."""
        return f"{self.parameter}={self.values[k]:g}"

    @property
    def labels(self) -> list[str]:
        return [self.label(k) for k in range(len(self.values))]

    def to_dict(self) -> dict[str, Any]:
        return {"block": self.block, "parameter": self.parameter, "values": list(self.values)}


def parse_values(text: str) -> tuple[list[float], list[str]]:
    """Values separated by spaces, commas or semicolons; SPICE suffixes allowed
    (``1m 2m 5m``). Returns (values, problems); never raises."""
    values: list[float] = []
    problems: list[str] = []
    for item in re.split(r"[\s,;]+", text.strip()):
        if not item:
            continue
        value = parse_number(item)
        if value is None or not math.isfinite(value):
            problems.append(f"{item!r} is not a number such as 0.01, 10m or 2.2u")
        else:
            values.append(value)
    if not values and not problems:
        problems.append("give at least one value")
    if len(values) > MAX_RUNS:
        problems.append(f"{len(values)} values: a sweep has at most {MAX_RUNS}")
    return values, problems


def check(spec: SweepSpec, diagram: Diagram) -> list[NetworkDiagnostic]:
    """What is wrong with sweeping ``spec`` on ``diagram`` (empty: nothing)."""

    def fail(message: str) -> list[NetworkDiagnostic]:
        return [NetworkDiagnostic(spec.block, ERROR, message)]

    if not spec.values:
        return fail("the sweep has no values")
    if len(spec.values) > MAX_RUNS:
        return fail(f"{len(spec.values)} values: a sweep has at most {MAX_RUNS}")
    try:
        inst = diagram.block(spec.block)
        settings = diagram.effective_settings(spec.block)
    except DiagramError:
        return fail(f"the diagram has no block {spec.block!r}")
    if spec.parameter not in settings:
        return fail(f"{inst.spec_name} has no parameter {spec.parameter!r}")
    default = settings[spec.parameter]
    if isinstance(default, bool) or not isinstance(default, int | float):
        return fail(f"{spec.parameter} is not a number: only numeric parameters can be swept")
    if (inst.spec_name, spec.parameter) == _SOLVER_TS:
        return fail("Ts is the run's time step: sweep another parameter")
    if isinstance(default, int):
        whole = [v for v in spec.values if v != int(v)]
        if whole:
            return fail(f"{spec.parameter} is a whole number, not {whole[0]:g}")
    return []


def apply(diagram: dict[str, Any], spec: SweepSpec, value: float) -> dict[str, Any]:
    """A copy of ``diagram`` (``Diagram.to_dict()``) with the swept setting set."""
    out = copy.deepcopy(diagram)
    for block in out.get("blocks", []):
        if block.get("id") == spec.block:
            block.setdefault("settings", {})[spec.parameter] = value
    return out


def typed(diagram: Diagram, spec: SweepSpec) -> SweepSpec:
    """``spec`` with its values of the parameter's type (int for a whole number)."""
    default = diagram.effective_settings(spec.block)[spec.parameter]
    if isinstance(default, int) and not isinstance(default, bool):
        return SweepSpec(spec.block, spec.parameter, tuple(int(v) for v in spec.values))
    return SweepSpec(spec.block, spec.parameter, tuple(float(v) for v in spec.values))


def run_name(name: str, k: int) -> str:
    """Vector ``name`` of run ``k`` (1-based): ``name@k``."""
    return f"{name}{RUN_SEP}{k}"


def split_run_name(name: str, runs: int) -> tuple[str, int] | None:
    """``("w", 2)`` for ``"w@2"`` when 1 <= 2 <= runs, else None."""
    m = _RUN_NAME.match(name)
    if m is None:
        return None
    k = int(m.group("k"))
    return (m.group("base"), k) if k <= runs else None


def combine(results: list[SimulationResult], labels: list[str]) -> SimulationResult:
    """One SimulationResult of the runs of a sweep (see the module docstring).

    Every run must have the same axis; a vector missing from a run is left out for
    that run only.
    """
    if not results or len(results) != len(labels):
        raise ValueError("combine needs one label per result and at least one result")
    axis = results[0].axis
    if not axis or any(r.axis != axis for r in results):
        raise ValueError("every run of a sweep must have the same axis")
    axes = [np.asarray(r.vectors[axis]).real.astype(float) for r in results]
    x = np.unique(np.concatenate(axes))
    vectors: dict[str, np.ndarray] = {axis: x}
    units: dict[str, str] = {axis: results[0].units.get(axis, "")}
    for k, (result, rx) in enumerate(zip(results, axes, strict=True), start=1):
        for name in result.trace_names():
            data = np.asarray(result.vectors[name])
            if data.shape != rx.shape:
                continue
            key = run_name(name, k)
            vectors[key] = np.interp(x, rx, data.real.astype(float))
            units[key] = result.units.get(name, "")
    return SimulationResult(
        axis=axis, vectors=vectors, units=units, analysis=results[0].analysis, runs=list(labels)
    )


class _Runner(Protocol):
    def run(self) -> RunOutcome: ...

    def cancel(self) -> None: ...


class SweepRunner:
    """Runs ``diagram`` once per value of ``spec``, one run after another.

    ``make_runner(diagram_dict)`` makes the runner of one run (a BlockRunner).
    ``progress(k, n, label)`` is called before run k (1-based) starts, from the
    thread that calls ``run()``. Create a new SweepRunner for each sweep.
    """

    def __init__(
        self,
        diagram: dict[str, Any],
        spec: SweepSpec,
        make_runner: Callable[[dict[str, Any]], _Runner],
        progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.diagram = copy.deepcopy(diagram)
        self.spec = spec
        self._make = make_runner
        self._progress = progress
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._current: _Runner | None = None

    def cancel(self) -> None:
        """Stop the sweep: the running run stops at once, no further run starts."""
        self._cancelled.set()
        with self._lock:
            current = self._current
        if current is not None:
            current.cancel()

    def run(self) -> RunOutcome:
        spec = self.spec
        n = len(spec.values)
        console: list[str] = []
        results: list[SimulationResult] = []
        for k, value in enumerate(spec.values, start=1):
            label = spec.label(k - 1)
            if self._cancelled.is_set():
                return RunOutcome(CANCELLED, message="stopped", console=console)
            if self._progress is not None:
                self._progress(k, n, label)
            console.append(f"Sweep run {k} of {n}: {spec.block}.{label}")
            runner = self._make(apply(self.diagram, spec, value))
            with self._lock:
                self._current = runner
            if self._cancelled.is_set():
                return RunOutcome(CANCELLED, message="stopped", console=console)
            outcome = runner.run()
            with self._lock:
                self._current = None
            console.extend(outcome.console)
            if not outcome.ok or outcome.result is None:
                message = outcome.message
                if outcome.status != CANCELLED:
                    message = f"sweep run {k} of {n} ({label}): {outcome.message}"
                return RunOutcome(outcome.status, None, message, outcome.traceback, console)
            results.append(outcome.result)
        try:
            combined = combine(results, spec.labels)
        except ValueError as err:
            return RunOutcome("error", message=f"sweep: {err}", console=console)
        return RunOutcome(OK, combined, console=console)
