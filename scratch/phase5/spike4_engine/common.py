"""S4 spike helpers: DSS contexts and the IEEE feeders in their reference state.

OpenDSS runs in-process here (spike only, never in the app: rule 5). Every context is a
fresh DSS.NewContext() with AllowChangeDir and AllowForms off (v0.4 S2).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES, FIXTURES  # noqa: E402

FEEDERS = ("ieee13", "ieee34", "ieee123")


def new_context() -> Any:
    from dss import DSS

    ctx = DSS.NewContext()
    ctx.AllowChangeDir = False
    ctx.AllowForms = False
    return ctx


def compile_feeder(feeder: str, solve: bool = True) -> Any:
    """A context with ``feeder`` compiled as for its opendss_reference.json
    (AFTER_COMPILE commands), solved once in STATIC control mode when ``solve``."""
    ctx = new_context()
    ctx.Text.Command = f'compile "{FEEDER_FILES[feeder]}"'
    for command in AFTER_COMPILE[feeder]:
        ctx.Text.Command = command
    if solve:
        ctx.ActiveCircuit.Solution.ControlMode = 0
        ctx.ActiveCircuit.Solution.Solve()
    return ctx


def each(iface: Any) -> list[str]:
    """Names of every element of a collection interface (Lines, LineCodes, ...)."""
    out: list[str] = []
    idx = iface.First
    while idx > 0:
        out.append(str(iface.Name).lower())
        idx = iface.Next
    return out


def prop(ctx: Any, element: str, name: str) -> str:
    """``Properties(name).Val`` of ``element`` ("line.650632", "linecode.mtx601")."""
    ckt = ctx.ActiveCircuit
    cls, _, obj = element.partition(".")
    ckt.SetActiveClass(cls)
    ckt.ActiveClass.Name = obj
    return str(ckt.ActiveDSSElement.Properties(name).Val)


def versions() -> dict[str, str]:
    import dss

    ctx = new_context()
    return {
        "dss_python": dss.__version__,
        "engine": str(ctx.Version).splitlines()[0].strip(),
        "python": sys.version.split()[0],
    }


__all__ = [
    "FEEDERS",
    "FEEDER_FILES",
    "FIXTURES",
    "ROOT",
    "compile_feeder",
    "each",
    "new_context",
    "prop",
    "versions",
]
