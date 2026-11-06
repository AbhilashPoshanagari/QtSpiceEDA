from __future__ import annotations

import numpy as np
import pytest

from core.netlist.model import ERROR, WARNING
from core.waveform.expr import Data
from core.waveform.plotdefs import DEFAULT_TEXT, parse_plotdefs

T = np.linspace(0.0, 1.0, 5)


def _data() -> Data:
    return Data(
        "tran",
        "time",
        T,
        {"time": ("s", T), "V(x)": ("V", 3 * T), "V(y)": ("V", 4 * T), "V(out)": ("V", 10 * T)},
    )


def test_user_pythag_func_works() -> None:
    defs = parse_plotdefs(".func Pythag(x,y) sqrt(x**2+y**2)\n")
    assert defs.ok and defs.diagnostics == []
    result = defs.engine().evaluate("Pythag(V(x),V(y))", _data())
    assert result.ok
    np.testing.assert_allclose(result.values, 5 * T)


def test_default_text_defines_db() -> None:
    defs = parse_plotdefs(DEFAULT_TEXT)
    assert defs.ok and defs.diagnostics == [] and list(defs.definitions.funcs) == ["db"]
    assert ".func dB(x) 20*log10(Mag(x))" in DEFAULT_TEXT
    result = defs.engine().evaluate("dB(V(out))", _data())
    np.testing.assert_allclose(result.values[1:], 20 * np.log10(10 * T[1:]))
    ac = Data("ac", "frequency", np.array([1.0, 2.0]), {"V(o)": ("V", np.array([10j, 0.1]))})
    np.testing.assert_allclose(defs.engine().evaluate("dB(V(o))", ac).values, [20, -20])


def test_spice_forms_braces_equals_continuations_and_comments() -> None:
    text = (
        "* comment\n"
        "\n"
        ".FUNC twice(a) = {2*a}  ; trailing comment\n"
        ".func plus(a,\n"
        "+ b) a+b\n"
        ".param g=2 h = 'g*3'\n"
        "+ k2=1k\n"
    )
    defs = parse_plotdefs(text)
    assert defs.ok, defs.diagnostics
    assert set(defs.definitions.funcs) == {"twice", "plus"}
    assert set(defs.definitions.params) == {"g", "h", "k2"}
    engine = defs.engine()
    assert engine.evaluate("twice(h) + plus(1, k2)", _data()).values[0] == pytest.approx(1013)
    assert defs.definitions.funcs["plus"].line == 4


def test_forward_references_and_nested_calls() -> None:
    defs = parse_plotdefs(
        ".func outer(x) inner(x)*2\n.func inner(x) x+1\n.param a=b*2\n.param b=3\n"
    )
    assert defs.ok
    engine = defs.engine()
    assert engine.evaluate("outer(a)", _data()).values[0] == 14


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (".func sin(x) x", "built-in"),
        (".func V(x) x", "built-in"),
        (".func f x", ".func needs"),
        (".func f(x)", "no body"),
        (".func f(x, x) x", "distinct"),
        (".func f(1x) x", "distinct"),
        (".func 9f(x) x", "not a valid name"),
        (".func f(x) x +* ", "syntax error"),
        (".func f(x) __import__('os')", "unexpected character"),
        (".func f(x) nosuch(x)", "unknown function"),
        (".param", "name = value"),
        (".param 3 = 4", "name = value"),
        (".param a =", "no value"),
        (".param a = 1 +", "syntax error"),
    ],
)
def test_bad_lines_give_errors_and_are_left_out(text: str, fragment: str) -> None:
    defs = parse_plotdefs(text + "\n.func ok(x) x\n")
    errors = [d for d in defs.diagnostics if d.severity == ERROR]
    assert len(errors) == 1 and fragment in errors[0].message and errors[0].line == 1
    assert "ok" in defs.definitions.funcs


def test_warnings() -> None:
    defs = parse_plotdefs(".func f(x) x\n.func f(x) 2*x\n.param pi=3\n.meas foo\nhello\n")
    assert defs.ok
    warnings = [d for d in defs.diagnostics if d.severity == WARNING]
    assert [d.line for d in warnings] == [2, 3, 4, 5]
    assert defs.engine().evaluate("f(1)+pi", _data()).values[0] == 5


def test_recursion_is_an_evaluation_error_not_a_crash() -> None:
    defs = parse_plotdefs(".func f(x) g(x)\n.func g(x) f(x)\n")
    assert defs.ok
    result = defs.engine().evaluate("f(1)", _data())
    assert not result.ok and "too deeply" in result.message


def test_never_raises_on_junk() -> None:
    for junk in ["", "\x00\x01", ".func", ".param ==", "+ orphan", "(((", ".func f(x) {"]:
        parse_plotdefs(junk)
