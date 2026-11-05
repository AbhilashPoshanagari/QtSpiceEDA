"""Source blocks: Constant, Step, Sine."""

from __future__ import annotations

import math

from core.blocks.decorator import block

SOURCE_COLOR = "#3fa76a"


@block(color=SOURCE_COLOR)
def Constant(value=1.0):  # noqa: N802  block names are shown to the user as written
    """A constant value."""
    return value


@block(color=SOURCE_COLOR)
def Step(t, step_time=1.0, initial=0.0, final=1.0):  # noqa: N802
    """``initial`` before ``step_time``, ``final`` from then on."""
    return final if t >= step_time else initial


@block(color=SOURCE_COLOR)
def Sine(t, amplitude=1.0, frequency=1.0, phase=0.0, offset=0.0):  # noqa: N802
    """offset + amplitude * sin(2*pi*frequency*t + phase), phase in radians."""
    return offset + amplitude * math.sin(2.0 * math.pi * frequency * t + phase)
