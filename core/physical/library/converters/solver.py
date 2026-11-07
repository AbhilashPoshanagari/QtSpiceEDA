"""B5: the Solver Configuration block.

Every physical network needs exactly one (F5 checks this). Its port joins a network
of any domain. Its settings are read by name by the mapper and the CoSimulator
(``core.physical.mapper.SOLVER_SETTINGS``):

- ``Ts``: the controller tick. It must equal the diagram's time step: a combined run
  with a different time step is refused with a message.
- ``reltol`` / ``abstol``: ngspice's ``.options`` (the strictest of all networks).
- ``max_step``: ngspice's largest internal step; 0 means Ts. It never exceeds Ts.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.physical.component import (
    SOLVER,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    physical_component,
)
from core.physical.domains import ANY_DOMAIN
from core.physical.library._helpers import positive, real

MIN_TS = 10e-6
"""Smallest controller tick in v0.3 (the same limit as the CoSimulator's)."""


@physical_component
class SolverConfiguration(PhysicalComponent):
    """Solver settings of a physical network: controller tick Ts, ngspice reltol and
    abstol, and the largest internal step (0 = Ts). Wire it to any node of the
    network."""

    role = SOLVER
    ports = (Port("port", ANY_DOMAIN, "network"),)
    parameters = (
        Parameter("Ts", 1e-3, "s", "controller tick (= diagram time step)"),
        Parameter("reltol", 1e-3, "", "relative tolerance"),
        Parameter("abstol", 1e-12, "", "absolute tolerance (through quantity)"),
        Parameter("max_step", 0.0, "s", "largest internal step (0 = Ts)"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        ts = positive(params, "Ts")
        if ts < MIN_TS * (1 - 1e-9):
            raise ValueError(f"Ts = {ts:g} s is below the smallest supported {MIN_TS:g} s")
        positive(params, "reltol")
        positive(params, "abstol")
        if real(params, "max_step") < 0:
            raise ValueError("max_step must not be negative (0 means Ts)")
        return []
