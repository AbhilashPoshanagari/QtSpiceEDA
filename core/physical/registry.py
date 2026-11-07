"""PhysicalRegistry: the physical components that can be placed. Qt-free.

``@physical_component`` only marks a class; a registry instance collects marked
classes, so there is no module-level mutable state.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import ModuleType

from core.physical.component import PhysicalSpec, physical_spec_of


class PhysicalRegistry:
    """Physical component classes by name, in the order added."""

    def __init__(self, classes: Iterable[type] = ()) -> None:
        self._classes: dict[str, type] = {}
        self._specs: dict[str, PhysicalSpec] = {}
        for cls in classes:
            self.add(cls)

    def add(self, cls: type) -> PhysicalSpec:
        """Register a ``@physical_component`` class. Raises ValueError on a clash."""
        spec = physical_spec_of(cls)
        if spec is None:
            raise ValueError(f"{cls!r} is not a @physical_component class")
        if spec.name in self._specs:
            raise ValueError(f"a physical component named {spec.name!r} already exists")
        self._classes[spec.name] = cls
        self._specs[spec.name] = spec
        return spec

    def add_module(self, module: ModuleType) -> list[PhysicalSpec]:
        """Register every component class defined in ``module`` (not imported ones)."""
        added = []
        for value in vars(module).values():
            if (
                physical_spec_of(value) is not None
                and getattr(value, "__module__", None) == module.__name__
            ):
                added.append(self.add(value))
        return added

    def get(self, name: str) -> type | None:
        return self._classes.get(name)

    def spec(self, name: str) -> PhysicalSpec | None:
        return self._specs.get(name)

    def specs(self) -> list[PhysicalSpec]:
        return list(self._specs.values())

    def names(self) -> list[str]:
        return list(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)
