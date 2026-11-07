"""F3: PhysicalRegistry collects @physical_component classes."""

from __future__ import annotations

import types

import pytest

from core.physical.component import (
    PhysicalComponent,
    Port,
    Resistor,
    physical_component,
)
from core.physical.domains import THERMAL
from core.physical.registry import PhysicalRegistry


def _make(name: str) -> type:
    class Comp(PhysicalComponent):
        ports = (Port("a", THERMAL), Port("b", THERMAL))

        def network(self, ports, params):
            return [Resistor("R", ports.a, ports.b, 1.0)]

    Comp.__name__ = name
    return physical_component(Comp)


def test_add_and_lookup() -> None:
    a, b = _make("ThermalR"), _make("Other")
    reg = PhysicalRegistry([a, b])
    assert reg.names() == ["ThermalR", "Other"]
    assert reg.get("ThermalR") is a
    assert reg.spec("Other") is not None
    assert "Other" in reg and len(reg) == 2
    assert reg.get("missing") is None


def test_duplicates_and_unmarked_classes_are_refused() -> None:
    reg = PhysicalRegistry([_make("X")])
    with pytest.raises(ValueError, match="already exists"):
        reg.add(_make("X"))
    with pytest.raises(ValueError, match="not a @physical_component"):
        reg.add(int)


def test_add_module_takes_only_its_own_classes() -> None:
    module = types.ModuleType("fake_lib")
    own = _make("Own")
    own.__module__ = "fake_lib"
    imported = _make("Imported")  # __module__ is this test module
    module.Own = own
    module.Imported = imported
    module.junk = 3
    reg = PhysicalRegistry()
    added = reg.add_module(module)
    assert [s.name for s in added] == ["Own"]
    assert reg.names() == ["Own"]


def test_registries_are_independent() -> None:
    first = PhysicalRegistry([_make("A")])
    second = PhysicalRegistry()
    assert "A" in first and "A" not in second
