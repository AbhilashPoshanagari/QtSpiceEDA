"""The built-in blocks."""

from __future__ import annotations

from core.blocks.decorator import spec_of
from core.blocks.library import arithmetic, sinks, sources, stateful
from core.blocks.spec import BlockSpec

_MODULES = (sources, arithmetic, stateful, sinks)


def builtin_blocks() -> dict[str, object]:
    """Built-in block functions and classes by name, in library order."""
    blocks: dict[str, object] = {}
    for module in _MODULES:
        for value in vars(module).values():
            spec = spec_of(value)
            if spec is not None and getattr(value, "__module__", None) == module.__name__:
                blocks[spec.name] = value
    return blocks


def builtin_specs() -> list[BlockSpec]:
    """Specs of the built-in blocks, in library order."""
    specs = [spec_of(obj) for obj in builtin_blocks().values()]
    return [s for s in specs if s is not None]
