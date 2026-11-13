"""B5: the Solver Configuration block.

Every physical network needs exactly one (F5 checks this). Its port joins a network
of any domain. Its settings are read by name by the mapper and the CoSimulator
(``core.physical.mapper.SOLVER_SETTINGS``):

- ``Ts``: the controller tick. It must equal the diagram's time step: a combined run
  with a different time step is refused with a message.
- ``reltol`` / ``abstol``: ngspice's ``.options`` (the strictest of all networks).
- ``max_step``: ngspice's largest internal step; 0 means Ts. It never exceeds Ts.
- ``steady_state`` (M6): False (default) starts the run from the start values of the
  storage elements (inertia ``w0``, mass ``v0``, thermal mass ``T0``, capacitor
  ``v0``, inductor ``i0``, spring ``tau0`` / ``F0``), passed to ngspice as ``ic=``
  with ``uic``. True runs an operating point first: the run starts in steady state
  with the sources at their initial values, and the start values are ignored.
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
    abstol, the largest internal step (0 = Ts), and whether the run starts from
    steady state (an operating point) instead of the start values. Wire it to any node
    of the network."""

    role = SOLVER
    ports = (Port("port", ANY_DOMAIN, "network"),)
    parameters = (
        Parameter("Ts", 1e-3, "s", "controller tick (= diagram time step)"),
        Parameter("reltol", 1e-3, "", "relative tolerance"),
        Parameter("abstol", 1e-12, "", "absolute tolerance (through quantity)"),
        Parameter("max_step", 0.0, "s", "largest internal step (0 = Ts)"),
        Parameter("steady_state", False, "", "start from steady state (operating point)"),
    )

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        ts = positive(params, "Ts")
        if ts < MIN_TS * (1 - 1e-9):
            raise ValueError(f"Ts = {ts:g} s is below the smallest supported {MIN_TS:g} s")
        positive(params, "reltol")
        positive(params, "abstol")
        if real(params, "max_step") < 0:
            raise ValueError("max_step must not be negative (0 means Ts)")
        if not isinstance(params["steady_state"], bool):
            raise ValueError("steady_state must be true or false")
        return []
