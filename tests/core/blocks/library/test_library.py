from __future__ import annotations

import math

import pytest

from core.blocks.library import builtin_blocks, builtin_specs
from core.blocks.library.arithmetic import Gain, Product, Sum
from core.blocks.library.sinks import Scope
from core.blocks.library.sources import Constant, Sine, Step
from core.blocks.library.stateful import Integrator, UnitDelay
from core.blocks.registry import BlockRegistry

NAMES = ["Constant", "Step", "Sine", "Gain", "Sum", "Product", "Integrator", "UnitDelay", "Scope"]


def test_all_builtins_in_order() -> None:
    assert list(builtin_blocks()) == NAMES
    assert [s.name for s in builtin_specs()] == NAMES


def test_builtins_register() -> None:
    reg = BlockRegistry(builtin_specs())
    assert all(reg.is_builtin(n) for n in NAMES)


def test_ports() -> None:
    specs = {s.name: s for s in builtin_specs()}
    assert specs["Constant"].inputs == []
    assert specs["Step"].inputs == [] and specs["Step"].time_args == ["t"]
    assert specs["Sum"].inputs == ["a", "b"] and specs["Sum"].settings == {"signs": "++"}
    assert specs["Integrator"].stateful and specs["Integrator"].inputs == ["u"]
    assert specs["Integrator"].time_args == ["dt"]
    assert specs["UnitDelay"].time_args == []
    assert specs["Scope"].outputs == [] and specs["Scope"].inputs == ["u"]


def test_sources() -> None:
    assert Constant(2.5) == 2.5
    assert Step(0.5) == 0.0
    assert Step(1.0) == 1.0
    assert Step(2.0, step_time=3.0, initial=-1.0, final=4.0) == -1.0
    assert Sine(0.25, amplitude=2.0, frequency=1.0) == pytest.approx(2.0)
    assert Sine(0.0, phase=math.pi / 2, offset=1.0) == pytest.approx(2.0)


def test_arithmetic() -> None:
    assert Gain(3.0, k=2.0) == 6.0
    assert Sum(3.0, 1.0) == 4.0
    assert Sum(3.0, 1.0, signs="+-") == 2.0
    assert Sum(3.0, 1.0, signs="-+") == -2.0
    assert Product(3.0, 2.0) == 6.0
    with pytest.raises(ValueError, match="signs"):
        Sum(1.0, 1.0, signs="+")


def test_integrator_forward_euler() -> None:
    integ = Integrator(initial=1.0)
    outputs = []
    for _ in range(3):
        outputs.append(integ.output())
        integ.step(u=2.0, dt=0.5)
    assert outputs == [1.0, 2.0, 3.0]
    assert integ.output() == 4.0


def test_unit_delay() -> None:
    delay = UnitDelay(initial=-1.0)
    outputs = []
    for u in (10.0, 20.0, 30.0):
        outputs.append(delay.output())
        delay.step(u=u)
    assert outputs == [-1.0, 10.0, 20.0]


def test_scope_returns_nothing() -> None:
    assert Scope(1.0) is None
