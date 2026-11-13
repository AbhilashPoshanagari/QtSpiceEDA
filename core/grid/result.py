"""GridResult: the result of one snapshot power flow. Qt-free, picklable.

Conventions (CLAUDE.md "Grid model"):
- node names are "<bus>.<node>" (671.1), lower case;
- node voltages are complex volts, line-to-neutral; pu = abs(V) / line-to-neutral base;
- branch currents are per phase at terminal 1; branch power is positive into
  terminal 1;
- total power is positive when supplied to the grid by the source(s).

The waveform window never reads GridResult (rule 7): ``to_simulation_result()``
comes in G5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.netlist.model import ERROR
from core.physical.component import NetworkDiagnostic


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

    @classmethod
    def failed(cls, diagnostics: list[NetworkDiagnostic], **metadata: Any) -> GridResult:
        """A result with no solution, only the diagnostics that explain why."""
        return cls(converged=False, diagnostics=list(diagnostics), metadata=dict(metadata))
