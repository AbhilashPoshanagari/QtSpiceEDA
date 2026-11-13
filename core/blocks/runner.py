"""BlockRunner: runs a block diagram in a separate process. Qt-free.

User block code can hang or crash, so it never runs in the UI process. The runner
starts a ``multiprocessing`` process (spawn context, same on every OS) and passes it
only plain data: the diagram's ``to_dict()``, the user source documents as text, and
the time settings. Functions are never pickled. The child process:

1. loads the built-in blocks;
2. executes each source document and collects its ``@block`` objects;
3. rebuilds the diagram, compiles it and runs it;
4. sends back ``SimulationResult`` or the error message and traceback.

Combined runs (C4). The child also imports the physical component modules named in
``physical_modules`` (default: the built-in library, ``core.physical.library``). If
the compiled diagram has physical networks, the child runs it with the CoSimulator,
which loads its own sync-mode NgspiceAdapter in this child process: ngspice and the
user's block code never run in the UI process. The diagram's time step is the
controller tick Ts. The result is the merged SimulationResult (C5).
Circuit blocks (M2) arrive as ``circuits``, plain ``CircuitDefinition.to_dict()``
data, and the child rebuilds their component classes. User-defined physical
components (M5) arrive as ``components``, their source text by document; the child
checks and runs that code (``core.physical.user.load_components``), so it too never
runs in the UI process.

Console lines (U3). ``RunOutcome.console`` holds what the run printed for the user:
for a combined run, ngspice's console output and the CoSimulator's warnings (ticks
that ran late). It is sent back with the result and with an error alike.

``wait()`` returns a RunOutcome. The timeout covers the whole run, including starting
the process. ``cancel()`` may be called from any thread and stops the process at once
(for a combined run that ends ngspice too: it lives in the child).
"""

from __future__ import annotations

import linecache
import multiprocessing as mp
import threading
import time
import traceback
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from typing import Any

from core.blocks.decorator import block, spec_of
from core.blocks.diagram import Diagram
from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks
from core.blocks.spec import BlockSpec
from core.physical.cosim import CoSimulator
from core.physical.library import physical_registry
from core.physical.user import load_components
from core.sim.result import SimulationResult

DEFAULT_TIMEOUT = 10.0
_POLL = 0.05

OK = "ok"
ERROR = "error"
TIMEOUT = "timeout"
CANCELLED = "cancelled"


@dataclass
class RunOutcome:
    """How a run ended. ``result`` is set only when ``status`` is OK."""

    status: str
    result: SimulationResult | None = None
    message: str = ""
    """One line for the user, e.g. ``AlgebraicLoopError: algebraic loop through ...``."""
    traceback: str = ""
    console: list[str] = field(default_factory=list)
    """ngspice console lines and run warnings, for the Console (combined runs)."""

    @property
    def ok(self) -> bool:
        return self.status == OK


class BlockRunner:
    """One run of a diagram in a child process. Create a new runner for each run."""

    def __init__(
        self,
        diagram: dict[str, Any],
        sources: dict[str, str],
        t_end: float,
        dt: float,
        timeout: float = DEFAULT_TIMEOUT,
        physical_modules: list[str] | None = None,
        circuits: list[dict[str, Any]] | None = None,
        components: dict[str, str] | None = None,
    ) -> None:
        modules = None if physical_modules is None else list(physical_modules)
        circuit_data = [dict(c) for c in circuits or []]
        component_code = dict(components or {})
        self._args = (
            diagram,
            dict(sources),
            float(t_end),
            float(dt),
            modules,
            circuit_data,
            component_code,
        )
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._process: mp.process.BaseProcess | None = None
        self._conn: Connection | None = None
        self._started_at = 0.0
        self._outcome: RunOutcome | None = None

    def start(self) -> None:
        """Start the child process (returns at once)."""
        with self._lock:
            if self._process is not None:
                raise RuntimeError("a BlockRunner runs only once")
            ctx = mp.get_context("spawn")
            parent, child = ctx.Pipe(duplex=False)
            self._process = ctx.Process(target=_child_main, args=(child, *self._args), daemon=True)
            self._started_at = time.monotonic()
            self._process.start()
            child.close()
            self._conn = parent

    def cancel(self) -> None:
        """Stop the run. Safe to call from any thread, before or after it ends."""
        self._cancelled.set()
        with self._lock:
            if self._process is not None and self._process.is_alive():
                self._process.terminate()

    def wait(self) -> RunOutcome:
        """Block until the run ends, times out or is cancelled."""
        if self._outcome is not None:
            return self._outcome
        if self._process is None:
            self.start()
        assert self._process is not None and self._conn is not None
        deadline = self._started_at + self.timeout
        outcome: RunOutcome | None = None
        while outcome is None:
            if self._cancelled.is_set():
                outcome = RunOutcome(CANCELLED, message="run cancelled")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = RunOutcome(
                    TIMEOUT, message=f"run stopped after the {self.timeout:g} s timeout"
                )
                break
            try:
                ready = self._conn.poll(min(_POLL, remaining))
            except (EOFError, OSError):
                ready = True  # the pipe closed: recv() below reports it
            if ready:
                outcome = self._receive()
            elif not self._process.is_alive():
                outcome = RunOutcome(
                    ERROR,
                    message=f"block runner process exited with code {self._process.exitcode}",
                )
            # A cancel kills the process, which closes the pipe: that is not an error.
            if outcome is not None and not outcome.ok and self._cancelled.is_set():
                outcome = RunOutcome(CANCELLED, message="run cancelled")
        self._finish()
        self._outcome = outcome
        return outcome

    def run(self) -> RunOutcome:
        """``start()`` then ``wait()``."""
        self.start()
        return self.wait()

    def _receive(self) -> RunOutcome:
        assert self._conn is not None
        try:
            msg = self._conn.recv()
        except (EOFError, OSError) as err:
            return RunOutcome(ERROR, message=f"lost contact with the block runner: {err}")
        if msg[0] == OK:
            return RunOutcome(OK, result=msg[1], console=list(msg[2]))
        return RunOutcome(ERROR, message=msg[1], traceback=msg[2], console=list(msg[3]))

    def _finish(self) -> None:
        with self._lock:
            proc = self._process
            if proc is not None:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=5)
            if self._conn is not None:
                self._conn.close()


# ---- child process


def load_blocks(sources: dict[str, str]) -> dict[str, object]:
    """Built-in blocks plus the ``@block`` objects defined by each source document.

    Runs the user code; call this only in the runner process.
    """
    blocks = builtin_blocks()
    for index, (document, code) in enumerate(sources.items()):
        filename = f"<block code {document}>"
        # Lets tracebacks show the user's source lines.
        linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
        namespace: dict[str, object] = {"__name__": f"qtspice_user_{index}", "block": block}
        exec(compile(code, filename, "exec"), namespace)  # runs user block code
        for value in namespace.values():
            spec = spec_of(value)
            if spec is not None:
                blocks[spec.name] = value
    return blocks


def run_diagram(
    diagram: dict[str, Any],
    sources: dict[str, str],
    t_end: float,
    dt: float,
    physical_modules: list[str] | None = None,
    console: list[str] | None = None,
    circuits: list[dict[str, Any]] | None = None,
    components: dict[str, str] | None = None,
) -> SimulationResult:
    """Load the blocks, then compile and run the diagram (in the current process).

    A diagram with physical networks is a combined run: ngspice (sync mode) is set
    up in this process, so call it only in the runner process or a fresh one.
    ngspice's console lines and the run's warnings are appended to ``console`` (also
    when the run fails).
    """
    blocks = load_blocks(sources)
    # M5: user component code runs here, in the runner process, never in the UI
    user_classes = load_components(components or {})
    physical = physical_registry(physical_modules, circuits or [], user_classes)
    specs: dict[str, BlockSpec | None] = {name: spec_of(obj) for name, obj in blocks.items()}

    def lookup(name: str) -> BlockSpec | None:
        spec = physical.spec(name)
        return spec.block if spec is not None else specs.get(name)

    engine = ExecutionEngine(blocks, physical)
    engine.compile(Diagram.from_dict(diagram, lookup))
    if engine.networks:
        cosim = CoSimulator(engine, physical)
        try:
            run = cosim.run(t_end, dt)
        finally:
            if console is not None and cosim.adapter is not None:
                console.extend(cosim.adapter.console)
        if console is not None:
            console.extend(f"Warning: {w}" for w in run.warnings)
        return run.result
    return engine.run(t_end, dt)


def _child_main(
    conn: Connection,
    diagram: dict[str, Any],
    sources: dict[str, str],
    t_end: float,
    dt: float,
    physical_modules: list[str] | None,
    circuits: list[dict[str, Any]],
    components: dict[str, str],
) -> None:
    console: list[str] = []
    try:
        result = run_diagram(
            diagram, sources, t_end, dt, physical_modules, console, circuits, components
        )
        conn.send((OK, result, console))
    except BaseException as err:  # everything goes back to the parent
        message = "".join(traceback.format_exception_only(err)).strip()
        conn.send((ERROR, message, traceback.format_exc(), console))
    finally:
        conn.close()
