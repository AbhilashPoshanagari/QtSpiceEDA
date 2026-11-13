"""M5: user-defined physical components. Qt-free.

Users write components with the same PhysicalComponent API as the built-in library
(rule 13)::

    @physical_component
    class TCThermalResistor(PhysicalComponent):
        "Thermal resistance R0 * (1 + alpha * (T - T0)) between A and B."

        ports = (Port("A", THERMAL), Port("B", THERMAL))
        parameters = (Parameter("R0", 1.0, "K/W"), Parameter("alpha", 0.004, "1/K"))

        def network(self, ports, params):
            th = get_domain(THERMAL)
            ...
            return [Behavioural("R", ports.A, ports.B, "i", expression)]

The API names (primitives, ``Port`` ..., the domain constants, ``get_domain``,
``math``) are already defined; importing them from ``core.physical.component``,
``core.physical.domains`` or ``math`` is allowed too.

Reading (``read_component_source``) never runs the code: ``ast.parse`` finds the
``@physical_component`` classes and their specs are built from the syntax tree with
``spec_from_declarations``, the function ``@physical_component`` itself uses. It checks

- the top level holds only imports of the names above, constants, helper functions
  and ``@physical_component`` classes deriving from ``PhysicalComponent``;
- ``ports``, ``parameters``, ``internal`` and ``role`` are literal declarations and
  every conserving ``Port`` names one of the four domains (typed ports);
- ``network(self, ports, params)`` exists, and code calls only the allowed primitives,
  ``get_domain`` and Domain methods, ``math`` functions, a short list of builtins and
  helpers defined in the same code; no ``eval``/``exec``/``open``/``__import__``,
  no names or attributes starting with ``_``, no ``global``/``nonlocal``/``with``.
  ``SubcircuitInstance`` (M2) and ``compose`` are for built-in parts only.

These rules catch mistakes and keep the code to the component API; they are not a
security sandbox, as for v0.1 function blocks. The code runs only in the block-runner
process (``load_components``, rule 5), with only the API and the allowed builtins in
its namespace. ``build_network`` then checks the primitives ``network()`` returns
(rule 6). Problems come back as Diagnostics with 1-based line and column.
"""

from __future__ import annotations

import ast
import builtins
import linecache
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from core.netlist.model import ERROR, WARNING, Diagnostic
from core.physical import component as api
from core.physical import domains as _domains
from core.physical.component import (
    COMPONENT,
    Parameter,
    PhysicalComponentError,
    PhysicalSpec,
    Port,
    SignalInput,
    SignalOutput,
    physical_spec_of,
    spec_from_declarations,
)
from core.physical.domains import DOMAINS, ELECTRICAL, ROTATIONAL, THERMAL, TRANSLATIONAL
from core.physical.domains import get_domain as _get_domain
from core.physical.registry import PhysicalRegistry

DECORATOR_NAME = "physical_component"
BASE_NAME = "PhysicalComponent"
USER_GROUP = "User components"

PRIMITIVES: tuple[str, ...] = (
    "Resistor",
    "Capacitor",
    "Inductor",
    "VoltageSource",
    "CurrentSource",
    "VCVS",
    "VCCS",
    "CCCS",
    "CCVS",
    "Behavioural",
    "ExternalSource",
    "Sensor",
    "Crossing",
)
"""The primitives a user component may build."""

_DECLARATIONS = ("Port", "SignalInput", "SignalOutput", "Parameter")
_COMPONENT_NAMES = (
    "PhysicalComponent",
    "physical_component",
    *_DECLARATIONS,
    *PRIMITIVES,
    "Sine",
    "Pulse",
    "GROUND",
)
DOMAIN_CONSTANTS: dict[str, str] = {
    "ELECTRICAL": ELECTRICAL,
    "ROTATIONAL": ROTATIONAL,
    "TRANSLATIONAL": TRANSLATIONAL,
    "THERMAL": THERMAL,
}
_DOMAIN_NAMES = (*DOMAIN_CONSTANTS, "get_domain")
ALLOWED_IMPORTS: dict[str, tuple[str, ...]] = {
    "core.physical.component": _COMPONENT_NAMES,
    "core.physical.domains": _DOMAIN_NAMES,
    "math": tuple(n for n in dir(math) if not n.startswith("_")),
}

SAFE_BUILTINS: tuple[str, ...] = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "float",
    "int",
    "isinstance",
    "len",
    "list",
    "max",
    "min",
    "pow",
    "range",
    "repr",
    "round",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "ValueError",
    "TypeError",
    "KeyError",
    "ZeroDivisionError",
)
"""Builtins user component code may use (the runner's namespace has only these)."""

_CALLABLE = frozenset((*_COMPONENT_NAMES, "get_domain", *SAFE_BUILTINS))
_NOT_FOR_USERS = {
    "SubcircuitInstance": "SubcircuitInstance is for Circuit blocks (M2), not user components",
    "compose": "compose() is for built-in composite parts, not user components",
}
_FORBIDDEN_NODES: dict[type, str] = {
    ast.Import: "import",
    ast.ImportFrom: "import",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
    ast.ClassDef: "a nested class",
    ast.AsyncFunctionDef: "async def",
    ast.Await: "await",
    ast.Yield: "yield",
    ast.YieldFrom: "yield from",
    ast.With: "with",
    ast.AsyncWith: "async with",
    ast.AsyncFor: "async for",
}


def _api_namespace() -> dict[str, object]:
    names: dict[str, object] = {n: getattr(api, n) for n in _COMPONENT_NAMES}
    names.update({n: v for n, v in DOMAIN_CONSTANTS.items()})
    names["get_domain"] = _get_domain
    names["math"] = math
    return names


# ---- reading source code


def read_component_source(code: str) -> tuple[list[PhysicalSpec], list[Diagnostic]]:
    """Find the ``@physical_component`` classes in ``code`` without running it."""
    try:
        tree = ast.parse(code)
    except SyntaxError as err:
        return [], [_syntax_diagnostic(code, err)]
    except ValueError as err:  # e.g. null bytes in the source
        return [], [Diagnostic(1, 1, ERROR, str(err))]
    reader = _Reader(tree)
    reader.read()
    diagnostics = sorted(reader.diagnostics, key=lambda d: (d.line, d.col))
    if not reader.specs and not diagnostics and code.strip():
        diagnostics.append(Diagnostic(1, 1, WARNING, "no @physical_component class found"))
    if any(d.severity == ERROR for d in diagnostics):
        return [], diagnostics
    return reader.specs, diagnostics


def _syntax_diagnostic(code: str, err: SyntaxError) -> Diagnostic:
    line_count = max(1, len(code.splitlines()))
    line = min(max(err.lineno or 1, 1), line_count)
    col = max(err.offset or 1, 1) if err.lineno == line else 1
    message = err.msg or "invalid syntax"
    if isinstance(err, IndentationError):
        message = f"indentation: {message}"
    return Diagnostic(line, col, ERROR, f"syntax error: {message}")


class _Bad(Exception):
    def __init__(self, node: ast.AST, message: str) -> None:
        super().__init__(message)
        self.node = node
        self.message = message


def _where(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1


class _Reader:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.specs: list[PhysicalSpec] = []
        self.diagnostics: list[Diagnostic] = []
        self.helpers = {
            n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name[:1] != "_"
        }
        self.constants: dict[str, object] = {}

    def error(self, node: ast.AST, message: str) -> None:
        line, col = _where(node)
        self.diagnostics.append(Diagnostic(line, col, ERROR, message))

    # ---- top level

    def read(self) -> None:
        seen: dict[str, int] = {}
        for index, node in enumerate(self.tree.body):
            try:
                if index == 0 and _is_docstring(node):
                    continue
                if isinstance(node, ast.Import | ast.ImportFrom):
                    self._import(node)
                elif isinstance(node, ast.Assign):
                    self._constant(node)
                elif isinstance(node, ast.FunctionDef):
                    self._check_code(node)
                elif isinstance(node, ast.ClassDef):
                    spec = self._component(node)
                    if spec.name in seen:
                        raise _Bad(
                            node,
                            f"component {spec.name!r} is already defined on line {seen[spec.name]}",
                        )
                    seen[spec.name] = node.lineno
                    self.specs.append(spec)
                else:
                    raise _Bad(
                        node,
                        "only imports, constants, helper functions and @physical_component "
                        "classes are allowed at the top level",
                    )
            except _Bad as bad:
                self.error(bad.node, bad.message)

    def _import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name != "math":
                    raise _Bad(node, f"cannot import {alias.name!r}: only math (and the API)")
            return
        allowed = ALLOWED_IMPORTS.get(node.module or "") if node.level == 0 else None
        if allowed is None:
            raise _Bad(
                node,
                f"cannot import from {node.module!r}: use core.physical.component, "
                "core.physical.domains or math",
            )
        for alias in node.names:
            if alias.name not in allowed:
                raise _Bad(node, f"{node.module} has no {alias.name!r} for user components")

    def _constant(self, node: ast.Assign) -> None:
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id.startswith("_"):
                raise _Bad(target, "a top-level assignment must set a plain name")
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raise _Bad(node.value, "a top-level value must be a constant") from None
        for target in node.targets:
            self.constants[target.id] = value  # type: ignore[union-attr]

    # ---- a component class

    def _component(self, node: ast.ClassDef) -> PhysicalSpec:
        name = self._decorator(node)
        if (
            len(node.bases) != 1
            or not isinstance(node.bases[0], ast.Name)
            or node.bases[0].id != BASE_NAME
            or node.keywords
        ):
            raise _Bad(node, f"class {node.name!r} must derive from {BASE_NAME} only")
        decl: dict[str, object] = {}
        has_network = False
        for index, item in enumerate(node.body):
            if index == 0 and _is_docstring(item):
                continue
            if isinstance(item, ast.Assign):
                if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                    raise _Bad(item, "class attributes are single names: ports = (...)")
                attr = item.targets[0].id
                if attr == "ports":
                    decl["ports"] = self._ports(item.value)
                elif attr == "parameters":
                    decl["parameters"] = self._parameters(item.value)
                elif attr == "internal":
                    decl["internal"] = self._internal(item.value)
                elif attr == "role":
                    if self._literal(item.value) != COMPONENT:
                        raise _Bad(item.value, "a user component has role 'component'")
                else:
                    raise _Bad(item, f"unknown class attribute {attr!r}")
            elif isinstance(item, ast.FunctionDef):
                if item.decorator_list:
                    raise _Bad(item.decorator_list[0], "methods take no decorators")
                if item.name == "network":
                    self._network_signature(item)
                    has_network = True
                elif item.name.startswith("_"):
                    raise _Bad(item, f"method names must not start with '_': {item.name!r}")
                self._check_code(item)
            else:
                raise _Bad(item, "a component class holds only its declarations and methods")
        if not has_network:
            raise _Bad(node, f"{node.name} must implement network(self, ports, params)")
        try:
            return spec_from_declarations(
                name,
                decl.get("ports", ()),  # type: ignore[arg-type]
                decl.get("parameters", ()),  # type: ignore[arg-type]
                decl.get("internal", {}),  # type: ignore[arg-type]
                COMPONENT,
                ast.get_docstring(node) or "",
            )
        except PhysicalComponentError as err:
            raise _Bad(node, str(err)) from None

    def _decorator(self, node: ast.ClassDef) -> str:
        found = None
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name) and target.id == DECORATOR_NAME:
                found = dec
            else:
                raise _Bad(dec, f"a component class takes only @{DECORATOR_NAME}")
        if found is None:
            raise _Bad(node, f"class {node.name!r} needs @{DECORATOR_NAME}")
        if not isinstance(found, ast.Call):
            return node.name
        if found.args or any(kw.arg != "name" for kw in found.keywords):
            raise _Bad(found, f"@{DECORATOR_NAME} takes only name='...'")
        name = self._literal(found.keywords[0].value) if found.keywords else node.name
        if not isinstance(name, str):
            raise _Bad(found, f"@{DECORATOR_NAME}(name=...) must be a string")
        return name

    def _network_signature(self, node: ast.FunctionDef) -> None:
        args = node.args
        names = [a.arg for a in args.posonlyargs + args.args]
        if (
            len(names) != 3
            or names[0] != "self"
            or args.vararg
            or args.kwarg
            or args.kwonlyargs
            or args.defaults
        ):
            raise _Bad(node, "network must be defined as network(self, ports, params)")

    # ---- literal declarations

    def _literal(self, expr: ast.expr) -> object:
        if isinstance(expr, ast.Name) and expr.id in self.constants:
            return self.constants[expr.id]
        try:
            return ast.literal_eval(expr)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raise _Bad(expr, "must be a constant (number, string or bool)") from None

    def _domain(self, expr: ast.expr) -> str:
        if isinstance(expr, ast.Name) and expr.id in DOMAIN_CONSTANTS:
            return DOMAIN_CONSTANTS[expr.id]
        value = self._literal(expr)
        if not isinstance(value, str) or value not in DOMAINS:
            raise _Bad(
                expr,
                f"unknown domain {value!r}: use {', '.join(DOMAIN_CONSTANTS)}",
            )
        return value

    def _items(self, expr: ast.expr, what: str) -> list[ast.expr]:
        if not isinstance(expr, ast.Tuple | ast.List):
            raise _Bad(expr, f"{what} must be a tuple or list written out in the class")
        return list(expr.elts)

    def _call(self, expr: ast.expr, allowed: tuple[str, ...]) -> tuple[str, ast.Call]:
        if (
            not isinstance(expr, ast.Call)
            or not isinstance(expr.func, ast.Name)
            or expr.func.id not in allowed
        ):
            raise _Bad(expr, f"expected {' / '.join(allowed)}(...)")
        return expr.func.id, expr

    def _ports(self, expr: ast.expr) -> list[object]:
        ports: list[object] = []
        for item in self._items(expr, "ports"):
            kind, call = self._call(item, ("Port", "SignalInput", "SignalOutput"))
            fields = (
                ("name", "domain", "label", "optional")
                if kind == "Port"
                else (
                    "name",
                    "unit",
                    "label",
                )
            )
            values = self._arguments(call, fields, {"domain"} if kind == "Port" else set())
            if kind == "Port":
                if "domain" not in values:
                    raise _Bad(call, "a conserving Port needs a domain, e.g. Port('A', THERMAL)")
                ports.append(Port(**values))  # type: ignore[arg-type]
            elif kind == "SignalInput":
                ports.append(SignalInput(**values))  # type: ignore[arg-type]
            else:
                ports.append(SignalOutput(**values))  # type: ignore[arg-type]
        return ports

    def _parameters(self, expr: ast.expr) -> list[object]:
        out: list[object] = []
        for item in self._items(expr, "parameters"):
            _, call = self._call(item, ("Parameter",))
            values = self._arguments(call, ("name", "default", "unit", "label"), set())
            if "default" not in values:
                raise _Bad(call, "a Parameter needs a default, e.g. Parameter('R', 1.0, 'K/W')")
            out.append(Parameter(**values))  # type: ignore[arg-type]
        return out

    def _internal(self, expr: ast.expr) -> dict[str, str]:
        if not isinstance(expr, ast.Dict):
            raise _Bad(expr, "internal must be a dict written out in the class")
        out: dict[str, str] = {}
        for key, value in zip(expr.keys, expr.values, strict=True):
            if key is None:
                raise _Bad(value, "internal cannot use **")
            name = self._literal(key)
            if not isinstance(name, str):
                raise _Bad(key, "internal node names are strings")
            out[name] = self._domain(value)
        return out

    def _arguments(
        self, call: ast.Call, fields: tuple[str, ...], domains: set[str]
    ) -> dict[str, object]:
        if len(call.args) > len(fields):
            raise _Bad(call, f"too many arguments (fields: {', '.join(fields)})")
        values: dict[str, object] = {}
        pairs: list[tuple[str, ast.expr]] = list(zip(fields, call.args, strict=False))
        for kw in call.keywords:
            if kw.arg is None or kw.arg not in fields:
                raise _Bad(kw.value, f"unknown argument {kw.arg!r} (fields: {', '.join(fields)})")
            pairs.append((kw.arg, kw.value))
        for name, expr in pairs:
            if name in values:
                raise _Bad(expr, f"argument {name!r} is given twice")
            values[name] = self._domain(expr) if name in domains else self._literal(expr)
        return values

    # ---- code

    def _check_code(self, func: ast.FunctionDef) -> None:
        if func.decorator_list:
            raise _Bad(func.decorator_list[0], "functions take no decorators")
        for node in ast.walk(func):
            if node is func:
                continue
            for kind, what in _FORBIDDEN_NODES.items():
                if isinstance(node, kind):
                    raise _Bad(node, f"{what} is not allowed in component code")
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise _Bad(
                    node, f"attribute {node.attr!r}: names starting with '_' are not allowed"
                )
            if isinstance(node, ast.Name):
                problem = _name_problem(node.id, isinstance(node.ctx, ast.Load))
                if problem:
                    raise _Bad(node, problem)
            if isinstance(node, ast.Call):
                self._check_call(node)

    def _check_call(self, call: ast.Call) -> None:
        func = call.func
        if isinstance(func, ast.Name):
            problem = _name_problem(func.id, True)
            if problem:  # the specific message, before the general one
                raise _Bad(func, problem)
            if func.id not in _CALLABLE and func.id not in self.helpers:
                raise _Bad(
                    func,
                    f"cannot call {func.id!r}: component code may call the primitives "
                    f"({', '.join(PRIMITIVES)}), get_domain, math functions, "
                    "basic builtins and helpers defined here",
                )
            return
        if isinstance(func, ast.Attribute):
            is_math = isinstance(func.value, ast.Name) and func.value.id == "math"
            if is_math and func.attr not in ALLOWED_IMPORTS["math"]:
                raise _Bad(func, f"math has no function {func.attr!r}")
            return  # methods: Domain, str, list ... (no '_' names, checked above)
        raise _Bad(func, "only named functions and methods may be called")


def _name_problem(name: str, load: bool) -> str:
    """Why component code may not use ``name`` ("" if it may)."""
    if name.startswith("__"):
        return f"name {name!r}: names starting with '__' are not allowed"
    if name in _NOT_FOR_USERS:
        return _NOT_FOR_USERS[name]
    if load and hasattr(builtins, name) and name not in _CALLABLE and not _is_value(name):
        return f"{name!r} is not available in component code"
    return ""


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_value(name: str) -> bool:
    """Builtin names that are values, not functions (True, False, None ...)."""
    return name in ("True", "False", "None", "NotImplemented", "Ellipsis")


# ---- documents in the UI process


@dataclass
class ComponentSourceResult:
    """What ``UserComponents.check_source`` / ``add_source`` found in a document."""

    specs: list[PhysicalSpec] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    """Components the document defined before but no longer does."""

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)


class UserComponents:
    """The user component documents, read without running them.

    Their specs are added to ``registry`` (a PhysicalRegistry) as spec-only entries:
    the UI process knows their ports and settings but has no class to build a network
    with. ``taken(name)`` reports names used elsewhere (e.g. signal blocks).
    """

    def __init__(
        self, registry: PhysicalRegistry, taken: Callable[[str], bool] | None = None
    ) -> None:
        self._registry = registry
        self._taken: Callable[[str], bool] = taken or (lambda _name: False)
        self._owner: dict[str, str] = {}  # component name -> document
        self._sources: dict[str, str] = {}  # document -> code

    @property
    def registry(self) -> PhysicalRegistry:
        return self._registry

    def documents(self) -> list[str]:
        return list(self._sources)

    def source(self, document: str) -> str:
        return self._sources.get(document, "")

    def sources(self) -> dict[str, str]:
        return dict(self._sources)

    def document_of(self, name: str) -> str | None:
        return self._owner.get(name)

    def names(self) -> list[str]:
        return list(self._owner)

    def check_source(self, code: str, document: str | None = None) -> ComponentSourceResult:
        specs, diagnostics = read_component_source(code)
        result = ComponentSourceResult(specs=specs, diagnostics=diagnostics)
        lines = _class_lines(code)
        for spec in specs:
            owner = self._owner.get(spec.name)
            line = lines.get(spec.name, 1)
            if owner is None and spec.name in self._registry:
                result.diagnostics.append(
                    Diagnostic(
                        line, 1, ERROR, f"a built-in component is already named {spec.name!r}"
                    )
                )
            elif owner is not None and owner != document:
                result.diagnostics.append(
                    Diagnostic(line, 1, ERROR, f"another document already defines {spec.name!r}")
                )
            elif self._taken(spec.name):
                result.diagnostics.append(
                    Diagnostic(line, 1, ERROR, f"a block is already named {spec.name!r}")
                )
        if document is not None:
            new = {s.name for s in specs}
            result.removed = [n for n, d in self._owner.items() if d == document and n not in new]
        result.diagnostics.sort(key=lambda d: (d.line, d.col))
        if not result.ok:
            result.specs = []
        return result

    def add_source(self, code: str, document: str) -> ComponentSourceResult:
        """Check ``code``; without errors it becomes the code of ``document``."""
        result = self.check_source(code, document)
        if not result.ok:
            result.removed = []
            return result
        for name in result.removed + [s.name for s in result.specs if s.name in self._owner]:
            self._registry.remove(name)
            self._owner.pop(name, None)
        for spec in result.specs:
            self._registry.add_spec(spec)
            self._owner[spec.name] = document
        self._sources[document] = code
        return result

    def remove_document(self, document: str) -> list[str]:
        names = [n for n, d in self._owner.items() if d == document]
        for name in names:
            self._registry.remove(name)
            self._owner.pop(name)
        self._sources.pop(document, None)
        return names


def _class_lines(code: str) -> dict[str, int]:
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = node.lineno
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            out[str(kw.value.value)] = node.lineno
    return out


# ---- loading in the runner process


class ComponentLoadError(ValueError):
    """User component code cannot be loaded."""


def load_components(sources: Mapping[str, str]) -> list[type]:
    """Run each document's code and return its component classes.

    Runs user code: call this only in the block-runner process. The code is checked
    again first (it never runs with errors), and each class's spec must equal the one
    read from its source. Raises ComponentLoadError.
    """
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS}
    safe["__build_class__"] = builtins.__build_class__  # class statements need it
    # Imports are checked to name only the API; make them resolve to it.
    safe["__import__"] = _api_import
    classes: list[type] = []
    for index, (document, code) in enumerate(sources.items()):
        specs, diagnostics = read_component_source(code)
        errors = [d for d in diagnostics if d.severity == ERROR]
        if errors:
            first = errors[0]
            raise ComponentLoadError(
                f"component document {document}: line {first.line}: {first.message}"
            )
        filename = f"<component code {document}>"
        linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
        module = f"qtspice_component_{index}"
        namespace: dict[str, object] = {
            "__builtins__": safe,
            "__name__": module,
            **_api_namespace(),
        }
        exec(compile(code, filename, "exec"), namespace)  # runs user component code
        found = {
            spec.name: value
            for value in namespace.values()
            if (spec := physical_spec_of(value)) is not None
            and getattr(value, "__module__", None) == module
        }
        for spec in specs:
            cls = found.get(spec.name)
            if cls is None or physical_spec_of(cls) != spec:
                raise ComponentLoadError(
                    f"component document {document}: {spec.name} does not match its source"
                )
            classes.append(cls)
    return classes


_IMPORTABLE = {
    "core.physical.component": api,
    "core.physical.domains": _domains,
    "math": math,
}


def _api_import(
    name: str,
    globals: object = None,  # noqa: A002
    locals: object = None,  # noqa: A002
    fromlist: Iterable[str] = (),
    level: int = 0,
) -> object:
    """``__import__`` for user component code: only the modules the reader allows."""
    module = _IMPORTABLE.get(name)
    if module is None or level:
        raise ImportError(f"user components cannot import {name!r}")
    return module
