"""CoSimulator: physical networks in ngspice + signal blocks in the v0.1 engine. Qt-free.

The combined run (C2). All physical networks of a compiled diagram are mapped into
ONE ngspice deck (``NetworkMapper.map_all``; ngspice holds one circuit per process)
and solved by ngspice only (rule 11). The signal blocks run in the v0.1 engine with
a fixed controller tick ``Ts`` (the diagram's time step), in which each network is
one stateful node (C3, rule 14).

At every accepted tick ``t = k * Ts``:

1. read the sensor values of that time point;
2. run the signal blocks once (``engine.tick(k)``);
3. set the external source values, which ngspice then holds until the next tick
   (zero-order hold, one sample of delay).

Ticks are run only from SendData, which ngspice calls for accepted time points; the
adapter drops points of steps that ngspice reported as rejected. GetVSRCData /
GetISRCData only return the held value, so trial and rejected points never reach the
controller. A breakpoint is set at the next tick from each tick (one at a time:
setting all of them up front grows faster than linear).

The deck runs two ticks past ``t_end`` and stops half a tick after the last tick
(``NgspiceAdapter.run_ticks``). Ts below 10 us is refused (out of scope for v0.3).
A Solver Configuration's ``Ts`` must equal the run's time step; its ``max_step``
caps ngspice's internal step (never above Ts) and reltol / abstol go into the deck.
Signal values become source values through the Domain registry (rule 12).

PWM edges (M4) are made inside ngspice by a comparator of a held duty and a pulse
carrier. A comparator edge is not a breakpoint, so ngspice would step over it; a
``Crossing`` hint makes the CoSimulator set a pair of breakpoints, ``_EDGE_EPS * Ts``
before and after every time in the next tick where the carrier crosses the held value
(``pulse_crossings``), together with the next tick's breakpoint. The comparator
switches between the two accepted points, so the edge is known to within that
distance. (One breakpoint on the crossing itself is not enough: the comparator is
undecided there and ngspice's next step is long.) The controller never sets a source
value at an edge.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from core.blocks.diagram import DiagramError
from core.blocks.engine import ExecutionEngine, NetworkError, port_key
from core.netlist.model import ERROR
from core.physical.component import Pulse
from core.physical.mapper import (
    CrossingHint,
    MappedNetwork,
    NetworkMapper,
    max_step,
    solver_settings,
    source_value,
)
from core.physical.registry import PhysicalRegistry
from core.sim.backend import SimulationError
from core.sim.ngspice_adapter import NgspiceAdapter, SyncHandlers, tran_card
from core.sim.result import SimulationResult

TIME = "time"

MIN_TS = 10e-6
"""Smallest controller tick v0.3 supports."""

_EDGE_EPS = 1e-4
"""Crossing breakpoints are this many Ts before and after the edge. It must stay above
ngspice's smallest breakpoint spacing (5e-5 times the largest step, at most Ts)."""

_TICK_TOL = 1e-6
"""A time point is on tick k when |t - k*Ts| <= _TICK_TOL * Ts."""


def pulse_crossings(wave: Pulse, level: float, t0: float, t1: float) -> list[float]:
    """Times in the open interval (t0, t1) where ``wave`` crosses ``level`` on one of its
    ramps (ngspice ``pulse`` timing), in order.

    Levels at or beyond ``v1`` / ``v2`` give none (the corners are ngspice's own
    breakpoints already); a ramp of zero length gives none either.
    """
    span = wave.v2 - wave.v1
    if not span or not (t1 > t0):
        return []
    frac = (level - wave.v1) / span
    if not 0.0 < frac < 1.0:
        return []
    offsets = []
    if wave.rise > 0:
        offsets.append(wave.rise * frac)
    if wave.fall > 0:
        offsets.append(wave.rise + wave.width + wave.fall * (1.0 - frac))
    if not offsets:
        return []
    if wave.period > 0:
        first = max(0, math.floor((t0 - wave.delay - wave.period) / wave.period))
        last = math.floor((t1 - wave.delay) / wave.period)
        if wave.ncycles:
            last = min(last, int(wave.ncycles) - 1)
        cycles = range(first, last + 1)
    else:
        cycles = range(1)
    out = []
    for n in cycles:
        start = wave.delay + n * wave.period
        out.extend(t for off in offsets if t0 < (t := start + off) < t1)
    return sorted(out)


class CoSimError(RuntimeError):
    """A combined run could not start or did not finish."""


@dataclass
class CoSimRun:
    """What one combined run produced."""

    signal: SimulationResult
    """The engine's Scope vectors, one sample per tick."""
    physical: SimulationResult
    """ngspice's vectors, at its accepted time points."""
    mapped: MappedNetwork
    ts: float
    t_end: float
    warnings: list[str] = field(default_factory=list)
    redo_count: int = 0

    @property
    def result(self) -> SimulationResult:
        """Everything as one SimulationResult (``merge``)."""
        return merge(self)


ANALYSIS = "cosim"


def merge(run: CoSimRun) -> SimulationResult:
    """One SimulationResult for a combined run (C5).

    - axis ``time``: ngspice's accepted time points up to ``t_end`` (every tick is
      one of them, since a breakpoint is set on each);
    - every physical vector, named ``<component>.<port>.<quantity>`` (across
      variables, e.g. ``inertia.A.w``) or ``<component>.<signal>`` (sensor outputs),
      in SI with its unit;
    - every Scope trace, sampled at Ts and held until the next tick on this axis
      (zero-order hold: at the ticks it equals the Scope's samples exactly).
    """
    tol = _TICK_TOL * run.ts
    phys = run.physical
    t_all = np.asarray(phys.vectors[TIME], dtype=float).real
    keep = t_all <= run.t_end + tol
    t = t_all[keep]
    vectors: dict[str, np.ndarray] = {TIME: t}
    units: dict[str, str] = {TIME: "s"}
    for name, source in run.mapped.vectors.items():
        data = phys.vectors.get(source)
        if data is None:
            continue
        vectors[name] = np.asarray(data).real[keep].copy()
        units[name] = run.mapped.units.get(name, "")
    ticks = np.clip(np.floor((t + tol) / run.ts).astype(int), 0, None)
    for name in run.signal.trace_names():
        samples = np.asarray(run.signal.vectors[name])
        if not len(samples):
            continue
        vectors[name] = samples[np.minimum(ticks, len(samples) - 1)]
        if name in run.signal.units:
            units[name] = run.signal.units[name]
    return SimulationResult(axis=TIME, vectors=vectors, units=units, analysis=ANALYSIS)


def node_of(vector: str) -> str:
    """``"V(rotational_1)"`` -> ``"rotational_1"`` (the name SendData uses)."""
    if vector[:2].upper() == "V(" and vector.endswith(")"):
        return vector[2:-1].lower()
    return vector.lower()


class _HeldNetwork:
    """The engine's NetworkBlock for one network: sensor values in, source values out."""

    def __init__(
        self, sensors: dict[str, str], sources: dict[str, tuple[str, str, str]], held: dict
    ) -> None:
        self.sensors = sensors
        """"block.port" -> SendData name of its sensor node."""
        self.sources = sources
        """"block.port" -> (element, kind, domain)."""
        self.held = held
        """Shared: lower-case element name -> value ngspice gets until the next tick."""
        self.values: dict[str, float] = {}

    def sense(self, point: Mapping[str, float]) -> None:
        self.values = {key: float(point[name]) for key, name in self.sensors.items()}

    def output(self) -> Mapping[str, float]:
        return self.values

    def step(self, inputs: Mapping[str, object]) -> None:
        for key, (element, kind, domain) in self.sources.items():
            value = float(inputs[key])  # type: ignore[arg-type]
            if not math.isfinite(value):
                raise ValueError(f"{key} is {value}: a source value must be finite")
            self.held[element] = source_value(domain, kind, value)


class CoSimulator:
    """One combined run of a compiled engine. Create a new one for each run.

    ``adapter`` must be a sync-mode NgspiceAdapter (one is made if none is given);
    it is the only way this module reaches ngspice.
    """

    def __init__(
        self,
        engine: ExecutionEngine,
        registry: PhysicalRegistry,
        adapter: NgspiceAdapter | None = None,
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.adapter = adapter
        self._stop = threading.Event()
        self._error: BaseException | None = None

    def stop(self) -> None:
        """Stop the run from any thread; ``run`` then raises CoSimError."""
        self._stop.set()
        if self.adapter is not None:
            self.adapter.stop()

    def map(self, t_end: float, ts: float) -> MappedNetwork:
        """The deck of this run. Raises NetworkError if a network does not map."""
        networks = [n.network for n in self.engine.networks]
        # No `uic`: with uic ngspice sends no point at t = 0, so tick 0 could not read
        # the sensors there. The run starts from the operating point instead, with the
        # external sources at their initial values (device ic= values are ignored;
        # initial conditions are task M6).
        tran = tran_card(ts, t_end, max_step(solver_settings(networks)))
        mapped = NetworkMapper(self.registry).map_all(networks, tran)
        errors = [d for d in mapped.diagnostics if d.severity == ERROR]
        if errors:
            raise NetworkError(errors)
        return mapped

    def run(self, t_end: float, ts: float) -> CoSimRun:
        """Run ``t = 0, Ts, ... t_end``. Raises DiagramError, CoSimError or BlockError."""
        if not (math.isfinite(ts) and ts > 0):
            raise ValueError(f"time step must be a positive number, not {ts!r}")
        if ts < MIN_TS * (1 - 1e-9):
            raise DiagramError(
                f"the controller tick Ts = {ts:g} s is below the smallest supported {MIN_TS:g} s"
            )
        if not (math.isfinite(t_end) and t_end > 0):
            raise ValueError(f"end time must be a positive number, not {t_end!r}")
        if not self.engine.networks:
            raise DiagramError("this diagram has no physical network")
        for solver in solver_settings([n.network for n in self.engine.networks]):
            if solver.ts is not None and abs(solver.ts - ts) > _TICK_TOL * ts:
                raise DiagramError(
                    f"Solver Configuration {solver.block}: Ts = {solver.ts:g} s, but the "
                    f"run's time step is {ts:g} s; set them to the same value"
                )
        mapped = self.map(t_end, ts)
        clash = [n for n in self.engine.scope_names if n in mapped.vectors]
        if clash:
            raise DiagramError(
                f"Scope {clash[0]!r} has the name of a physical vector: give it another label"
            )

        held: dict[str, float] = {}
        dc: dict[str, float] = {}
        holders: dict[str, _HeldNetwork] = {}
        for net in self.engine.networks:
            blocks = set(net.network.block_ids)
            sensors = {}
            for ref in net.outputs:
                vector = mapped.vectors.get(port_key(ref))
                if vector is None:
                    raise DiagramError(
                        f"block {ref[0]}: signal output {ref[1]!r} has no Sensor in its network"
                    )
                sensors[port_key(ref)] = node_of(vector)
            sources = {}
            for ext in mapped.external_inputs:
                if ext.block in blocks:
                    element = ext.element.lower()
                    sources[f"{ext.block}.{ext.signal}"] = (element, ext.kind, ext.domain)
                    held[element] = source_value(ext.domain, ext.kind, ext.initial)
                    dc[element] = held[element]
            holders[net.id] = _HeldNetwork(sensors, sources, held)

        steps = self.engine.begin(t_end, ts, holders)
        adapter = self.adapter or NgspiceAdapter(sync=True)
        self.adapter = adapter
        warnings: list[str] = []
        tol = _TICK_TOL * ts

        hints: list[CrossingHint] = list(mapped.crossings)

        def schedule(k: int) -> None:
            """Breakpoints for the interval after tick k: the next tick and the edges."""
            if k + 1 >= steps:
                return
            t0, t1 = k * ts, (k + 1) * ts
            eps = _EDGE_EPS * ts
            for hint in hints:
                level = held.get(hint.level.lower(), 0.0)
                for edge in pulse_crossings(hint.carrier, level, t0 + eps, t1 - eps):
                    adapter.set_breakpoint(edge - eps)
                    adapter.set_breakpoint(edge + eps)
            adapter.set_breakpoint(t1)

        def on_point(t: float, point: dict[str, float]) -> None:
            if self._error is not None:
                return
            try:
                while self.engine.ticks_done < steps:
                    k = self.engine.ticks_done
                    tick = k * ts
                    if t < tick - tol:
                        return
                    if t > tick + tol:
                        warnings.append(f"tick {k} (t = {tick:g} s) ran late, at t = {t:g} s")
                    for holder in holders.values():
                        holder.sense(point)
                    self.engine.tick(k)
                    schedule(k)
            except BaseException as err:  # stop the run; run() raises it
                self._error = err
                adapter.stop()

        def on_source(name: str, _t: float) -> float:
            # The deck declares `dc <initial> external` (the operating point uses the
            # initial value), and in a transient ngspice ADDS that dc value to the one
            # returned here (vsrcload.c / isrcload.c, SHARED_MODULE). Subtract it, so
            # the source carries exactly the held value.
            return held.get(name, 0.0) - dc.get(name, 0.0)

        adapter.handlers = SyncHandlers(data=on_point, source=on_source)
        if not adapter.available():
            raise CoSimError(f"ngspice is not available: {adapter.last_error}")
        if self._stop.is_set():
            raise CoSimError("run stopped")
        try:
            adapter.load(mapped.deck)
            if self._stop.is_set():  # load() clears the adapter's stop flag
                adapter.stop()
            if steps > 1:
                adapter.set_breakpoint(ts)
            adapter.run_ticks(t_end, ts)
        except SimulationError as err:
            if self._error is not None:
                raise self._error from None
            if self._stop.is_set():
                raise CoSimError("run stopped") from None
            raise CoSimError(f"ngspice: {err}") from err
        if self._error is not None:
            raise self._error
        if adapter.handlers.errors:
            raise CoSimError(f"ngspice callback failed: {adapter.handlers.errors[0]}")
        done = self.engine.ticks_done
        if done < steps:
            raise CoSimError(
                f"ngspice ended before tick {done} (t = {done * ts:g} s) of {steps - 1}"
            )
        physical = adapter.results()
        if physical is None:
            raise CoSimError("ngspice returned no results")
        return CoSimRun(
            signal=self.engine.finish(),
            physical=physical,
            mapped=mapped,
            ts=ts,
            t_end=t_end,
            warnings=warnings,
            redo_count=adapter.redo_count,
        )
