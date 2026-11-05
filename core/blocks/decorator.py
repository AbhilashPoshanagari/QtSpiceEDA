"""The ``@block`` decorator. Qt-free.

    @block(shape="circle", outputs=["sum", "diff"])
    def add_sub(a, b):
        return a + b, a - b

    @block(stateful=True)
    class Integrator:
        def __init__(self, initial=0.0):
            self.x = initial
        def output(self):
            return self.x
        def step(self, u, dt):
            self.x += u * dt

The decorated object is returned unchanged, with its spec attached as
``__block_spec__``. An invalid definition raises BlockSpecError at decoration time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from core.blocks.spec import BlockSpec, check_options, spec_from_callable

T = TypeVar("T")

SPEC_ATTR = "__block_spec__"


def block(
    obj: Any = None,
    *,
    shape: str = "square",
    outputs: Sequence[str] | None = None,
    stateful: bool = False,
    color: str | None = None,
) -> Any:
    """Mark a function (or, with ``stateful=True``, a class) as a block.

    Works both bare (``@block``) and with arguments (``@block(shape="circle")``).
    """
    options = check_options(shape, outputs, stateful, color)

    def mark(target: T) -> T:
        spec = spec_from_callable(target, options)
        setattr(target, SPEC_ATTR, spec)
        return target

    if obj is not None:
        return mark(obj)
    return mark


def spec_of(obj: object) -> BlockSpec | None:
    """The spec attached by ``@block``, or None if ``obj`` is not a block."""
    spec = getattr(obj, SPEC_ATTR, None)
    return spec if isinstance(spec, BlockSpec) else None
