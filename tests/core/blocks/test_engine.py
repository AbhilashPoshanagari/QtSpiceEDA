from __future__ import annotations

import math

import numpy as np
import pytest

from core.blocks.decorator import block, spec_of
from core.blocks.diagram import Diagram, DiagramError
from core.blocks.engine import AlgebraicLoopError, BlockError, ExecutionEngine
from core.blocks.library import builtin_blocks


@block(outputs=["sum", "diff"])
def add_sub(a, b):
    return a + b, a - b


BLOCKS: dict[str, object] = {**builtin_blocks(), "add_sub": add_sub}
SPECS = {name: spec_of(obj) for name, obj in BLOCKS.items()}


def new_diagram() -> Diagram:
    return Diagram(SPECS.get)


def compiled(d: Diagram) -> ExecutionEngine:
    engine = ExecutionEngine(BLOCKS)
    engine.compile(d)
    return engine


def test_execution_order_follows_wires() -> None:
    d = new_diagram()
    # Placed in reverse signal order on purpose.
    d.add_block("Scope", block_id="scope")
    d.add_block("Scope", block_id="scope2", settings={"label": "b"})
    d.add_block("Sum", block_id="sum")
    d.add_block("Gain", block_id="gain")
    d.add_block("Sine", block_id="sine")
    d.add_block("Constant", block_id="const")
    d.add_wire("sine", "y", "gain", "u")
    d.add_wire("gain", "y", "sum", "a")
    d.add_wire("const", "y", "sum", "b")
    d.add_wire("sum", "y", "scope", "u")
    d.add_wire("sine", "y", "scope2", "u")
    order = compiled(d).order
    assert sorted(order) == sorted(b.id for b in d.blocks)
    for wire in d.wires:
        assert order.index(wire.src_block) < order.index(wire.dst_block), wire


def test_stateful_block_comes_before_what_it_feeds() -> None:
    d = new_diagram()
    d.add_block("Gain", block_id="gain")
    d.add_block("Integrator", block_id="integ")
    d.add_wire("integ", "y", "gain", "u")
    d.add_wire("gain", "y", "integ", "u")
    order = compiled(d).order
    assert order.index("integ") < order.index("gain")


def test_sine_gain_scope() -> None:
    d = new_diagram()
    d.add_block("Sine", settings={"frequency": 2.0})
    d.add_block("Gain", settings={"k": 2.0})
    d.add_block("Scope", settings={"label": "out"})
    d.add_wire("Sine1", "y", "Gain1", "u")
    d.add_wire("Gain1", "y", "Scope1", "u")
    result = compiled(d).run(t_end=1.0, dt=0.01)
    assert result.axis == "time" and result.analysis == "blocks"
    assert result.trace_names() == ["out"]
    t = result.vectors["time"]
    assert len(t) == 101 and t[0] == 0.0 and t[-1] == pytest.approx(1.0)
    np.testing.assert_allclose(result.vectors["out"], 2.0 * np.sin(2 * math.pi * 2.0 * t))


def test_constant_integrator_gives_unit_ramp() -> None:
    d = new_diagram()
    d.add_block("Constant", settings={"value": 1.0})
    d.add_block("Integrator")
    d.add_block("Scope")
    d.add_wire("Constant1", "y", "Integrator1", "u")
    d.add_wire("Integrator1", "y", "Scope1", "u")
    result = compiled(d).run(t_end=2.0, dt=0.1)
    t, y = result.vectors["time"], result.vectors["Scope1"]
    np.testing.assert_allclose(y, t, atol=1e-12)
    assert np.allclose(np.diff(y) / np.diff(t), 1.0)


def test_feedback_through_integrator() -> None:
    # dx/dt = -x, x(0) = 1, forward Euler: x[k] = (1 - dt)^k.
    d = new_diagram()
    d.add_block("Integrator", settings={"initial": 1.0})
    d.add_block("Gain", settings={"k": -1.0})
    d.add_block("Scope", settings={"label": "x"})
    d.add_wire("Integrator1", "y", "Gain1", "u")
    d.add_wire("Gain1", "y", "Integrator1", "u")
    d.add_wire("Integrator1", "y", "Scope1", "u")
    dt = 0.01
    result = compiled(d).run(t_end=1.0, dt=dt)
    k = np.arange(len(result.vectors["time"]))
    np.testing.assert_allclose(result.vectors["x"], (1 - dt) ** k)
    assert result.vectors["x"][-1] == pytest.approx(math.exp(-1.0), rel=0.01)


def test_feedback_through_unit_delay_counts() -> None:
    # y[k] = y[k-1] + 1 through a UnitDelay: a counter.
    d = new_diagram()
    d.add_block("UnitDelay")
    d.add_block("Constant")
    d.add_block("Sum")
    d.add_block("Scope")
    d.add_wire("UnitDelay1", "y", "Sum1", "a")
    d.add_wire("Constant1", "y", "Sum1", "b")
    d.add_wire("Sum1", "y", "UnitDelay1", "u")
    d.add_wire("UnitDelay1", "y", "Scope1", "u")
    result = compiled(d).run(t_end=4.0, dt=1.0)
    assert list(result.vectors["Scope1"]) == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_loop_of_two_gains_is_an_algebraic_loop() -> None:
    d = new_diagram()
    d.add_block("Gain", block_id="g1")
    d.add_block("Gain", block_id="g2")
    d.add_block("Scope")
    d.add_wire("g1", "y", "g2", "u")
    d.add_wire("g2", "y", "g1", "u")
    d.add_wire("g2", "y", "Scope1", "u")
    with pytest.raises(AlgebraicLoopError) as info:
        compiled(d)
    assert sorted(info.value.blocks) == ["g1", "g2"]
    assert "g1" in str(info.value) and "g2" in str(info.value)


def test_block_wired_to_itself_is_an_algebraic_loop() -> None:
    d = new_diagram()
    d.add_block("Sum")
    d.add_block("Constant")
    d.add_wire("Sum1", "y", "Sum1", "a")
    d.add_wire("Constant1", "y", "Sum1", "b")
    with pytest.raises(AlgebraicLoopError, match="Sum1"):
        compiled(d)


def test_unconnected_input_is_an_error() -> None:
    d = new_diagram()
    d.add_block("Gain")
    d.add_block("Scope")
    d.add_wire("Gain1", "y", "Scope1", "u")
    with pytest.raises(DiagramError, match=r"unconnected input\(s\): Gain1.u"):
        compiled(d)


def test_scope_names_must_differ() -> None:
    d = new_diagram()
    d.add_block("Constant")
    d.add_block("Scope", settings={"label": "v"})
    d.add_block("Scope", settings={"label": "v"})
    d.add_wire("Constant1", "y", "Scope1", "u")
    d.add_wire("Constant1", "y", "Scope2", "u")
    with pytest.raises(DiagramError, match="both record 'v'"):
        compiled(d)


def test_multi_output_block() -> None:
    d = new_diagram()
    d.add_block("Constant", settings={"value": 5.0})
    d.add_block("Constant", settings={"value": 3.0})
    d.add_block("add_sub")
    d.add_block("Scope", settings={"label": "s"})
    d.add_block("Scope", settings={"label": "d"})
    d.add_wire("Constant1", "y", "add_sub1", "a")
    d.add_wire("Constant2", "y", "add_sub1", "b")
    d.add_wire("add_sub1", "sum", "Scope1", "u")
    d.add_wire("add_sub1", "diff", "Scope2", "u")
    result = compiled(d).run(t_end=0.0, dt=1.0)
    assert result.vectors["s"][0] == 8.0 and result.vectors["d"][0] == 2.0


def test_exception_in_block_names_the_block() -> None:
    d = new_diagram()
    d.add_block("Constant")
    d.add_block("Sum", settings={"signs": "+*"})
    d.add_block("Scope")
    d.add_wire("Constant1", "y", "Sum1", "a")
    d.add_wire("Constant1", "y", "Sum1", "b")
    d.add_wire("Sum1", "y", "Scope1", "u")
    with pytest.raises(BlockError, match="block Sum1: ValueError"):
        compiled(d).run(1.0, 0.1)


def test_runs_are_repeatable() -> None:
    d = new_diagram()
    d.add_block("Constant")
    d.add_block("Integrator")
    d.add_block("Scope")
    d.add_wire("Constant1", "y", "Integrator1", "u")
    d.add_wire("Integrator1", "y", "Scope1", "u")
    engine = compiled(d)
    first = engine.run(1.0, 0.1).vectors["Scope1"]
    second = engine.run(1.0, 0.1).vectors["Scope1"]
    np.testing.assert_array_equal(first, second)


def test_bad_run_arguments() -> None:
    engine = ExecutionEngine(BLOCKS)
    with pytest.raises(RuntimeError, match="compile"):
        engine.run(1.0, 0.1)
    engine.compile(new_diagram())
    with pytest.raises(ValueError, match="time step"):
        engine.run(1.0, 0.0)
    with pytest.raises(ValueError, match="end time"):
        engine.run(-1.0, 0.1)
