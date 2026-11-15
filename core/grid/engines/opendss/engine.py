"""OpenDSSEngine: snapshot power flow with OpenDSS through DSS-Python. Qt-free.

- ``dss`` is imported lazily (rule 15): without dss-python, ``available()`` is False
  and every call returns an error Diagnostic instead of raising.
- Each engine owns its own DSS context (``DSS.NewContext()``, rule 18), with
  ``AllowChangeDir`` off (OpenDSS would otherwise change the process working
  directory on ``compile``) and ``AllowForms`` off.
- OpenDSS is the .dss parser (rule 16): ``load_native`` compiles the file (absolute
  path, quoted) and never reads DSS text itself.
- OpenDSS errors become Diagnostics, never exceptions (rule 6). A solve that does
  not converge gives ``converged=False`` and an error Diagnostic.
- ``run`` first reads the loaded circuit back as a GridModel and checks it with
  core/grid/validate.py (once per load). Any error: no solve, a failed GridResult with
  the validation Diagnostics. Warnings are kept in the result's Diagnostics.

Meant to run in the GridRunner child process; OpenDSS never runs in the UI process.
"""

from __future__ import annotations

import contextlib
import locale
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from core.grid.config import CONTROL_OFF, PowerFlowConfig
from core.grid.result import BranchResult, GridResult
from core.netlist.model import ERROR, WARNING
from core.physical.component import NetworkDiagnostic

if TYPE_CHECKING:
    from core.grid.model import GridModel

CIRCUIT = "circuit"
"""Diagnostic location for problems of the whole file or circuit."""

INSTALL_HINT = 'dss-python is not installed (pip install -e ".[grid]")'

_BRANCH_CLASSES = ("line", "transformer")
_CONTROL_MODE = {"static": 0, CONTROL_OFF: -1}  # OpenDSS ControlModes: STATIC=0, OFF=-1


def _import_dss() -> Any | None:
    try:
        import dss
    except ImportError:
        return None
    return dss


def _error(message: str, location: str = CIRCUIT) -> NetworkDiagnostic:
    return NetworkDiagnostic(location, ERROR, message)


def _dss_message(err: Exception) -> str:
    """One line from a DSSException ("(#243) Redirect file not found ... [file: ..]")."""
    args = getattr(err, "args", ())
    if len(args) >= 2 and isinstance(args[0], int):
        text, number = str(args[1]), args[0]
    else:
        text, number = str(err), None
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    prefix = f"OpenDSS error {number}" if number is not None else "OpenDSS error"
    return f"{prefix}: {text or type(err).__name__}"


def _complex(values: Any) -> np.ndarray:
    """DSS-Python gives complex arrays as interleaved (re, im) floats."""
    arr = np.asarray(values)
    if np.iscomplexobj(arr):
        return arr.astype(complex)
    arr = arr.astype(float)
    return arr[0::2] + 1j * arr[1::2]


class OpenDSSEngine:
    """One OpenDSS context. Create, ``load_native``, ``run`` (any number of times),
    ``close``. Not thread-safe; use one engine per thread or process."""

    def __init__(self) -> None:
        self._ctx: Any | None = None
        self._loaded = False
        self._path = ""
        self.script = ""
        """The script of the last ``load_model`` call ("" before)."""
        self._validation: list[NetworkDiagnostic] | None = None
        """validate() of the loaded circuit; None until the first run after a load."""

    # ---- GridEngine

    @staticmethod
    def available() -> bool:
        return _import_dss() is not None

    @property
    def context(self) -> Any | None:
        """The engine's own DSS context (None before the first load); for tests."""
        return self._ctx

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load_native(self, path: str | Path) -> list[NetworkDiagnostic]:
        """Compile a .dss file in this engine's context.

        A compile error (unknown file in a redirect, unknown linecode, ...) is
        returned as an error Diagnostic and leaves no circuit loaded.
        """
        self._loaded = False
        self._validation = None
        file = Path(path).expanduser().resolve()
        if not file.is_file():
            return [_error(f"file not found: {file}")]
        ctx, diagnostics = self._context()
        if ctx is None:
            return diagnostics
        # Rule 4 style: the engine formats numbers itself (S5), but this is cheap.
        locale.setlocale(locale.LC_NUMERIC, "C")
        try:
            self._command("clear")
            self._command(f'compile "{file}"')
        except Exception as err:  # DSSException and anything else from the engine
            return [_error(_dss_message(err))]
        try:
            name = ctx.ActiveCircuit.Name
        except Exception as err:
            return [_error(_dss_message(err))]
        if not name:
            return [_error(f"{file.name} defines no circuit (New Circuit.<name>)")]
        self._loaded = True
        self._path = str(file)
        return []

    def load_model(self, model: GridModel) -> list[NetworkDiagnostic]:
        """Write ``model`` as a DSS script (writer.py) and run it in this context.

        The script is run one command at a time, so an engine error names the script
        line and the element it defines. Writer problems come back as Diagnostics;
        any error leaves no circuit
        loaded. The written script is kept in ``script`` for inspection.
        """
        from core.grid.engines.opendss.writer import element_of, script_commands, write

        self._loaded = False
        self._validation = None
        text, diagnostics = write(model)
        self.script = text or ""
        if text is None or any(d.severity == ERROR for d in diagnostics):
            return diagnostics
        ctx, more = self._context()
        if ctx is None:
            return diagnostics + more
        locale.setlocale(locale.LC_NUMERIC, "C")
        element = CIRCUIT
        for number, command in script_commands(text):
            element = element_of(command, CIRCUIT if not command.startswith("~") else element)
            try:
                self._command(command)
            except Exception as err:
                message = f"script line {number}: {_dss_message(err)}"
                return [*diagnostics, _error(message, element)]
        try:
            name = ctx.ActiveCircuit.Name
        except Exception as err:
            return [*diagnostics, _error(_dss_message(err))]
        if not name:
            return [*diagnostics, _error("the written script defines no circuit")]
        self._loaded = True
        self._path = ""
        return diagnostics

    def extract_model(self) -> tuple[GridModel | None, list[NetworkDiagnostic]]:
        """Read the loaded circuit back as a GridModel (extract.py).

        Call it after ``run`` so regulator taps are the solved ones; an unsolved
        circuit gives a warning. Without a circuit, or on an engine error, the model
        is None and the Diagnostics say why.
        """
        if self._ctx is None or not self._loaded:
            return None, [_error("no circuit is loaded")]
        from core.grid.engines.opendss.extract import extract

        try:
            return extract(self._ctx)
        except Exception as err:  # an engine error while reading; never raise
            return None, [_error(_dss_message(err))]

    def run(self, config: PowerFlowConfig | None = None) -> GridResult:
        """Solve the loaded circuit once with ``config`` and read the results."""
        config = config or PowerFlowConfig()
        if self._ctx is None or not self._loaded:
            return GridResult.failed([_error("no circuit is loaded")])
        problems = config.problems()
        if problems:
            return GridResult.failed([_error(f"power-flow settings: {p}") for p in problems])
        checks = self.validation()
        if any(d.severity == ERROR for d in checks):
            return GridResult.failed(checks)
        diagnostics: list[NetworkDiagnostic] = []
        ckt = self._ctx.ActiveCircuit
        sol = ckt.Solution
        try:
            sol.ControlMode = _CONTROL_MODE[config.control_mode]
            if config.max_iterations is not None:
                sol.MaxIterations = config.max_iterations
            if config.max_control_iterations is not None:
                sol.MaxControlIterations = config.max_control_iterations
            if config.tolerance is not None:
                sol.Tolerance = config.tolerance
        except Exception as err:
            return GridResult.failed([_error(_dss_message(err))])
        try:
            sol.Solve()
        except Exception as err:  # e.g. "(#485) Max Control Iterations Exceeded"
            diagnostics.append(_error(_dss_message(err)))
        try:
            result = self._read_result()
        except Exception as err:
            return GridResult.failed([*diagnostics, _error(_dss_message(err))])
        if not result.converged:
            diagnostics.append(
                _error(f"power flow did not converge after {result.iterations} iterations")
            )
        elif diagnostics:
            result.converged = False  # an error during the solve: do not trust it
        result.diagnostics = checks + diagnostics + result.diagnostics
        return result

    def validation(self) -> list[NetworkDiagnostic]:
        """validate() of the loaded circuit, computed once per load.

        The model is read back from the engine, so it is what OpenDSS will solve.
        When the extractor reported unsupported elements the model is incomplete,
        and reachability problems are only warnings (one of them may be a branch).
        Extractor errors count; its other warnings are left out here.
        """
        if self._validation is not None:
            return list(self._validation)
        from core.grid.engines.opendss.extract import UNSUPPORTED_ELEMENT
        from core.grid.validate import validate

        model, found = self.extract_model()
        errors = [d for d in found if d.severity == ERROR]
        if model is None or errors:
            checks = errors or [_error("cannot read the circuit back for validation")]
        else:
            incomplete = any(d.code == UNSUPPORTED_ELEMENT for d in found)
            checks = validate(model, incomplete=incomplete)
        self._validation = checks
        return list(checks)

    def close(self) -> None:
        if self._ctx is not None:
            with contextlib.suppress(Exception):  # closing never raises
                self._command("clear")
        self._ctx = None
        self._loaded = False
        self._validation = None

    # ---- helpers

    def _context(self) -> tuple[Any | None, list[NetworkDiagnostic]]:
        if self._ctx is not None:
            return self._ctx, []
        dss = _import_dss()
        if dss is None:
            return None, [_error(INSTALL_HINT)]
        try:
            ctx = dss.DSS.NewContext()
            ctx.AllowChangeDir = False
            ctx.AllowForms = False
        except Exception as err:
            return None, [_error(f"cannot start OpenDSS: {_dss_message(err)}")]
        self._ctx = ctx
        return ctx, []

    def _command(self, text: str) -> str:
        assert self._ctx is not None
        self._ctx.Text.Command = text
        return str(self._ctx.Text.Result)

    def _read_result(self) -> GridResult:
        assert self._ctx is not None
        ckt = self._ctx.ActiveCircuit
        sol = ckt.Solution
        diagnostics: list[NetworkDiagnostic] = []

        names = [n.lower() for n in ckt.AllNodeNames]
        voltage = _complex(ckt.AllBusVolts)
        bases = self._node_bases_v()
        base = np.array([bases.get(n, 0.0) for n in names], dtype=float)
        no_base = sorted({n.split(".")[0] for n, b in zip(names, base, strict=True) if b <= 0})
        for bus in no_base:
            diagnostics.append(
                NetworkDiagnostic(f"bus.{bus}", WARNING, "bus has no voltage base: no pu value")
            )
        with np.errstate(divide="ignore", invalid="ignore"):
            pu = np.where(base > 0, np.abs(voltage) / np.where(base > 0, base, 1.0), np.nan)

        branches = self._read_branches()
        p_kw, q_kvar = (float(x) for x in ckt.TotalPower)
        loss_w, loss_var = (float(x) for x in ckt.Losses)
        metadata = {
            "engine": str(self._ctx.Version).splitlines()[0].strip(),
            "source_file": self._path,
            "base_frequency": float(self._command("get DefaultBaseFrequency")),
            "voltage_bases": [float(v) for v in ckt.Settings.VoltageBases],
            "regulator_taps": self._regulator_taps(),
        }
        return GridResult(
            converged=bool(sol.Converged),
            iterations=int(sol.Iterations),
            control_iterations=int(sol.ControlIterations),
            node_names=names,
            node_voltage=voltage,
            node_voltage_pu=pu,
            branches=branches,
            # OpenDSS reports power into the circuit's terminals: negative = supplied.
            total_power_kw=-p_kw,
            total_power_kvar=-q_kvar,
            total_losses_kw=loss_w / 1000.0,
            total_losses_kvar=loss_var / 1000.0,
            diagnostics=diagnostics,
            metadata=metadata,
        )

    def _node_bases_v(self) -> dict[str, float]:
        """Line-to-neutral base voltage in V of every node, by node name."""
        assert self._ctx is not None
        ckt = self._ctx.ActiveCircuit
        out: dict[str, float] = {}
        for i in range(int(ckt.NumBuses)):
            ckt.SetActiveBusi(i)
            bus = ckt.ActiveBus
            name = str(bus.Name).lower()
            kv = float(bus.kVBase)  # kVBase of a bus is line-to-neutral
            for node in bus.Nodes:
                out[f"{name}.{int(node)}"] = kv * 1000.0
        return out

    def _read_branches(self) -> dict[str, BranchResult]:
        assert self._ctx is not None
        ckt = self._ctx.ActiveCircuit
        pd = ckt.PDElements
        out: dict[str, BranchResult] = {}
        idx = pd.First
        while idx > 0:
            el = ckt.ActiveCktElement
            element = str(el.Name).lower()
            if element.split(".")[0] in _BRANCH_CLASSES and el.Enabled:
                phases = int(el.NumPhases)
                conductors = int(el.NumConductors)
                current = _complex(el.Currents)[:phases]
                power = _complex(el.Powers)[:conductors]  # kW + j kvar, terminal 1
                losses = _complex(el.Losses)  # W + j var
                buses = [str(b).lower().split(".")[0] for b in el.BusNames]
                normal = float(el.NormalAmps)
                peak = float(np.max(np.abs(current))) if current.size else 0.0
                out[element] = BranchResult(
                    element=element,
                    bus1=buses[0],
                    bus2=buses[1] if len(buses) > 1 else "",
                    phases=phases,
                    current=current,
                    p_kw=float(power.real.sum()),
                    q_kvar=float(power.imag.sum()),
                    losses_kw=float(losses[0].real) / 1000.0,
                    losses_kvar=float(losses[0].imag) / 1000.0,
                    normal_amps=normal,
                    loading_pct=peak / normal * 100.0 if normal > 0 else None,
                )
            idx = pd.Next
        return out

    def _regulator_taps(self) -> dict[str, float]:
        """Tap of the controlled winding of every RegControl, after the solve."""
        assert self._ctx is not None
        ckt = self._ctx.ActiveCircuit
        regs = ckt.RegControls
        names: list[tuple[str, str, int]] = []
        idx = regs.First
        while idx > 0:
            names.append((str(regs.Name).lower(), str(regs.Transformer), int(regs.Winding)))
            idx = regs.Next
        taps: dict[str, float] = {}
        xfs = ckt.Transformers
        for name, transformer, winding in names:
            xfs.Name = transformer
            xfs.Wdg = winding
            taps[name] = float(xfs.Tap)
        return taps
