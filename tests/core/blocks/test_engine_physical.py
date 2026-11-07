"""C3: a physical network is one stateful block of the engine (rule 14)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pytest

from core.blocks.diagram import DiagramError
from core.blocks.engine import AlgebraicLoopError, ExecutionEngine, NetworkError
from core.blocks.library import builtin_blocks
from tests.fixtures.physical.components import new_diagram, speed_loop, wire


class FakeNetwork:
    """Stands in for the CoSimulator's block: w follows tau with one tick of delay."""

    def __init__(self) -> None:
        self.w = 0.0
        self.inputs: list[dict[str, object]] = []

    def output(self) -> Mapping[str, float]:
        return {"sensor.w": self.w}

    def step(self, inputs: Mapping[str, object]) -> None:
        self.inputs.append(dict(inputs))
        self.w = float(inputs["motor.tau"])  # type: ignore[arg-type]


def compiled(d, reg) -> ExecutionEngine:
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    return engine


def test_network_is_one_stateful_node() -> None:
    d, reg = speed_loop()
    engine = compiled(d, reg)
    assert len(engine.networks) == 1
    net = engine.networks[0]
    assert net.id == "physical network 1"
    assert net.inputs == [("motor", "tau")]
    assert net.outputs == [("sensor", "w")]
    assert set(net.network.block_ids) == {"solver", "ref", "inertia", "damper", "motor", "sensor"}
    # Physical blocks are not nodes of their own; the network node comes first,
    # before the blocks fed by its sensor (stateful: depends on nothing).
    assert "inertia" not in engine.order and "motor" not in engine.order
    order = engine.order
    assert order.index(net.id) < order.index("error") < order.index("kp")


def test_loop_through_a_network_is_not_algebraic() -> None:
    d, reg = speed_loop(kp=0.5, w_set=10.0)
    engine = compiled(d, reg)
    fake = FakeNetwork()
    steps = engine.begin(0.004, 0.001, {engine.networks[0].id: fake})
    for k in range(steps):
        engine.tick(k)
    result = engine.finish()
    # Outputs are the previous tick's values: w_k = tau_{k-1}, tau_k = 0.5 * (10 - w_k).
    w, tau = [0.0], []
    for _ in range(steps):
        tau.append(0.5 * (10 - w[-1]))
        w.append(tau[-1])
    np.testing.assert_allclose(result.vectors["w"], w[:steps])
    np.testing.assert_allclose(result.vectors["tau"], tau)
    assert fake.inputs == [{"motor.tau": t} for t in tau]
    np.testing.assert_allclose(result.vectors["time"], [0, 0.001, 0.002, 0.003, 0.004])


def test_finish_keeps_only_the_ticks_run() -> None:
    d, reg = speed_loop()
    engine = compiled(d, reg)
    engine.begin(1.0, 0.1, {engine.networks[0].id: FakeNetwork()})
    for k in range(3):
        engine.tick(k)
    assert engine.ticks_done == 3
    assert len(engine.finish().vectors["w"]) == 3
    with pytest.raises(ValueError, match="out of order"):
        engine.tick(5)


def test_plain_run_refuses_physical_networks() -> None:
    d, reg = speed_loop()
    engine = compiled(d, reg)
    with pytest.raises(DiagramError, match="combined run"):
        engine.run(0.01, 0.001)
    with pytest.raises(ValueError, match="no NetworkBlock"):
        engine.begin(0.01, 0.001)


def test_physical_blocks_need_a_registry() -> None:
    d, _reg = speed_loop()
    engine = ExecutionEngine(builtin_blocks())
    with pytest.raises(DiagramError, match="physical block"):
        engine.compile(d)


def test_network_check_errors_stop_the_compile() -> None:
    d, reg = speed_loop()
    d.remove_block("solver")
    with pytest.raises(NetworkError, match="Solver Configuration") as info:
        compiled(d, reg)
    assert info.value.diagnostics[0].block == "inertia"


def test_unconnected_physical_signal_input() -> None:
    d, reg = speed_loop()
    for w in list(d.wires):
        if w.dst_block == "motor":
            d.remove_wire(w)
    with pytest.raises(DiagramError, match=r"motor\.tau"):
        compiled(d, reg)


def test_loop_of_two_gains_is_still_rejected_next_to_a_network() -> None:
    d, reg = speed_loop()
    d.add_block("Gain", block_id="g1")
    d.add_block("Gain", block_id="g2")
    d.add_wire("g1", "y", "g2", "u")
    d.add_wire("g2", "y", "g1", "u")
    with pytest.raises(AlgebraicLoopError) as info:
        compiled(d, reg)
    assert sorted(info.value.blocks) == ["g1", "g2"]


def test_two_networks_are_two_nodes() -> None:
    d, reg = new_diagram()
    for i in (1, 2):
        d.add_block("Solver", block_id=f"s{i}")
        d.add_block("RotRef", block_id=f"r{i}")
        d.add_block("Inertia", block_id=f"j{i}")
        d.add_block("SpeedSensor", block_id=f"w{i}")
        wire(d, f"s{i}.port", f"r{i}.W")
        wire(d, f"j{i}.A", f"w{i}.A")
        wire(d, f"j{i}.A", f"r{i}.W")
    engine = compiled(d, reg)
    assert [n.outputs for n in engine.networks] == [[("w1", "w")], [("w2", "w")]]
