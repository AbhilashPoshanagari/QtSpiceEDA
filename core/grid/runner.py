"""GridRunner: one power flow of a .dss file in a child process. Qt-free.

OpenDSS never runs in the UI process (rule 5). The runner uses the same process
helper as BlockRunner (core/blocks/process.py: spawn, one-way Pipe, daemon Process)
and passes only plain data: the absolute .dss path and the PowerFlowConfig. The
child creates its own OpenDSSEngine, runs ``load_native`` and ``run``, reads the
circuit back as a GridModel (after the solve, so regulator taps are the solved ones)
and sends back both (or the load diagnostics). With ``solve=False`` the child only
loads, checks and reads back the circuit: the UI uses it when a file is opened, so
a file with errors never gets as far as a run.

A runner can also solve a GridModel (v0.5 designs, rule 23): the pickled model goes to
the child, which writes it as a DSS script with writer.py (``OpenDSSEngine.load_model``),
saves the script to ``script_path`` when one is given (name_results/grid/<design>/),
solves it in its own engine and sends back the result, the solved model and the
script text. The UI process never calls OpenDSS.

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
from core.grid.model import GridModel
from core.grid.result import GridResult
from core.netlist.model import ERROR as SEVERITY_ERROR
from core.netlist.model import WARNING as SEVERITY_WARNING
from core.physical.component import NetworkDiagnostic

DEFAULT_TIMEOUT = 30.0

_NOT_SOLVED = "circuit is not solved"

__all__ = [
    "CANCELLED",
    "DEFAULT_TIMEOUT",
    "ERROR",
    "OK",
    "TIMEOUT",
    "GridRunOutcome",
    "GridRunner",
    "load_grid",
    "run_grid",
    "run_model",
    "run_power_flow",
]


@dataclass
class GridRunOutcome:
    """How a grid run ended.

    ``status`` is OK when the child finished, even if the file had errors or the
    power flow did not converge: then ``result`` is None (load errors) or has
    ``converged=False``, and ``diagnostics`` says why. ``model`` is the circuit read
    back from the engine (None when it did not load); a load-only run
    (``solve=False``) has a model and no result. ERROR, TIMEOUT and CANCELLED
    are about the process itself; ``message`` is one line for the user.
    """

    status: str
    result: GridResult | None = None
    model: GridModel | None = None
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)
    message: str = ""
    traceback: str = ""
    script: str = ""
    """The DSS script a model run wrote ("" for a .dss file run)."""
    script_path: str = ""
    """Where that script was saved ("" when not saved)."""

    @property
    def ok(self) -> bool:
        """The child finished and the power flow converged."""
        return self.status == OK and self.result is not None and self.result.converged


class GridRunner:
    """One run of a .dss file or a GridModel in a child process. Create a new runner
    for each run.

    ``source`` is a .dss path or a GridModel. A model is always solved (``solve`` must
    be True); ``script_path`` is where the child saves the script it writes for it.
    """

    def __init__(
        self,
        source: str | Path | GridModel,
        config: PowerFlowConfig | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        solve: bool = True,
        script_path: str | Path | None = None,
    ) -> None:
        config = config or PowerFlowConfig()
        self.solve = solve
        self.from_model = isinstance(source, GridModel)
        if self.from_model:
            if not solve:
                raise ValueError("a GridModel run always solves")
            script = "" if script_path is None else str(Path(script_path).expanduser().resolve())
            self._child = ChildProcess(
                _child_model_main, (source, config, script), timeout, name="grid runner"
            )
        else:
            assert not isinstance(source, GridModel)
            path = str(Path(source).expanduser().resolve())
            self._child = ChildProcess(
                _child_main, (path, config, solve), timeout, name="grid runner"
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
                result, model, diagnostics, script, script_path = payload
                outcome = GridRunOutcome(
                    OK,
                    result=result,
                    model=model,
                    diagnostics=list(diagnostics),
                    script=script,
                    script_path=script_path,
                )
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
    """Load and solve in the current process (also for tests).

    Returns (result, diagnostics): result is None when the file did not load.
    """
    result, _, diagnostics = run_grid(dss_path, config)
    return result, diagnostics


def run_grid(
    dss_path: str | Path, config: PowerFlowConfig
) -> tuple[GridResult | None, GridModel | None, list[NetworkDiagnostic]]:
    """Load, solve and read back in the current process (the child's work).

    Returns (result, model, diagnostics). result and model are None when the file
    did not load. The extractor's warnings (unsupported elements, ...) are added to
    the diagnostics; its errors are already in the result's (engine validation).
    """
    engine = OpenDSSEngine()
    try:
        diagnostics = engine.load_native(dss_path)
        if any(d.severity == SEVERITY_ERROR for d in diagnostics):
            return None, None, diagnostics
        result = engine.run(config)
        model, found = engine.extract_model()
        extra = [d for d in found if d.severity != SEVERITY_ERROR]
        return result, model, diagnostics + result.diagnostics + extra
    finally:
        engine.close()


def load_grid(dss_path: str | Path) -> tuple[GridModel | None, list[NetworkDiagnostic]]:
    """Load, check (engine validation) and read back without solving.

    Returns (model, diagnostics); model is None when the file did not load or the
    circuit cannot be read back. A model with error diagnostics must not be run.
    """
    engine = OpenDSSEngine()
    try:
        diagnostics = engine.load_native(dss_path)
        if any(d.severity == SEVERITY_ERROR for d in diagnostics):
            return None, diagnostics
        checks = engine.validation()
        model, found = engine.extract_model()
        # Not solved yet is the point of a load-only run: drop the extractor's
        # "circuit is not solved" warning; validation already has its errors.
        seen = {(d.block, d.message) for d in checks}
        extra = [
            d
            for d in found
            if (d.block, d.message) not in seen and not d.message.startswith(_NOT_SOLVED)
        ]
        return model, diagnostics + checks + extra
    finally:
        engine.close()


def run_model(
    model: GridModel, config: PowerFlowConfig, script_path: str | Path | None = None
) -> tuple[GridResult | None, GridModel | None, list[NetworkDiagnostic], str, str]:
    """Write ``model`` as a DSS script, solve it and read it back in the current
    process (the child's work for a design).

    Returns (result, model read back after the solve, diagnostics, script, saved
    path). result and model are None when the model could not be written or the
    engine refused the script (the diagnostics say why). The script is saved to
    ``script_path`` whenever one was written; a failed save is a warning.
    """
    engine = OpenDSSEngine()
    try:
        diagnostics = engine.load_model(model)
        script = engine.script
        saved = ""
        if script and script_path:
            target = Path(script_path)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(script, encoding="utf-8", newline="\n")
                saved = str(target)
            except OSError as err:
                diagnostics.append(
                    NetworkDiagnostic("circuit", SEVERITY_WARNING, f"script not saved: {err}")
                )
        if not engine.loaded or any(d.severity == SEVERITY_ERROR for d in diagnostics):
            return None, None, diagnostics, script, saved
        result = engine.run(config)
        back, found = engine.extract_model()
        extra = [d for d in found if d.severity != SEVERITY_ERROR]
        return result, back, diagnostics + result.diagnostics + extra, script, saved
    finally:
        engine.close()


# ---- child process


def _child_main(conn: Connection, dss_path: str, config: PowerFlowConfig, solve: bool) -> None:
    try:
        if solve:
            result, model, diagnostics = run_grid(dss_path, config)
        else:
            result = None
            model, diagnostics = load_grid(dss_path)
        conn.send((OK, result, model, diagnostics, "", ""))
    except BaseException as err:  # everything goes back to the parent
        _send_error(conn, err)
    finally:
        conn.close()


def _child_model_main(
    conn: Connection, model: GridModel, config: PowerFlowConfig, script_path: str
) -> None:
    try:
        conn.send((OK, *run_model(model, config, script_path or None)))
    except BaseException as err:
        _send_error(conn, err)
    finally:
        conn.close()


def _send_error(conn: Connection, err: BaseException) -> None:
    message = "".join(traceback.format_exception_only(err)).strip()
    conn.send((ERROR, message, traceback.format_exc()))
