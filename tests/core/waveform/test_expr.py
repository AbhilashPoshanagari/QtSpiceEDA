from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from core.netlist.model import ERROR
from core.waveform.expr import (
    BUILTIN_FUNCTIONS,
    CONSTANTS,
    Data,
    Definitions,
    ExpressionEngine,
    FuncDef,
    ParamDef,
    _Reject,
    check_tree,
)

T = np.linspace(0.0, 1.0, 11)
A = 0.2 + 0.6 * T  # 0.2 .. 0.8, crosses 0.5 at t = 0.5
B = 1.2 - T


def _tran() -> Data:
    return Data(
        "tran",
        "time",
        T,
        {"time": ("s", T), "V(a)": ("V", A), "V(b)": ("V", B), "I(r1)": ("A", 2 * T)},
    )


F = np.logspace(0, 5, 501)
H = 1 / (1 + 1j * 2 * np.pi * F * 1e-3)


def _ac() -> Data:
    return Data(
        "ac",
        "frequency",
        F,
        {
            "frequency": ("Hz", F.astype(complex)),
            "V(out)": ("V", H),
            "V(in)": ("V", np.ones_like(H)),
        },
    )


def ev(text: str, data: Data | None = None, engine: ExpressionEngine | None = None):
    result = (engine or ExpressionEngine()).evaluate(text, data or _tran())
    assert result.ok, result.message
    return result.values


def err(text: str, data: Data | None = None, engine: ExpressionEngine | None = None) -> str:
    result = (engine or ExpressionEngine()).evaluate(text, data or _tran())
    assert not result.ok and result.values is None
    assert result.diagnostics and result.diagnostics[0].severity == ERROR
    return result.message


# ---------------------------------------------------------------- operators

TRUE_A = (A > 0.5).astype(float)
TRUE_B = (B > 0.5).astype(float)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("V(a)+V(b)", A + B),
        ("V(a)-V(b)", A - B),
        ("V(a)*V(b)", A * B),
        ("V(a)/V(b)", A / B),
        ("V(a)**2", A**2),
        ("-V(a)", -A),
        ("+V(a)", A),
        ("V(a)>0.5", (A > 0.5).astype(float)),
        ("V(a)<0.5", (A < 0.5).astype(float)),
        ("V(a)>=0.5", (A >= 0.5).astype(float)),
        ("V(a)<=0.5", (A <= 0.5).astype(float)),
        ("V(a)&V(b)", TRUE_A * TRUE_B),
        ("V(a)|V(b)", np.maximum(TRUE_A, TRUE_B)),
        ("V(a)^V(b)", (TRUE_A != TRUE_B).astype(float)),
        ("!V(a)", 1 - TRUE_A),
    ],
)
def test_operator(text: str, expected: np.ndarray) -> None:
    np.testing.assert_allclose(ev(text), expected)


def test_caret_is_boolean_xor_not_power() -> None:
    assert ev("2^3")[0] == 0.0  # both true -> 0, not 8
    assert ev("1^0")[0] == 1.0 and ev("0^1")[0] == 1.0 and ev("0^0")[0] == 0.0
    assert ev("0.4^0.6")[0] == 1.0  # truth threshold 0.5
    assert ev("2**3")[0] == 8.0


def test_boolean_and_comparison_precedence() -> None:
    # comparisons bind tighter than & | ^ (LTspice), unlike Python's bitwise operators
    np.testing.assert_allclose(ev("V(a)>0.5 & V(b)>0.3"), ((A > 0.5) & (B > 0.3)).astype(float))
    assert ev("1 | 0 & 0")[0] == 1.0  # & before |
    assert ev("1+2*3")[0] == 7.0 and ev("2*3**2")[0] == 18.0 and ev("-2**2")[0] == -4.0
    assert ev("!0 + 1")[0] == 2.0  # ! binds tightest
    assert ev("1 < 2 < 0.5")[0] == 0.0  # (1 < 2) < 0.5, not a Python chain


def test_v_of_two_nodes_is_a_difference() -> None:
    np.testing.assert_allclose(ev("V(a,b)"), A - B)
    np.testing.assert_allclose(ev("V(a, 0)"), A)
    np.testing.assert_allclose(ev("V(gnd,a)"), -A)


def test_names_are_case_insensitive_and_numbers_take_suffixes() -> None:
    np.testing.assert_allclose(ev("v(A)+i(R1)"), A + 2 * T)
    assert ev("1k")[0] == 1000.0 and ev("2meg")[0] == 2e6 and ev("3u")[0] == pytest.approx(3e-6)
    assert ev("1e-3")[0] == 1e-3 and ev(".5")[0] == 0.5 and ev("4.7µ")[0] == pytest.approx(4.7e-6)


# ---------------------------------------------------------------- functions

X = np.linspace(0.1, 0.9, 11)


def _xdata() -> Data:
    return Data("tran", "time", T, {"time": ("s", T), "V(x)": ("V", X), "V(y)": ("V", 1 - X)})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sin(V(x))", np.sin(X)),
        ("cos(V(x))", np.cos(X)),
        ("tan(V(x))", np.tan(X)),
        ("asin(V(x))", np.arcsin(X)),
        ("acos(V(x))", np.arccos(X)),
        ("atan(V(x))", np.arctan(X)),
        ("atan2(V(x),V(y))", np.arctan2(X, 1 - X)),
        ("sinh(V(x))", np.sinh(X)),
        ("cosh(V(x))", np.cosh(X)),
        ("tanh(V(x))", np.tanh(X)),
        ("ln(V(x))", np.log(X)),
        ("log(V(x))", np.log(X)),  # LTspice: natural log
        ("log10(V(x))", np.log10(X)),
        ("exp(V(x))", np.exp(X)),
        ("sqrt(V(x))", np.sqrt(X)),
        ("pow(V(x),3)", X**3),
        ("abs(-V(x))", X),
        ("ceil(V(x)*3)", np.ceil(X * 3)),
        ("floor(V(x)*3)", np.floor(X * 3)),
        ("round(V(x)*3)", np.round(X * 3)),
        ("int(-V(x)*3)", np.trunc(-X * 3)),
        ("if(V(x)>0.5, 1, 2)", np.where(X > 0.5, 1.0, 2.0)),
        ("limit(V(x), 0.3, 0.6)", np.clip(X, 0.3, 0.6)),
        ("limit(V(x), 0.6, 0.3)", np.clip(X, 0.3, 0.6)),
        ("min(V(x),V(y))", np.minimum(X, 1 - X)),
        ("max(V(x),V(y))", np.maximum(X, 1 - X)),
        ("hypot(V(x),V(y))", np.hypot(X, 1 - X)),
        ("d(V(x))", np.full_like(X, 0.8)),
        ("Re(V(x))", X),
        ("Im(V(x))", np.zeros_like(X)),
        ("Ph(-V(x))", np.full_like(X, 180.0)),
        ("Mag(-V(x))", X),
        ("conj(V(x))", X),
    ],
)
def test_function(text: str, expected: np.ndarray) -> None:
    np.testing.assert_allclose(ev(text, _xdata()), expected, rtol=1e-9, atol=1e-12)


def test_every_listed_function_is_tested() -> None:
    listed = (
        (
            "sin cos tan asin acos atan atan2 sinh cosh tanh ln log log10 exp sqrt pow "
            "abs ceil floor round int if limit min max hypot d Re Im Ph Mag conj"
        )
        .lower()
        .split()
    )
    assert sorted(listed) == sorted(BUILTIN_FUNCTIONS)


def test_complex_functions_on_ac_data() -> None:
    data = _ac()
    np.testing.assert_allclose(ev("Re(V(out))", data), H.real)
    np.testing.assert_allclose(ev("Im(V(out))", data), H.imag)
    np.testing.assert_allclose(ev("Mag(V(out))", data), np.abs(H))
    np.testing.assert_allclose(ev("Ph(V(out))", data), np.degrees(np.angle(H)))
    np.testing.assert_allclose(ev("conj(V(out))", data), np.conj(H))
    np.testing.assert_allclose(ev("V(out)*V(out)", data), H * H)
    assert np.iscomplexobj(ev("V(out)/V(in)", data))
    assert np.iscomplexobj(ev("1", data))  # AC results are complex


def test_derivative_over_the_axis() -> None:
    np.testing.assert_allclose(ev("d(time**2)")[1:-1], 2 * T[1:-1], rtol=1e-9)
    t = np.linspace(0, 2 * np.pi, 2001)
    data = Data("tran", "time", t, {"time": ("s", t), "V(s)": ("V", np.sin(t))})
    np.testing.assert_allclose(ev("d(V(s))", data), np.cos(t), atol=1e-5)


# ---------------------------------------------------------------- constants and axes


@pytest.mark.parametrize(
    ("text", "expected"),
    [("E", math.e), ("pi", math.pi), ("K", 1.380649e-23), ("Q", 1.602176634e-19)],
)
def test_constant(text: str, expected: float) -> None:
    assert ev(text)[0] == pytest.approx(expected)
    assert text.lower() in CONSTANTS


def test_axis_names() -> None:
    np.testing.assert_allclose(ev("time"), T)
    np.testing.assert_allclose(ev("2*time"), 2 * T)
    data = _ac()
    np.testing.assert_allclose(ev("omega", data).real, 2 * np.pi * F)
    np.testing.assert_allclose(ev("w", data).real, 2 * np.pi * F)
    np.testing.assert_allclose(ev("frequency", data).real, F)
    assert "only defined for an .ac" in err("omega")
    assert "not the axis" in err("time", data)


def test_the_axis_vector_own_name() -> None:
    v = np.linspace(-1, 1, 5)
    data = Data("dc", "v1", v, {"v1": ("V", v), "I(d1)": ("A", v**2)})
    np.testing.assert_allclose(ev("v1*2", data), 2 * v)


# ---------------------------------------------------------------- units (W10)


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("V(a)", "V"),
        ("V(a,b)", "V"),
        ("V(a)-V(b)+1", "V"),
        ("2*V(a)", "V"),
        ("V(a)/2", "V"),
        ("abs(V(a))", "V"),
        ("I(r1)", "A"),
        ("-I(r1)*3", "A"),
        ("V(a)*I(r1)", "W"),
        ("V(a)/V(b)", ""),
        ("V(a)+I(r1)", ""),
        ("sin(V(a))", ""),
        ("V(a)>0.5", ""),
        ("3", ""),
    ],
)
def test_unit_inference(text: str, unit: str) -> None:
    assert ExpressionEngine().evaluate(text, _tran()).unit == unit


# ---------------------------------------------------------------- rejection


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "__import__(1)",
        "V(a).real",
        "time.__class__",
        "lambda: 1",
        "(lambda x: x)(1)",
        "open(1)",
        "eval(1)",
        "V(a)[0]",
        "x = 1",
        "V(a) == 1",
        "V(a) != 1",
        "1 if 1 else 2",
        "[1, 2]",
        "{1: 2}",
        "'text'",
        "1 ^",
        "",
        "sin(1, 2)",
        "nosuch(1)",
        "V(a b)",
        "I(r1, r2)",
        "V(a",
        "V(a)/*",
    ],
)
def test_rejected(text: str) -> None:
    assert err(text)


def test_unknown_vectors_and_names_are_errors_not_crashes() -> None:
    assert "unknown vector V(nope)" in err("V(nope)")
    assert "unknown name" in err("nope*2")
    assert "unknown function" in err("__import__(1)")


@pytest.mark.parametrize(
    "source",
    [
        "(lambda: 1)()",
        "n0.real",
        "n0[0]",
        "'os'",
        "n0(x=1)",
        "n0(*n1)",
        "[n0]",
        "n0 if n0 else n0",
        "n0 % 2",
        "n0 // 2",
        "n0 << 1",
        "n0 == 1",
        "not n0",
        "True",
        "1j",
        "undefined",
        "n0(1)",  # n0 is a vector, not a function
        "(n2 := 1)",
    ],
)
def test_whitelist_rejects_python_constructs(source: str) -> None:
    from core.waveform.expr import _Symbol

    symbols = {"n0": _Symbol("vec", "V(a)", 1), "n1": _Symbol("vec", "V(b)", 1)}
    with pytest.raises(_Reject):
        check_tree(ast.parse(source, mode="eval"), symbols)


def test_module_never_calls_eval_or_exec() -> None:
    import core.waveform.expr as expr

    code = Path(expr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(code)
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not called & {"eval", "exec", "compile", "__import__"}


def test_error_columns_point_at_the_problem() -> None:
    result = ExpressionEngine().evaluate("V(a) + V(zz)", _tran())
    assert result.diagnostics[0].col == 8
    assert ExpressionEngine().check("1 + nosuch(2)")[0].col == 5


# ---------------------------------------------------------------- user definitions


def test_user_func_and_param() -> None:
    defs = Definitions(
        funcs={"pythag": FuncDef("Pythag", ["x", "y"], "sqrt(x**2+y**2)")},
        params={"gain": ParamDef("gain", "2*half"), "half": ParamDef("half", "0.5")},
    )
    engine = ExpressionEngine(defs)
    np.testing.assert_allclose(ev("Pythag(V(a),V(b))", engine=engine), np.hypot(A, B))
    np.testing.assert_allclose(ev("gain*V(a)", engine=engine), A)
    assert "takes 2 argument" in err("pythag(1)", engine=engine)


def test_recursive_definitions_are_reported() -> None:
    defs = Definitions(
        funcs={"f": FuncDef("f", ["x"], "f(x)+1")},
        params={"p": ParamDef("p", "p+1")},
    )
    engine = ExpressionEngine(defs)
    assert "too deeply" in err("f(1)", engine=engine)
    assert "refers to itself" in err("p", engine=engine)
