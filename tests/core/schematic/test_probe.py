"""S11: probe requests and their arithmetic."""

from __future__ import annotations

import numpy as np
import pytest

from core.schematic import probe
from core.schematic.model import Instance
from core.symbols.asy import Symbol
from core.symbols.library import BUILTIN_DIR, SymbolLibrary

BUILTIN = SymbolLibrary(builtin_dir=BUILTIN_DIR)


def sym(name: str) -> Symbol:
    symbol = BUILTIN.lookup(name)[0]
    assert symbol is not None
    return symbol


VECTORS = {
    "V(in)": np.array([5.0, 5.0]),
    "V(out)": np.array([1.0, 2.0]),
    "I(r2)": np.array([0.004, 0.003]),
    "I(v1)": np.array([-0.004, -0.003]),
    "Ic(q1)": np.array([0.01, 0.02]),
    "Ib(q1)": np.array([0.001, 0.002]),
    "Ie(q1)": np.array([-0.011, -0.022]),
    "V(c)": np.array([3.0, 3.0]),
    "V(b)": np.array([0.7, 0.7]),
}


def values(p: probe.Probe) -> np.ndarray:
    assert p.ok, p.error
    out = probe.evaluate(p, VECTORS)
    assert not isinstance(out, str), out
    return out


def test_net_voltage_is_the_vector_itself() -> None:
    p = probe.net_voltage("OUT")
    assert (p.name, p.unit, p.vector) == ("V(out)", "V", "V(out)")
    assert probe.net_voltage("0").error == "that is ground (0 V)"
    assert not probe.net_voltage("").ok


def test_voltage_between_two_nets() -> None:
    p = probe.voltage_between("IN", "OUT")
    assert p.name == "V(in,out)" and p.vector is None
    np.testing.assert_allclose(values(p), [4.0, 3.0])
    assert probe.voltage_between("OUT", "0").name == "V(out)"
    np.testing.assert_allclose(values(probe.voltage_between("0", "OUT")), [-1.0, -2.0])
    assert probe.voltage_between("in", "IN").error == "both ends are on the same net"


def test_two_terminal_currents_into_each_pin() -> None:
    r2 = Instance("res", "R2", 0, 0)
    assert probe.part_current(r2, sym("res")).vector == "I(r2)"
    assert probe.pin_current(r2, sym("res"), 0).name == "I(r2)"
    into_b = probe.pin_current(r2, sym("res"), 1)
    assert into_b.name == "-I(r2)"
    np.testing.assert_allclose(values(into_b), [-0.004, -0.003])
    # A part called "load" is netlisted as Rload.
    assert probe.part_current(Instance("res", "load", 0, 0), sym("res")).vector == "I(rload)"


def test_transistor_pins_and_body() -> None:
    q1 = Instance("npn", "Q1", 0, 0)
    assert [probe.pin_current(q1, sym("npn"), k).vector for k in range(3)] == [
        "Ic(q1)",
        "Ib(q1)",
        "Ie(q1)",
    ]
    assert "click one of its pins" in probe.part_current(q1, sym("npn")).error
    m1 = Instance("nmos", "M1", 0, 0)
    assert probe.pin_current(m1, sym("nmos"), 1).vector == "Ig(m1)"


def test_subcircuit_pins_are_not_available() -> None:
    u1 = Instance("opamp", "U1", 0, 0)
    for p in (
        probe.pin_current(u1, sym("opamp"), 0),
        probe.part_current(u1, sym("opamp")),
        probe.part_power(u1, sym("opamp"), ["a", "b", "c", "d", "e"]),
    ):
        assert "not available in ngspice" in p.error


def test_power_of_a_two_terminal_part() -> None:
    r2 = Instance("res", "R2", 0, 0)
    p = probe.part_power(r2, sym("res"), ["IN", "OUT"])
    assert (p.name, p.unit) == ("V(in,out)*I(r2)", "W")
    np.testing.assert_allclose(values(p), [(5 - 1) * 0.004, (5 - 2) * 0.003])
    v1 = Instance("voltage", "V1", 0, 0)
    supplied = probe.part_power(v1, sym("voltage"), ["IN", "0"])
    assert supplied.name == "V(in)*I(v1)"
    assert np.all(values(supplied) < 0)  # a source that delivers power is negative


def test_power_of_a_transistor_sums_every_pin() -> None:
    q1 = Instance("npn", "Q1", 0, 0)
    p = probe.part_power(q1, sym("npn"), ["c", "b", "0"])
    assert p.name == "P(q1)"
    np.testing.assert_allclose(values(p), [3.0 * 0.01 + 0.7 * 0.001, 3.0 * 0.02 + 0.7 * 0.002])


def test_missing_vector_is_named_not_raised() -> None:
    p = probe.net_voltage("nowhere")
    assert probe.evaluate(p, VECTORS) == "V(nowhere)"


@pytest.mark.parametrize("name", ["ind", "diode", "current", "cap"])
def test_other_two_terminal_parts(name: str) -> None:
    inst = Instance(name, "X9", 0, 0)
    p = probe.part_current(inst, sym(name))
    assert p.ok and p.vector is not None and p.vector.startswith("I(")
