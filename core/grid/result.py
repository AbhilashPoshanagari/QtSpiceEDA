"""GridResult: the result of one snapshot power flow. Qt-free, picklable.

Conventions (CLAUDE.md "Grid model"):
- node names are "<bus>.<node>" (671.1), lower case;
- node voltages are complex volts, line-to-neutral; pu = abs(V) / line-to-neutral base;
- branch currents are per phase at terminal 1; branch power is positive into
  terminal 1;
- total power is positive when supplied to the grid by the source(s).

The waveform window never reads GridResult (rule 7): it gets the voltage profile
through ``to_simulation_result()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from core.netlist.model import ERROR
from core.physical.component import NetworkDiagnostic
from core.sim.result import SimulationResult

if TYPE_CHECKING:
    from core.grid.analysis import VoltageProfile

PROFILE_AXIS = "distance"
PROFILE_ANALYSIS = "profile"
PROFILE_PHASES = ("a", "b", "c")


@dataclass
class BranchResult:
    """One branch (line, switch or transformer), seen from its terminal 1."""

    element: str
    """Element id, e.g. "line.650632"."""
    bus1: str
    bus2: str
    phases: int
    current: np.ndarray
    """Complex phase currents at terminal 1 in A (length ``phases``)."""
    p_kw: float
    """Active power into terminal 1 (all conductors)."""
    q_kvar: float
    losses_kw: float
    losses_kvar: float
    normal_amps: float
    """Normal current rating in A (OpenDSS NormAmps); 0 when the element has none."""
    loading_pct: float | None
    """Largest phase current / normal rating * 100; None without a rating."""


@dataclass
class GridResult:
    converged: bool
    iterations: int = 0
    control_iterations: int = 0
    node_names: list[str] = field(default_factory=list)
    node_voltage: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=complex))
    """Complex node voltages in V, line-to-neutral, in ``node_names`` order."""
    node_voltage_pu: np.ndarray = field(default_factory=lambda: np.zeros(0))
    """abs(V) / line-to-neutral base; NaN for a bus without a voltage base."""
    branches: dict[str, BranchResult] = field(default_factory=dict)
    total_power_kw: float = 0.0
    """Power supplied to the grid by the source(s) (positive = supplied)."""
    total_power_kvar: float = 0.0
    total_losses_kw: float = 0.0
    total_losses_kvar: float = 0.0
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    """engine (version string), base_frequency (Hz), voltage_bases (kV LL),
    regulator_taps ({regcontrol name: tap of its winding})."""

    @property
    def ok(self) -> bool:
        return self.converged and not any(d.severity == ERROR for d in self.diagnostics)

    def voltage(self, node: str) -> complex:
        """Complex voltage of one node ("671.1"); KeyError if it does not exist."""
        return complex(self.node_voltage[self._index(node)])

    def voltage_pu(self, node: str) -> float:
        return float(self.node_voltage_pu[self._index(node)])

    def _index(self, node: str) -> int:
        try:
            return self.node_names.index(node.lower())
        except ValueError:
            raise KeyError(node) from None

    def to_simulation_result(self, profile: VoltageProfile) -> SimulationResult:
        """The voltage profile for the waveform window (rule 7).

        One row per bus, sorted by distance (km, the axis "distance"), then bus name;
        vectors ``v_pu.a``, ``v_pu.b``, ``v_pu.c`` in pu, NaN where the bus has no
        node of that phase. Make ``profile`` with core.grid.analysis.voltage_profile.
        """
        rows: dict[str, tuple[float, dict[str, float]]] = {}
        for p in profile.points:
            rows.setdefault(p.bus, (p.distance_km, {}))[1][p.phase] = p.v_pu
        order = sorted(rows, key=lambda bus: (rows[bus][0], bus))
        vectors: dict[str, np.ndarray] = {
            PROFILE_AXIS: np.array([rows[b][0] for b in order], dtype=float)
        }
        units = {PROFILE_AXIS: "km"}
        for ph in PROFILE_PHASES:
            name = f"v_pu.{ph}"
            vectors[name] = np.array([rows[b][1].get(ph, np.nan) for b in order], dtype=float)
            units[name] = "pu"
        return SimulationResult(
            axis=PROFILE_AXIS, vectors=vectors, units=units, analysis=PROFILE_ANALYSIS
        )

    @classmethod
    def failed(cls, diagnostics: list[NetworkDiagnostic], **metadata: Any) -> GridResult:
        """A result with no solution, only the diagnostics that explain why."""
        return cls(converged=False, diagnostics=list(diagnostics), metadata=dict(metadata))
