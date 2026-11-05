"""Probing a schematic after a run (S11). Qt-free.

A Probe names a trace and says how to compute it from the result's vectors:
``terms`` is a sum of signed products, so V(a) - V(b) is ((+1, ("V(a)",)),
(-1, ("V(b)",))) and the power of R1 is V(p1)*I(r1) - V(p2)*I(r1). This is all the
arithmetic the probes need; the general LTspice expressions come with the waveform
window (W8). Nothing is parsed or evaluated from user text.

Vector names are the ones the ngspice adapter gives: V(net) with the net in lower
case, I(r1) for two-terminal parts (with ".options savecurrents"), Ic/Ib/Ie/Is(q1)
and Id/Ig/Is/Ib(m1) for transistors. Currents are into a pin, positive when current
flows into the part there, as in LTspice. Ground is 0 V and drops out of every sum.

ngspice does not save currents at subcircuit (X) pins, so those probes report that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.netlist.model import GROUND_NODES
from core.schematic.model import Instance
from core.symbols.asy import Symbol

Terms = tuple[tuple[float, tuple[str, ...]], ...]

TWO_TERMINAL = frozenset("RCLDVIB")
PIN_CURRENTS = {"Q": ("Ic", "Ib", "Ie", "Is"), "M": ("Id", "Ig", "Is", "Ib")}


@dataclass(frozen=True)
class Probe:
    """A trace to show: ``name`` (LTspice style), ``unit``, and how to compute it.
    ``error`` is set (and ``terms`` empty) when the probe cannot be made."""

    name: str
    unit: str = ""
    terms: Terms = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def vector(self) -> str | None:
        """The one result vector this probe is, when no arithmetic is needed."""
        if len(self.terms) == 1:
            sign, factors = self.terms[0]
            if sign == 1 and len(factors) == 1:
                return factors[0]
        return None


def failed(message: str) -> Probe:
    return Probe("", error=message)


def is_ground(net: str) -> bool:
    return net.casefold() in GROUND_NODES


def voltage_name(net: str) -> str:
    return f"V({net.lower()})"


def spice_name(inst: Instance, symbol: Symbol) -> str:
    """The element name the netlister writes (R1, XU1), in lower case as ngspice has it."""
    letter = symbol.prefix[:1].upper()
    name = inst.name.strip()
    if letter and name[:1].upper() != letter:
        name = letter + name
    return name.lower()


def letter_of(symbol: Symbol) -> str:
    return symbol.prefix[:1].upper()


def net_voltage(net: str) -> Probe:
    if not net:
        return failed("this wire is not part of the circuit that ran")
    if is_ground(net):
        return failed("that is ground (0 V)")
    return Probe(voltage_name(net), "V", ((1.0, (voltage_name(net),)),))


def voltage_between(a: str, b: str) -> Probe:
    if not a or not b:
        return failed("drag from one net to another")
    if a.casefold() == b.casefold():
        return failed("both ends are on the same net")
    if is_ground(b):
        return net_voltage(a)
    if is_ground(a):
        return Probe(f"V(0,{b.lower()})", "V", ((-1.0, (voltage_name(b),)),))
    terms = ((1.0, (voltage_name(a),)), (-1.0, (voltage_name(b),)))
    return Probe(f"V({a.lower()},{b.lower()})", "V", terms)


def _current_vector(inst: Instance, symbol: Symbol, pin: int) -> tuple[str, float] | str:
    """(vector, sign) for the current into pin ``pin`` (SpiceOrder index), or why not."""
    letter = letter_of(symbol)
    name = spice_name(inst, symbol)
    if letter == "X":
        return f"{inst.name}: currents at subcircuit pins are not available in ngspice"
    if letter in PIN_CURRENTS:
        names = PIN_CURRENTS[letter]
        if pin >= len(names):
            return f"{inst.name}: no current for pin {pin + 1}"
        return f"{names[pin]}({name})", 1.0
    if letter in TWO_TERMINAL or letter in "EFGH":
        if pin == 0:
            return f"I({name})", 1.0
        if pin == 1:
            return f"I({name})", -1.0
        return f"{inst.name}: the control pins of {letter} sources carry no current"
    return f"{inst.name}: no current can be probed for Prefix {symbol.prefix or '-'}"


def pin_current(inst: Instance, symbol: Symbol, pin: int) -> Probe:
    """The current into one pin."""
    found = _current_vector(inst, symbol, pin)
    if isinstance(found, str):
        return failed(found)
    vector, sign = found
    name = vector if sign > 0 else f"-{vector}"
    return Probe(name, "A", ((sign, (vector,)),))


def part_current(inst: Instance, symbol: Symbol) -> Probe:
    """Clicking a part's body: the current through a two-terminal part (into pin 1)."""
    letter = letter_of(symbol)
    if len(symbol.pins) != 2 and letter not in "EFGH":
        if letter == "X":
            return failed(f"{inst.name}: currents at subcircuit pins are not available in ngspice")
        return failed(f"{inst.name}: click one of its pins for that pin's current")
    return pin_current(inst, symbol, 0)


def part_power(inst: Instance, symbol: Symbol, nets: list[str]) -> Probe:
    """Alt+click: the power into the part, the sum of V(pin) * I(into pin)."""
    letter = letter_of(symbol)
    if letter == "X":
        return failed(f"{inst.name}: currents at subcircuit pins are not available in ngspice")
    pins = 2 if letter in TWO_TERMINAL or letter in "EFGH" else len(symbol.pins)
    terms: list[tuple[float, tuple[str, ...]]] = []
    for k in range(min(pins, len(nets))):
        found = _current_vector(inst, symbol, k)
        if isinstance(found, str):
            return failed(found)
        vector, sign = found
        if nets[k] and not is_ground(nets[k]):
            terms.append((sign, (voltage_name(nets[k]), vector)))
    name = spice_name(inst, symbol)
    if pins == 2 and len(nets) >= 2:
        current = f"I({name})"
        a, b = nets[0].lower(), nets[1].lower()
        across = f"V({a})" if is_ground(b) else (f"V(0,{b})" if is_ground(a) else f"V({a},{b})")
        title = f"{across}*{current}"
    else:
        title = f"P({name})"
    if not terms:
        return failed(f"{inst.name}: all its pins are on ground, so its power is 0")
    return Probe(title, "W", tuple(terms))


def evaluate(probe: Probe, vectors: dict[str, np.ndarray]) -> np.ndarray | str:
    """The probe's values from a result's vectors, or the name of a missing vector."""
    total: np.ndarray | None = None
    for sign, factors in probe.terms:
        product: np.ndarray | None = None
        for factor in factors:
            if factor not in vectors:
                return factor
            values = np.asarray(vectors[factor])
            product = values if product is None else product * values
        if product is None:
            continue
        term = sign * product
        total = term if total is None else total + term
    if total is None:
        return probe.name or "nothing to show"
    return total
