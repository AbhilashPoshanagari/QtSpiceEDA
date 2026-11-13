"""Shared helpers for the v0.4 spikes (scratch only; nothing in app/, bridge/ or core/
imports this).

Every engine is its own DSS context (DSS.NewContext(), CLAUDE.md rule 18) with
AllowChangeDir switched off: by default OpenDSS `compile` changes the *process*
working directory to the folder of the compiled file (found in S2).
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "grid"


@dataclass(frozen=True)
class Case:
    """How one IEEE feeder is loaded: the file to compile, commands sent after the
    compile (taken from the feeder's own Run_*.dss script), and the bus coordinates."""

    name: str
    master: Path
    after_compile: tuple[str, ...] = ()
    buscoords: Path | None = None
    solve: bool = True


CASES: dict[str, Case] = {
    # IEEE13Nodeckt.dss solves itself and loads its own BusCoords.
    "ieee13": Case("ieee13", FIXTURES / "ieee13" / "IEEE13Nodeckt.dss", solve=False),
    # First script of Run_IEEE34Mod1.dss, without the meter, show and plot commands.
    "ieee34": Case(
        "ieee34",
        FIXTURES / "ieee34" / "ieee34Mod1.dss",
        buscoords=FIXTURES / "ieee34" / "IEEE34_BusXY.csv",
    ),
    # First script of Run_IEEE123Bus.DSS (one tap per control iteration, staggered
    # delays, 30 control iterations), without the meter, show and plot commands.
    "ieee123": Case(
        "ieee123",
        FIXTURES / "ieee123" / "IEEE123Master.dss",
        after_compile=(
            "RegControl.creg1a.maxtapchange=1 Delay=15",
            "RegControl.creg2a.maxtapchange=1 Delay=30",
            "RegControl.creg3a.maxtapchange=1 Delay=30",
            "RegControl.creg4a.maxtapchange=1 Delay=30",
            "RegControl.creg3c.maxtapchange=1 Delay=30",
            "RegControl.creg4b.maxtapchange=1 Delay=30",
            "RegControl.creg4c.maxtapchange=1 Delay=30",
            "Set MaxControlIter=30",
        ),
        buscoords=FIXTURES / "ieee123" / "BusCoords.dat",
    ),
}


def dss_available() -> bool:
    try:
        import dss  # noqa: F401
    except ImportError:
        return False
    return True


def new_context(allow_change_dir: bool = False) -> Any:
    """A fresh, independent DSS engine (never the global DSS instance)."""
    from dss import DSS

    ctx = DSS.NewContext()
    ctx.AllowChangeDir = allow_change_dir
    ctx.AllowForms = False
    return ctx


def versions() -> dict[str, str]:
    import dss

    ctx = new_context()
    return {
        "dss_python": dss.__version__,
        "engine": ctx.Version.splitlines()[0],
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def command(ctx: Any, text: str) -> str:
    """Send one DSS command and return its result (DSS-Python raises DSSException on errors)."""
    ctx.Text.Command = text
    return ctx.Text.Result


@dataclass
class Solved:
    """What one compile + snapshot solve gave. Picklable (sent through Pipes)."""

    feeder: str
    converged: bool
    iterations: int
    control_iterations: int
    total_power_kw: float  # as OpenDSS reports it: negative = supplied by the source
    total_power_kvar: float
    losses_kw: float
    losses_kvar: float
    voltage_bases: list[float]
    base_frequency: float
    node_names: list[str]
    node_v: list[tuple[float, float]]  # complex V (re, im), line-to-neutral, volts
    node_pu: list[float]
    taps: dict[str, float]  # regcontrol name -> tap of its controlled winding
    compile_s: float = 0.0
    solve_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def pu(self, node: str) -> float:
        return self.node_pu[self.node_names.index(node)]


def compile_case(ctx: Any, case: Case) -> float:
    """Compile the feeder (plus its after-compile commands); return wall seconds."""
    t0 = time.perf_counter()
    command(ctx, f'compile "{case.master}"')
    for cmd in case.after_compile:
        command(ctx, cmd)
    if case.buscoords is not None:
        command(ctx, f'buscoords "{case.buscoords}"')
    return time.perf_counter() - t0


def solve_case(ctx: Any, case: Case) -> float:
    t0 = time.perf_counter()
    if case.solve:
        ctx.ActiveCircuit.Solution.Solve()
    return time.perf_counter() - t0


def read_solution(ctx: Any, feeder: str) -> Solved:
    ckt = ctx.ActiveCircuit
    sol = ckt.Solution
    volts = list(ckt.AllBusVolts)
    names = [n.lower() for n in ckt.AllNodeNames]
    p, q = ckt.TotalPower
    loss_w, loss_var = ckt.Losses
    taps: dict[str, float] = {}
    regs = ckt.RegControls
    idx = regs.First
    while idx > 0:
        name, xf, wdg = regs.Name.lower(), regs.Transformer, regs.Winding
        ckt.Transformers.Name = xf
        ckt.Transformers.Wdg = wdg
        taps[name] = float(ckt.Transformers.Tap)
        ckt.RegControls.Name = name  # Transformers.Name moved the active element
        idx = regs.Next
    command(ctx, "get DefaultBaseFrequency")
    return Solved(
        feeder=feeder,
        converged=bool(sol.Converged),
        iterations=int(sol.Iterations),
        control_iterations=int(sol.ControlIterations),
        total_power_kw=float(p),
        total_power_kvar=float(q),
        losses_kw=float(loss_w) / 1000.0,
        losses_kvar=float(loss_var) / 1000.0,
        voltage_bases=[float(v) for v in ckt.Settings.VoltageBases],
        base_frequency=float(ctx.Text.Result),
        node_names=names,
        node_v=[(float(volts[2 * i]), float(volts[2 * i + 1])) for i in range(len(names))],
        node_pu=[float(v) for v in ckt.AllBusVmagPu],
        taps=taps,
    )


def load_and_solve(feeder: str, ctx: Any | None = None) -> Solved:
    """Compile and solve one IEEE feeder in `ctx` (a new context if None)."""
    case = CASES[feeder]
    ctx = new_context() if ctx is None else ctx
    t_compile = compile_case(ctx, case)
    t_solve = solve_case(ctx, case)
    result = read_solution(ctx, feeder)
    result.compile_s, result.solve_s = t_compile, t_solve
    return result


# ---- reference files (tests/fixtures/grid/<feeder>/opendss_reference.json)


def reference_path(feeder: str) -> Path:
    return FIXTURES / feeder / "opendss_reference.json"


def load_reference(feeder: str) -> dict[str, Any]:
    return json.loads(reference_path(feeder).read_text(encoding="utf-8"))


def as_reference(result: Solved) -> dict[str, Any]:
    data = asdict(result)
    for key in ("compile_s", "solve_s", "extra"):
        data.pop(key)
    data["node_v"] = [list(v) for v in result.node_v]
    return data


def compare_to_reference(result: Solved, ref: dict[str, Any]) -> float:
    """Largest |pu - pu_ref| over all nodes; raises if the node lists differ."""
    if result.node_names != ref["node_names"]:
        raise AssertionError(f"{result.feeder}: node list differs from the reference")
    return max(abs(a - b) for a, b in zip(result.node_pu, ref["node_pu"], strict=True))
