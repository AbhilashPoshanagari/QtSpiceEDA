"""Arithmetic blocks: Gain, Sum, Product."""

from __future__ import annotations

from core.blocks.decorator import block

MATH_COLOR = "#8a8f98"


@block(shape="rectangle", color=MATH_COLOR)
def Gain(u, k=1.0):  # noqa: N802  block names are shown to the user as written
    """k * u."""
    return k * u


@block(shape="circle", color=MATH_COLOR)
def Sum(a, b, signs="++"):  # noqa: N802
    """a and b added or subtracted: ``signs`` is two characters, each '+' or '-'."""
    if len(signs) != 2 or any(s not in "+-" for s in signs):
        raise ValueError(f"signs must be two characters of '+' or '-', not {signs!r}")
    sa = 1.0 if signs[0] == "+" else -1.0
    sb = 1.0 if signs[1] == "+" else -1.0
    return sa * a + sb * b


@block(shape="circle", color=MATH_COLOR)
def Product(a, b):  # noqa: N802
    """a * b."""
    return a * b
