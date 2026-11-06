"""Plot definitions: user ``.func`` and ``.param`` lines for waveform expressions (W9).

Qt-free. The text uses SPICE syntax:

    * a comment (also ``;`` and ``$``)
    .func dB(x) 20*log10(Mag(x))
    .func Pythag(x, y) {sqrt(x**2+y**2)}
    .param gain = 2  half = 0.5
    + more of the previous line (continuation)

A body may be wrapped in ``{}`` or ``''`` and ``.func`` may have an ``=`` before it.
Bodies are LTspice waveform expressions (core/waveform/expr.py) and are checked against
the full set of definitions, so a function may call one defined further down.

``parse_plotdefs`` never raises: it returns the usable definitions plus
Diagnostic(line, col, severity, message); a bad line is left out. The text is stored
in the project (``plot_defs``); ``DEFAULT_TEXT`` is what a new project starts with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.netlist.model import ERROR, WARNING, Diagnostic
from core.waveform.expr import (
    BUILTIN_FUNCTIONS,
    CONSTANTS,
    Definitions,
    ExpressionEngine,
    FuncDef,
    ParamDef,
)

DEFAULT_TEXT = (
    "* Plot definitions: .func and .param lines, as in SPICE.\n"
    "* Use them in Add Trace expressions, e.g. dB(V(out)).\n"
    ".func dB(x) 20*log10(Mag(x))\n"
)

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_FUNC = re.compile(r"\.func\s+([^\s(]+)\s*\(([^)]*)\)\s*=?\s*(.*)\Z", re.I | re.S)
_PARAM_NAME = re.compile(r"(?<![<>=!])\b([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
_RESERVED = frozenset({"v", "i", "ic", "ib", "ie", "is", "id", "ig"})


@dataclass
class PlotDefs:
    text: str
    definitions: Definitions = field(default_factory=Definitions)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)

    def engine(self) -> ExpressionEngine:
        return ExpressionEngine(self.definitions)


def _strip_body(body: str) -> str:
    body = body.strip()
    if len(body) >= 2 and (body[0], body[-1]) in (("{", "}"), ("'", "'")):
        body = body[1:-1].strip()
    return body


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """(first line number, text) with ``+`` continuations joined and comments removed."""
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        for mark in (";", "$ "):
            cut = line.find(mark)
            if cut >= 0:
                line = line[:cut].rstrip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+") and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + line[1:].strip())
        else:
            out.append((number, line))
    return out


def parse_plotdefs(text: str) -> PlotDefs:
    result = PlotDefs(text)
    defs = result.definitions
    diags = result.diagnostics

    def add(line: int, severity: str, message: str) -> None:
        diags.append(Diagnostic(line, 1, severity, message))

    def bad_name(line: int, name: str, what: str) -> bool:
        if not _NAME.match(name):
            add(line, ERROR, f"{what} name {name!r} is not a valid name")
            return True
        if name.lower() in BUILTIN_FUNCTIONS or name.lower() in _RESERVED:
            add(line, ERROR, f"{name} is a built-in name and cannot be redefined")
            return True
        return False

    for line, content in _logical_lines(text):
        word = content.split(None, 1)[0].lower()
        if word == ".func":
            m = _FUNC.match(content)
            if m is None:
                add(line, ERROR, ".func needs a name, (parameters) and a body")
                continue
            name, params_text, body = m.group(1), m.group(2), _strip_body(m.group(3))
            params = [p.strip() for p in params_text.split(",")] if params_text.strip() else []
            if bad_name(line, name, ".func"):
                continue
            if not body:
                add(line, ERROR, f".func {name} has no body")
                continue
            if any(not _NAME.match(p) for p in params) or len({p.lower() for p in params}) != len(
                params
            ):
                add(line, ERROR, f".func {name}: parameters must be distinct names")
                continue
            if name.lower() in defs.funcs:
                add(line, WARNING, f".func {name} is defined again; this line wins")
            defs.funcs[name.lower()] = FuncDef(name, params, body, line)
        elif word == ".param":
            rest = content[len(".param") :]
            matches = list(_PARAM_NAME.finditer(rest))
            if not matches or rest[: matches[0].start()].strip():
                add(line, ERROR, ".param needs name = value")
                continue
            for k, m in enumerate(matches):
                end = matches[k + 1].start() if k + 1 < len(matches) else len(rest)
                name, body = m.group(1), _strip_body(rest[m.end() : end])
                if bad_name(line, name, ".param"):
                    continue
                if not body:
                    add(line, ERROR, f".param {name} has no value")
                    continue
                if name.lower() in CONSTANTS:
                    add(line, WARNING, f".param {name} hides the constant {name}")
                if name.lower() in defs.params:
                    add(line, WARNING, f".param {name} is defined again; this line wins")
                defs.params[name.lower()] = ParamDef(name, body, line)
        elif word.startswith("."):
            add(line, WARNING, f"{word} is ignored: only .func and .param lines are read")
        else:
            add(line, WARNING, "line ignored: only .func and .param lines are read")

    # Check every body against the complete set (forward references are fine).
    engine = ExpressionEngine(defs)
    for key, func in list(defs.funcs.items()):
        found = engine.check_body(func.body, func.line)
        if found:
            diags.append(_prefixed(found[0], f".func {func.name}"))
            del defs.funcs[key]
    for key, param in list(defs.params.items()):
        found = engine.check_body(param.body, param.line)
        if found:
            diags.append(_prefixed(found[0], f".param {param.name}"))
            del defs.params[key]
    diags.sort(key=lambda d: d.line)
    return result


def _prefixed(d: Diagnostic, what: str) -> Diagnostic:
    return Diagnostic(d.line, d.col, d.severity, f"{what}: {d.message}")
