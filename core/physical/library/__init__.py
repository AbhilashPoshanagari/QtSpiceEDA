"""Built-in physical components (B1-B5, composite parts M1). Qt-free.

``BUILTIN_MODULES`` names the modules whose ``@physical_component`` classes make up
the built-in library; the block runner imports them by name in its child process
(module names are plain data, classes are never pickled).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from typing import Any

from core.physical.registry import PhysicalRegistry

BUILTIN_MODULES: tuple[str, ...] = (
    "core.physical.library.electrical.components",
    "core.physical.library.rotational.components",
    "core.physical.library.translational.components",
    "core.physical.library.thermal.components",
    "core.physical.library.converters.sources",
    "core.physical.library.converters.sensors",
    "core.physical.library.converters.solver",
    "core.physical.library.converters.pwm",
    "core.physical.library.composite.motor",
    "core.physical.library.composite.ratios",
    "core.physical.library.composite.battery",
    "core.physical.library.composite.bridges",
    "core.physical.library.composite.thermal_parts",
)
"""The built-in library, in the order its groups are shown."""


def physical_registry(
    modules: Iterable[str] | None = None,
    circuits: Iterable[Mapping[str, Any]] = (),
    components: Iterable[type] = (),
) -> PhysicalRegistry:
    """A registry of the components of ``modules`` (default: the built-in library)
    plus the Circuit blocks (M2) of ``circuits`` (``CircuitDefinition.to_dict()``)
    plus user component classes (M5, ``core.physical.user.load_components``).

    Raises ImportError for a module that cannot be imported and ValueError for two
    components with the same name or a circuit block that cannot be rebuilt.
    """
    from core.physical.circuit_block import load_definition

    registry = PhysicalRegistry()
    for name in BUILTIN_MODULES if modules is None else modules:
        registry.add_module(importlib.import_module(name))
    for data in circuits:
        block = load_definition(data)
        if not block.ok or block.component is None:
            raise ValueError("; ".join(d.message for d in block.diagnostics))
        registry.add(block.component)
    for cls in components:
        registry.add(cls)
    return registry
