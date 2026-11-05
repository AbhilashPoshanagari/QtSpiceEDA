"""Stateful blocks: Integrator, UnitDelay.

The engine calls ``output()`` to read a block's outputs for the current step, then
``step(..., dt)`` with this step's inputs to advance its state.
"""

from __future__ import annotations

from core.blocks.decorator import block


@block(stateful=True)
class Integrator:
    """Integral of u (forward Euler), starting at ``initial``."""

    def __init__(self, initial=0.0):
        self.x = initial

    def output(self):
        return self.x

    def step(self, u, dt):
        self.x += u * dt


@block(stateful=True)
class UnitDelay:
    """u delayed by one time step; ``initial`` on the first step."""

    def __init__(self, initial=0.0):
        self.x = initial

    def output(self):
        return self.x

    def step(self, u):
        self.x = u
