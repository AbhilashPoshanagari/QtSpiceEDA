"""B1-B5: the built-in library as a whole (no ngspice needed)."""

from __future__ import annotations

import importlib

import pytest

from core.netlist.model import ERROR
from core.physical.component import (
    REFERENCE,
    Port,
    build_network,
    physical_spec_of,
)
from core.physical.domains import DOMAINS
from core.physical.library import BUILTIN_MODULES, physical_registry

REGISTRY = physical_registry()
NAMES = REGISTRY.names()


def test_every_module_imports_and_adds_components() -> None:
    for name in BUILTIN_MODULES:
        module = importlib.import_module(name)
        assert any(physical_spec_of(v) is not None for v in vars(module).values()), name


@pytest.mark.parametrize("name", NAMES)
def test_every_component_builds_with_its_defaults(name: str) -> None:
    cls = REGISTRY.get(name)
    result = build_network(cls, block="X1")  # type: ignore[arg-type]
    assert result.ok, result.diagnostics
    assert result.diagnostics == []


@pytest.mark.parametrize("name", NAMES)
def test_every_component_has_a_doc_and_known_domains(name: str) -> None:
    spec = REGISTRY.spec(name)
    assert spec is not None
    assert spec.doc, f"{name} needs a docstring (shown in the library)"
    for port in spec.ports:
        if isinstance(port, Port) and spec.role != "solver":
            assert port.domain in DOMAINS


@pytest.mark.parametrize("name", NAMES)
def test_bad_parameter_values_are_diagnostics(name: str) -> None:
    spec = REGISTRY.spec(name)
    assert spec is not None
    for param in spec.parameters:
        if isinstance(param.default, bool) or not isinstance(param.default, int | float):
            continue
        result = build_network(REGISTRY.get(name), {param.name: "oops"}, "X1")  # type: ignore[arg-type]
        assert not result.ok, f"{name}.{param.name} accepted a string"
        assert all(d.block == "X1" for d in result.diagnostics)


def test_every_domain_has_one_reference() -> None:
    refs = {}
    for spec in REGISTRY.specs():
        if spec.role == REFERENCE:
            (port,) = [p for p in spec.ports if isinstance(p, Port)]
            refs.setdefault(port.domain, []).append(spec.name)
    assert all(len(v) == 1 for v in refs.values()), refs


def test_unknown_parameter_is_a_diagnostic() -> None:
    result = build_network(REGISTRY.get(NAMES[0]), {"nope": 1.0}, "X1")  # type: ignore[arg-type]
    assert [d.severity for d in result.diagnostics] == [ERROR]
