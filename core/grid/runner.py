"""GridRunner: one power flow of a .dss file in a child process. Qt-free.

OpenDSS never runs in the UI process (rule 5). The runner uses the same process
helper as BlockRunner (core/blocks/process.py: spawn, one-way Pipe, daemon Process)
and passes only plain data: the absolute .dss path and the PowerFlowConfig. The
child creates its own OpenDSSEngine, runs ``load_native`` and ``run``, and sends
back the GridResult (or the load diagnostics).

``wait()`` returns a GridRunOutcome; the timeout covers the whole run, including the
child's start-up (about 0.5 s, mostly ``import dss``). ``cancel()`` is the Stop: it
may be called from any thread and terminates the child at once.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from pathlib import Path

from core.blocks.process import CANCELLED, ERROR, OK, TIMEOUT, ChildProcess
from core.grid.config import PowerFlowConfig
from core.grid.engines.opendss.engine import OpenDSSEngine
from core.grid.result import GridResult
from core.netlist.model import ERROR as SEVERITY_ERROR
from core.physical.component import NetworkDiagnostic

DEFAULT_TIMEOUT = 30.0

__all__ = [
    "CANCELLED",
    "DEFAULT_TIMEOUT",
    "ERROR",
    "OK",
    "TIMEOUT",
    "GridRunOutcome",
    "GridRunner",
    "run_power_flow",
]


@dataclass
class GridRunOutcome:
    """How a grid run ended.

    ``status`` is OK when the child finished, even if the file had errors or the
    power flow did not converge: then ``result`` is None (load errors) or has
    ``converged=False``, and ``diagnostics`` says why. ERROR, TIMEOUT and CANCELLED
    are about the process itself; ``message`` is one line for the user.
    """

    status: str
    result: GridResult | None = None
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)
    message: str = ""
    traceback: str = ""

    @property
    def ok(self) -> bool:
        """The child finished and the power flow converged."""
        return self.status == OK and self.result is not None and self.result.converged


class GridRunner:
    """One run of a .dss file in a child process. Create a new runner for each run."""

    def __init__(
        self,
        dss_path: str | Path,
        config: PowerFlowConfig | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        path = str(Path(dss_path).expanduser().resolve())
        self._child = ChildProcess(
            _child_main, (path, config or PowerFlowConfig()), timeout, name="grid runner"
        )
        self._outcome: GridRunOutcome | None = None

    @property
    def timeout(self) -> float:
        return self._child.timeout

    def start(self) -> None:
        """Start the child process (returns at once)."""
        self._child.start()

    def cancel(self) -> None:
        """Stop the run. Safe to call from any thread, before or after it ends."""
        self._child.cancel()

    def wait(self) -> GridRunOutcome:
        """Block until the run ends, times out or is cancelled."""
        if self._outcome is not None:
            return self._outcome
        child = self._child.wait()
        if not child.ok:
            outcome = GridRunOutcome(child.status, message=child.error)
        else:
            kind, *payload = child.message
            if kind == OK:
                result, diagnostics = payload
                outcome = GridRunOutcome(OK, result=result, diagnostics=list(diagnostics))
            else:
                message, trace = payload
                outcome = GridRunOutcome(ERROR, message=message, traceback=trace)
        self._outcome = outcome
        return outcome

    def run(self) -> GridRunOutcome:
        """``start()`` then ``wait()``."""
        self.start()
        return self.wait()


def run_power_flow(
    dss_path: str | Path, config: PowerFlowConfig
) -> tuple[GridResult | None, list[NetworkDiagnostic]]:
    """Load and solve in the current process (the child's work; also for tests).

    Returns (result, diagnostics): result is None when the file did not load.
    """
    engine = OpenDSSEngine()
    try:
        diagnostics = engine.load_native(dss_path)
        if any(d.severity == SEVERITY_ERROR for d in diagnostics):
            return None, diagnostics
        result = engine.run(config)
        return result, diagnostics + result.diagnostics
    finally:
        engine.close()


# ---- child process


def _child_main(conn: Connection, dss_path: str, config: PowerFlowConfig) -> None:
    try:
        result, diagnostics = run_power_flow(dss_path, config)
        conn.send((OK, result, diagnostics))
    except BaseException as err:  # everything goes back to the parent
        message = "".join(traceback.format_exception_only(err)).strip()
        conn.send((ERROR, message, traceback.format_exc()))
    finally:
        conn.close()
