"""ExpressionEngine: LTspice-style waveform expressions on NumPy arrays (W7). Qt-free.

Text such as ``V(out)/V(in)``, ``V(a,b)``, ``I(R1)*V(n1)`` or ``V(c)>0.5 & V(d)>0.5`` is
turned into a trace. CLAUDE.md rule 9: the text is never given to eval() or exec().

How it works
1. A small lexer reads the LTspice text. Every identifier the user wrote becomes a
   generated name (``n0``, ``n1``, ...) whose meaning is kept in a symbol table, so no
   Python name from the user ever reaches the tree. ``V(a)``/``I(R1)``/``Ic(Q1)`` are
   read as vector references (their inside is raw text, e.g. a net name),
   ``V(a,b)`` becomes ``(V(a) - V(b))`` and numbers take SPICE suffixes (``1k``, ``2meg``).
   Operators are mapped so that Python's grammar gives LTspice's precedence
   (low to high: ``& | ^``, comparisons, ``+ -``, ``* /``, ``**``, ``!``):
   ``&`` -> ``and``, ``|`` -> ``or``, ``^`` (Boolean XOR, not power) -> ``or <xor> or``
   with a reserved marker name, ``!`` -> ``~``.
2. ``ast.parse`` builds the tree and a whitelist walker rejects any node type that is
   not listed (attributes, subscripts, lambdas, strings, keywords, ...).
3. The tree is evaluated on NumPy arrays, real or complex (AC).

Semantics (LTspice)
- Boolean operators and ``!`` treat a value as true when its real part is > 0.5 and
  give 1 or 0; comparisons compare real parts and give 1 or 0.
- ``log`` is the natural log (same as ``ln``); ``Ph`` is the phase in degrees.
- ``d(x)`` is a finite-difference derivative over the result's axis (np.gradient).
- Constants ``E`` ``pi`` ``K`` (Boltzmann, J/K) ``Q`` (electron charge, C); axis names
  ``time`` (.tran), ``frequency`` and ``omega``/``w`` = 2*pi*frequency (.ac), plus the
  axis vector's own name. Names are case-insensitive.

Nothing raises on bad text: results carry ``Diagnostic(line, col, severity, message)``.
The unit of a result is inferred ("V", "A", "W" or "") so the waveform window can tell
voltages and currents apart (W10 shows RMS only for those).
"""

from __future__ import annotations

import ast
import bisect
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from core.netlist.model import ERROR, GROUND_NODES, Diagnostic
from core.netlist.parser import parse_number

# ---------------------------------------------------------------- names

CONSTANTS: dict[str, float] = {
    "e": math.e,
    "pi": math.pi,
    "k": 1.380649e-23,  # Boltzmann constant, J/K
    "q": 1.602176634e-19,  # electron charge, C
}

# Heads that make a vector reference: V(node[,node]), I(device), Ic(Q1), Id(M1) ...
_VOLTAGE = "v"
_CURRENTS = frozenset({"i", "ic", "ib", "ie", "is", "id", "ig"})

_XOR = "x_or"  # reserved marker; user identifiers never come out as this name
_ANY = "*"  # unit of a plain number: takes the unit of what it is combined with
MAX_DEPTH = 32  # nested .func / .param evaluation


# ---------------------------------------------------------------- definitions


@dataclass
class FuncDef:
    """A user ``.func name(params) body`` (W9)."""

    name: str
    params: list[str]
    body: str
    line: int = 0


@dataclass
class ParamDef:
    """A user ``.param name = body`` (W9)."""

    name: str
    body: str
    line: int = 0


@dataclass
class Definitions:
    """User functions and parameters, keyed by lower-case name."""

    funcs: dict[str, FuncDef] = field(default_factory=dict)
    params: dict[str, ParamDef] = field(default_factory=dict)


# ---------------------------------------------------------------- data


@dataclass
class Data:
    """The vectors an expression can use. ``vectors`` maps display names (``V(out)``,
    ``I(r1)``) to ``(unit, values)``; every array has the axis' length and is complex
    for AC. ``axis`` is None for an operating point."""

    analysis: str
    axis_name: str
    axis: np.ndarray | None
    vectors: dict[str, tuple[str, np.ndarray]]
    _lower: dict[str, str] = field(default_factory=dict, repr=False)

    def lookup(self, name: str) -> tuple[str, np.ndarray] | None:
        if not self._lower:
            self._lower = {n.lower(): n for n in self.vectors}
        key = self._lower.get(name.lower())
        return self.vectors[key] if key is not None else None

    @property
    def size(self) -> int:
        if self.axis is not None:
            return len(self.axis)
        return len(next(iter(self.vectors.values()))[1]) if self.vectors else 1


@dataclass
class ExprResult:
    values: np.ndarray | None
    unit: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.values is not None and not any(d.severity == ERROR for d in self.diagnostics)

    @property
    def message(self) -> str:
        """The first error as one line ("" when there is none)."""
        for d in self.diagnostics:
            if d.severity == ERROR:
                return f"col {d.col}: {d.message}" if d.col > 0 else d.message
        return ""


# ---------------------------------------------------------------- lexer

_NUMBER = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?(?:meg|mil|[fpnumkgt])?[a-z]*", re.I)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OPERATORS = (
    ("**", "**"),
    ("&&", " and "),
    ("||", " or "),
    (">=", ">="),
    ("<=", "<="),
    ("&", " and "),
    ("|", " or "),
    ("^", f" or {_XOR} or "),
    ("!", "~"),
    (">", ">"),
    ("<", "<"),
    ("+", "+"),
    ("-", "-"),
    ("*", "*"),
    ("/", "/"),
    ("(", "("),
    (")", ")"),
    (",", ","),
)


@dataclass
class _Symbol:
    kind: str  # "vec" (vector ref), "zero" (ground), "name" (bare name), "func"
    text: str  # vector display name, or the name as written
    col: int

    @property
    def key(self) -> str:
        return self.text.lower()


@dataclass
class Compiled:
    """A checked expression: the tree plus what each generated name stands for."""

    text: str
    tree: ast.Expression
    symbols: dict[str, _Symbol]
    line: int = 1


class _Lexer:
    def __init__(self, text: str, line: int) -> None:
        self.text = text.replace("µ", "u").replace("μ", "u")
        self.line = line
        self.out: list[str] = []
        self.offsets: list[tuple[int, int]] = []  # (python offset, user col)
        self.symbols: dict[str, _Symbol] = {}
        self.errors: list[Diagnostic] = []
        self._py_len = 0

    def _emit(self, piece: str, col: int) -> None:
        self.offsets.append((self._py_len, col))
        self.out.append(piece)
        self._py_len += len(piece)

    def _name(self, kind: str, text: str, col: int) -> str:
        name = f"n{len(self.symbols)}"
        self.symbols[name] = _Symbol(kind, text, col)
        return name

    def _error(self, col: int, message: str) -> None:
        self.errors.append(Diagnostic(self.line, col, ERROR, message))

    def run(self) -> str:
        text, i = self.text, 0
        while i < len(text) and not self.errors:
            ch = text[i]
            col = i + 1
            if ch.isspace():
                i += 1
                continue
            if ch.isdigit() or (ch == "." and i + 1 < len(text) and text[i + 1].isdigit()):
                m = _NUMBER.match(text, i)
                assert m is not None
                value = parse_number(m.group(0))
                if value is None:
                    self._error(col, f"bad number {m.group(0)!r}")
                    break
                self._emit(f" {value!r} ", col)
                i = m.end()
                continue
            if ch.isalpha() or ch == "_":
                m = _IDENT.match(text, i)
                assert m is not None
                word = m.group(0)
                i = m.end()
                j = i
                while j < len(text) and text[j].isspace():
                    j += 1
                calls = j < len(text) and text[j] == "("
                lower = word.lower()
                if calls and (lower == _VOLTAGE or lower in _CURRENTS):
                    i = self._vector(word, j, col)
                    continue
                self._emit(f" {self._name('func' if calls else 'name', word, col)} ", col)
                continue
            if text.startswith(("!=", "=="), i) or ch == "=":
                self._error(col, "'=', '==' and '!=' are not LTspice expression operators")
                break
            for op, py in _OPERATORS:
                if text.startswith(op, i):
                    self._emit(py, col)
                    i += len(op)
                    break
            else:
                self._error(col, f"unexpected character {ch!r}")
        return "".join(self.out)

    def _vector(self, head: str, open_at: int, col: int) -> int:
        """``V(a)``, ``V(a,b)``, ``I(R1)``: the inside is raw text. Returns the index
        after the closing parenthesis."""
        close = self.text.find(")", open_at + 1)
        inner = self.text[open_at + 1 : close] if close >= 0 else ""
        if close < 0 or "(" in inner:
            self._error(col, f"{head}( needs a closing ')' around node or device names")
            return len(self.text)
        args = [a.strip() for a in inner.split(",")]
        voltage = head.lower() == _VOLTAGE
        if any(not a or any(c.isspace() for c in a) for a in args) or len(args) > (
            2 if voltage else 1
        ):
            want = "one or two node names" if voltage else "one device name"
            self._error(col, f"{head}(...) takes {want}")
            return close + 1

        def ref(arg: str) -> str:
            if voltage and arg.lower() in GROUND_NODES:
                return self._name("zero", f"V({arg})", col)
            return self._name("vec", f"{head}({arg})", col)

        if len(args) == 1:
            self._emit(f" {ref(args[0])} ", col)
        else:
            self._emit(f" ({ref(args[0])} - {ref(args[1])}) ", col)
        return close + 1

    def col_at(self, py_offset: int) -> int:
        keys = [o for o, _ in self.offsets]
        k = bisect.bisect_right(keys, py_offset) - 1
        return self.offsets[k][1] if k >= 0 else 1


# ---------------------------------------------------------------- functions


def _re(x):
    return np.real(x)


def _truth(x) -> np.ndarray:
    return np.asarray(np.real(x) > 0.5)


def _num(b) -> np.ndarray:
    return np.asarray(b, dtype=float)


def _limit(x, a, b):
    lo, hi = np.minimum(_re(a), _re(b)), np.maximum(_re(a), _re(b))
    return np.minimum(np.maximum(_re(x), lo), hi)


def _hypot(x, y):
    if np.iscomplexobj(x) or np.iscomplexobj(y):
        return np.hypot(np.abs(x), np.abs(y))
    return np.hypot(x, y)


def _pow(x, y):
    return np.power(np.asarray(x) if np.iscomplexobj(x) else np.asarray(x, float), y)


# name -> (min args, max args, function, unit rule). Unit rules: "keep" = the first
# argument's unit, "same" = all arguments combined as by +, "if" = arguments 2..3
# combined, "none" = no unit. ``d`` is handled by the evaluator (it needs the axis).
_FUNCTIONS: dict[str, tuple[int, int, Callable, str]] = {
    "sin": (1, 1, np.sin, "none"),
    "cos": (1, 1, np.cos, "none"),
    "tan": (1, 1, np.tan, "none"),
    "asin": (1, 1, np.arcsin, "none"),
    "acos": (1, 1, np.arccos, "none"),
    "atan": (1, 1, np.arctan, "none"),
    "atan2": (2, 2, lambda y, x: np.arctan2(_re(y), _re(x)), "none"),
    "sinh": (1, 1, np.sinh, "none"),
    "cosh": (1, 1, np.cosh, "none"),
    "tanh": (1, 1, np.tanh, "none"),
    "ln": (1, 1, np.log, "none"),
    "log": (1, 1, np.log, "none"),  # LTspice: log() is the natural log
    "log10": (1, 1, np.log10, "none"),
    "exp": (1, 1, np.exp, "none"),
    "sqrt": (1, 1, np.sqrt, "none"),
    "pow": (2, 2, _pow, "none"),
    "abs": (1, 1, np.abs, "keep"),
    "ceil": (1, 1, lambda x: np.ceil(_re(x)), "keep"),
    "floor": (1, 1, lambda x: np.floor(_re(x)), "keep"),
    "round": (1, 1, lambda x: np.round(_re(x)), "keep"),
    "int": (1, 1, lambda x: np.trunc(_re(x)), "keep"),
    "if": (3, 3, lambda c, a, b: np.where(_truth(c), a, b), "if"),
    "limit": (3, 3, _limit, "same"),
    "min": (2, 2, lambda a, b: np.where(_re(a) <= _re(b), a, b), "same"),
    "max": (2, 2, lambda a, b: np.where(_re(a) >= _re(b), a, b), "same"),
    "hypot": (2, 2, _hypot, "same"),
    "d": (1, 1, None, "none"),
    "re": (1, 1, np.real, "keep"),
    "im": (1, 1, np.imag, "keep"),
    "ph": (1, 1, lambda x: np.degrees(np.angle(x)), "none"),
    "mag": (1, 1, np.abs, "keep"),
    "conj": (1, 1, np.conj, "keep"),
}

BUILTIN_FUNCTIONS: tuple[str, ...] = tuple(_FUNCTIONS)


# ---------------------------------------------------------------- whitelist

_BINOPS: dict[type, Callable] = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: np.divide,
    ast.Pow: _pow,
}
_COMPARE: dict[type, Callable] = {
    ast.Gt: np.greater,
    ast.Lt: np.less,
    ast.GtE: np.greater_equal,
    ast.LtE: np.less_equal,
}
_UNARY = (ast.USub, ast.UAdd, ast.Invert)


class _Reject(Exception):
    def __init__(self, node: ast.AST | None, message: str) -> None:
        super().__init__(message)
        self.node = node
        self.message = message


def check_tree(tree: ast.AST, symbols: dict[str, _Symbol]) -> None:
    """Raise _Reject unless every node of ``tree`` is on the whitelist."""

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            walk(node.body)
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BINOPS:
                raise _Reject(node, f"operator {type(node.op).__name__} is not allowed")
            walk(node.left)
            walk(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY):
                raise _Reject(node, f"operator {type(node.op).__name__} is not allowed")
            walk(node.operand)
        elif isinstance(node, ast.BoolOp):
            values = node.values
            for i, v in enumerate(values):
                if isinstance(v, ast.Name) and v.id == _XOR:
                    if not isinstance(node.op, ast.Or) or i in (0, len(values) - 1):
                        raise _Reject(v, "'^' needs a value on each side")
                    if isinstance(values[i + 1], ast.Name) and values[i + 1].id == _XOR:
                        raise _Reject(v, "'^' needs a value on each side")
                else:
                    walk(v)
        elif isinstance(node, ast.Compare):
            if any(type(op) not in _COMPARE for op in node.ops):
                raise _Reject(node, "only > < >= <= comparisons are allowed")
            walk(node.left)
            for c in node.comparators:
                walk(c)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                not isinstance(func, ast.Name)
                or func.id not in symbols
                or symbols[func.id].kind != "func"
            ):
                raise _Reject(node, "only calls of named functions are allowed")
            if node.keywords:
                raise _Reject(node, "keyword arguments are not allowed")
            for a in node.args:
                if isinstance(a, ast.Starred):
                    raise _Reject(a, "'*' arguments are not allowed")
                walk(a)
        elif isinstance(node, ast.Name):
            symbol = symbols.get(node.id)
            if symbol is None or symbol.kind == "func" or not isinstance(node.ctx, ast.Load):
                raise _Reject(node, f"name {node.id!r} is not allowed")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise _Reject(node, "only numbers are allowed as constants")
        else:
            raise _Reject(node, f"{type(node).__name__} is not allowed in an expression")

    walk(tree)


# ---------------------------------------------------------------- engine


class _EvalError(Exception):
    def __init__(self, col: int, message: str) -> None:
        super().__init__(message)
        self.col = col
        self.message = message


def _combine(a: str, b: str) -> str:
    if a == _ANY:
        return b
    if b == _ANY or a == b:
        return a
    return ""


def _mul_unit(a: str, b: str) -> str:
    if a == _ANY:
        return b
    if b == _ANY:
        return a
    if {a, b} == {"V", "A"}:
        return "W"
    return ""


class ExpressionEngine:
    """Compile and evaluate LTspice waveform expressions. ``definitions`` holds the
    user's ``.func`` and ``.param`` lines (core/waveform/plotdefs.py)."""

    def __init__(self, definitions: Definitions | None = None) -> None:
        self.definitions = definitions or Definitions()
        self._compiled: dict[tuple[str, int], tuple[Compiled | None, list[Diagnostic]]] = {}

    # ---- compile

    def compile(self, text: str, line: int = 1) -> tuple[Compiled | None, list[Diagnostic]]:
        """Parse ``text`` and check it against the whitelist and the known functions."""
        key = (text, line)
        if key not in self._compiled:
            self._compiled[key] = self._compile(text, line)
        return self._compiled[key]

    def _compile(self, text: str, line: int) -> tuple[Compiled | None, list[Diagnostic]]:
        if not text.strip():
            return None, [Diagnostic(line, 1, ERROR, "the expression is empty")]
        lexer = _Lexer(text, line)
        source = lexer.run()
        if lexer.errors:
            return None, lexer.errors
        try:
            tree = ast.parse(source.strip(), mode="eval")
        except SyntaxError as exc:
            offset = (exc.offset or 1) - 1 + (len(source) - len(source.lstrip()))
            return None, [Diagnostic(line, lexer.col_at(offset), ERROR, "syntax error")]
        try:
            check_tree(tree, lexer.symbols)
            self._check_calls(tree, lexer.symbols)
        except _Reject as exc:
            col = lexer.col_at(getattr(exc.node, "col_offset", 0)) if exc.node else 1
            return None, [Diagnostic(line, col, ERROR, exc.message)]
        return Compiled(text, tree, lexer.symbols, line), []

    def _check_calls(self, tree: ast.AST, symbols: dict[str, _Symbol]) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            symbol = symbols[node.func.id]  # type: ignore[attr-defined]
            n = len(node.args)
            user = self.definitions.funcs.get(symbol.key)
            if user is not None:
                if n != len(user.params):
                    raise _Reject(
                        node, f"{user.name}() takes {len(user.params)} argument(s), not {n}"
                    )
                continue
            spec = _FUNCTIONS.get(symbol.key)
            if spec is None:
                raise _Reject(node, f"unknown function {symbol.text!r}")
            lo, hi = spec[0], spec[1]
            if not lo <= n <= hi:
                raise _Reject(node, f"{symbol.text}() takes {lo} argument(s), not {n}")

    def check(self, text: str) -> list[Diagnostic]:
        """Syntax and function problems that do not depend on the data."""
        return self.compile(text)[1]

    def check_body(self, body: str, line: int) -> list[Diagnostic]:
        """Check a ``.func``/``.param`` body (W9) at its line in the definitions text.
        Names (parameters, vectors) are resolved when it is evaluated."""
        return self.compile(body, line)[1]

    # ---- evaluate

    def evaluate(self, text: str, data: Data) -> ExprResult:
        compiled, found = self.compile(text)
        if compiled is None:
            return ExprResult(None, "", found)
        ctx = _Context(self, data)
        try:
            with np.errstate(all="ignore"):
                values, unit = ctx.run(compiled, {})
        except _EvalError as exc:
            return ExprResult(None, "", [Diagnostic(compiled.line, exc.col, ERROR, exc.message)])
        values = np.broadcast_to(np.asarray(values), (data.size,)).copy()
        values = values.astype(complex) if data.analysis == "ac" else np.real(values).astype(float)
        return ExprResult(values, "" if unit == _ANY else unit, [])


class _Context:
    """One evaluation: caches .param values and guards recursion."""

    def __init__(self, engine: ExpressionEngine, data: Data) -> None:
        self.engine = engine
        self.data = data
        self.params: dict[str, tuple[object, str]] = {}
        self.depth = 0
        self.busy: set[str] = set()

    def run(self, compiled: Compiled, scope: dict[str, tuple[object, str]]):
        self.compiled = compiled
        return self._eval(compiled.tree.body, compiled, scope)

    # ---- names

    def _name(self, symbol: _Symbol, scope: dict[str, tuple[object, str]]):
        key = symbol.key
        if symbol.kind == "zero":
            return 0.0, "V"
        if symbol.kind == "vec":
            found = self.data.lookup(symbol.text)
            if found is None:
                raise _EvalError(symbol.col, f"unknown vector {symbol.text}")
            unit, values = found
            return values, unit or ""
        if key in scope:
            return scope[key]
        if key in self.engine.definitions.params:
            return self._param(key, symbol.col)
        if key in CONSTANTS:
            return CONSTANTS[key], _ANY
        data = self.data
        if key in ("omega", "w"):
            if data.analysis != "ac" or data.axis is None:
                raise _EvalError(symbol.col, f"{symbol.text} is only defined for an .ac run")
            return 2 * math.pi * data.axis, ""
        if key in ("time", "frequency") or (data.axis_name and key == data.axis_name.lower()):
            if data.axis is None or (key == "time" and data.analysis != "tran"):
                raise _EvalError(symbol.col, f"{symbol.text} is not the axis of this run")
            if key == "frequency" and data.analysis != "ac":
                raise _EvalError(symbol.col, f"{symbol.text} is not the axis of this run")
            return data.axis, {"tran": "s", "ac": "Hz"}.get(data.analysis, "")
        found = data.lookup(symbol.text)
        if found is not None:
            return found[1], found[0] or ""
        raise _EvalError(symbol.col, f"unknown name {symbol.text!r}")

    def _param(self, key: str, col: int):
        if key in self.params:
            return self.params[key]
        if key in self.busy or self.depth >= MAX_DEPTH:
            raise _EvalError(col, f".param {key} refers to itself")
        definition = self.engine.definitions.params[key]
        compiled, found = self.engine.compile(definition.body, definition.line)
        if compiled is None:
            raise _EvalError(col, f".param {definition.name}: {found[0].message}")
        self.busy.add(key)
        self.depth += 1
        try:
            value = self._eval(compiled.tree.body, compiled, {})
        finally:
            self.busy.discard(key)
            self.depth -= 1
        self.params[key] = value
        return value

    # ---- tree

    def _col(self, node: ast.AST, compiled: Compiled) -> int:
        if isinstance(node, ast.Name) and node.id in compiled.symbols:
            return compiled.symbols[node.id].col
        if isinstance(node, ast.Call):
            return compiled.symbols[node.func.id].col  # type: ignore[attr-defined]
        return 0

    def _eval(self, node: ast.AST, c: Compiled, scope):
        if isinstance(node, ast.Constant):
            return float(node.value), _ANY
        if isinstance(node, ast.Name):
            return self._name(c.symbols[node.id], scope)
        if isinstance(node, ast.UnaryOp):
            v, u = self._eval(node.operand, c, scope)
            if isinstance(node.op, ast.USub):
                return np.negative(v), u
            if isinstance(node.op, ast.UAdd):
                return v, u
            return _num(~_truth(v)), ""  # '!' Boolean NOT
        if isinstance(node, ast.BinOp):
            a, ua = self._eval(node.left, c, scope)
            b, ub = self._eval(node.right, c, scope)
            op = type(node.op)
            if op in (ast.Add, ast.Sub):
                unit = _combine(ua, ub)
            elif op is ast.Mult:
                unit = _mul_unit(ua, ub)
            elif op is ast.Div:
                unit = ua if ub == _ANY else ""
            else:
                unit = ""
            return _BINOPS[op](a, b), unit
        if isinstance(node, ast.Compare):
            left, _ = self._eval(node.left, c, scope)
            out = None
            for op, comp in zip(node.ops, node.comparators, strict=True):
                right, _ = self._eval(comp, c, scope)
                out = _num(_COMPARE[type(op)](_re(left), _re(right)))
                left = out  # C-style: (a < b) < c
            return out, ""
        if isinstance(node, ast.BoolOp):
            return self._boolop(node, c, scope), ""
        if isinstance(node, ast.Call):
            return self._call(node, c, scope)
        raise _EvalError(0, f"{type(node).__name__} is not allowed")  # pragma: no cover

    def _boolop(self, node: ast.BoolOp, c: Compiled, scope) -> np.ndarray:
        values = node.values
        result = _truth(self._eval(values[0], c, scope)[0])
        xor = False
        for v in values[1:]:
            if isinstance(v, ast.Name) and v.id == _XOR:
                xor = True
                continue
            b = _truth(self._eval(v, c, scope)[0])
            if isinstance(node.op, ast.And):
                result = result & b
            elif xor:
                result = result ^ b
            else:
                result = result | b
            xor = False
        return _num(result)

    def _call(self, node: ast.Call, c: Compiled, scope):
        symbol = c.symbols[node.func.id]  # type: ignore[attr-defined]
        args = [self._eval(a, c, scope) for a in node.args]
        user = self.engine.definitions.funcs.get(symbol.key)
        if user is not None:
            return self._user_call(user, args, symbol.col)
        if symbol.key not in _FUNCTIONS:  # a .func that was dropped as broken
            raise _EvalError(symbol.col, f"unknown function {symbol.text!r}")
        lo, hi, fn, rule = _FUNCTIONS[symbol.key]
        values = [a[0] for a in args]
        units = [a[1] for a in args]
        out = self._derivative(values[0], symbol.col) if symbol.key == "d" else fn(*values)
        if rule == "keep":
            unit = units[0]
        elif rule == "same":
            unit = units[0]
            for u in units[1:]:
                unit = _combine(unit, u)
        elif rule == "if":
            unit = _combine(units[1], units[2])
        else:
            unit = ""
        return out, unit

    def _derivative(self, values, col: int):
        axis = self.data.axis
        if axis is None or len(axis) < 2:
            raise _EvalError(col, "d() needs a run with an axis of at least two points")
        y = np.broadcast_to(np.asarray(values), axis.shape)
        return np.gradient(y, np.real(axis).astype(float))

    def _user_call(self, user: FuncDef, args, col: int):
        if self.depth >= MAX_DEPTH:
            raise _EvalError(col, f".func {user.name} calls itself too deeply")
        compiled, found = self.engine.compile(user.body, user.line)
        if compiled is None:
            raise _EvalError(col, f".func {user.name}: {found[0].message}")
        scope = {p.lower(): a for p, a in zip(user.params, args, strict=True)}
        self.depth += 1
        try:
            return self._eval(compiled.tree.body, compiled, scope)
        finally:
            self.depth -= 1
