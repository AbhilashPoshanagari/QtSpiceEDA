"""Shared by the grid tests: fixture paths, reference files and a direct OpenDSS solve.

``solve_directly`` drives a DSS context by hand, outside OpenDSSEngine, with the same
steps the engine takes (compile, ControlMode=STATIC, one Solve), so the engine's
result can be compared with what OpenDSS itself reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "grid"
IEEE13 = FIXTURES / "ieee13" / "IEEE13Nodeckt.dss"
IEEE34 = FIXTURES / "ieee34" / "ieee34Mod1.dss"
IEEE123 = FIXTURES / "ieee123" / "IEEE123Master.dss"
BAD_REDIRECT = FIXTURES / "bad" / "misspelled_redirect.dss"
UNKNOWN_LINECODE = FIXTURES / "bad" / "unknown_linecode.dss"


def reference(feeder: str) -> dict[str, Any]:
    path = FIXTURES / feeder / "opendss_reference.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Direct:
    node_names: list[str]
    node_voltage: np.ndarray
    node_pu: np.ndarray
    losses_kw: float
    losses_kvar: float
    total_kw: float  # OpenDSS sign: negative = supplied by the source
    total_kvar: float
    branch_powers: dict[str, np.ndarray]  # element -> CktElement.Powers (kW, kvar pairs)


def solve_directly(path: Path) -> Direct:
    from dss import DSS

    ctx = DSS.NewContext()
    ctx.AllowChangeDir = False
    ctx.AllowForms = False
    ctx.Text.Command = f'compile "{path}"'
    ckt = ctx.ActiveCircuit
    ckt.Solution.ControlMode = 0  # STATIC
    ckt.Solution.Solve()
    volts = np.asarray(ckt.AllBusVolts, dtype=float)
    powers: dict[str, np.ndarray] = {}
    idx = ckt.PDElements.First
    while idx > 0:
        el = ckt.ActiveCktElement
        powers[el.Name.lower()] = np.asarray(el.Powers, dtype=float)
        idx = ckt.PDElements.Next
    p, q = ckt.TotalPower
    loss_w, loss_var = ckt.Losses
    return Direct(
        node_names=[n.lower() for n in ckt.AllNodeNames],
        node_voltage=volts[0::2] + 1j * volts[1::2],
        node_pu=np.asarray(ckt.AllBusVmagPu, dtype=float),
        losses_kw=float(loss_w) / 1000.0,
        losses_kvar=float(loss_var) / 1000.0,
        total_kw=float(p),
        total_kvar=float(q),
        branch_powers=powers,
    )
