"""BlockRegistry: the blocks the user can place. Qt-free.

User code is only *read* here: ``ast.parse`` finds the ``@block`` functions and classes
and their specs are built from the syntax tree, never by executing the code (that
happens later, in the block runner process). Problems come back as Diagnostics with
1-based line and column numbers, like the netlist checker's.

Source code is grouped in *documents* (one per function-editor buffer). Adding a
document's code again replaces the blocks that document defined before: blocks that
are still there keep their identity, and their ports are re-matched by name so the
caller can see which ports disappeared; blocks no longer in the code are removed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass, field

from core.blocks.spec import (
    EMPTY,
    OPTION_NAMES,
    BlockOptions,
    BlockSpec,
    BlockSpecError,
    Param,
    check_options,
    class_spec,
    function_spec,
)
from core.netlist.model import ERROR, WARNING, Diagnostic

DECORATOR_NAME = "block"


@dataclass
class PortMatch:
    """How the ports of an edited block line up with its previous version."""

    kept_inputs: list[str] = field(default_factory=list)
    kept_outputs: list[str] = field(default_factory=list)
    added_inputs: list[str] = field(default_factory=list)
    added_outputs: list[str] = field(default_factory=list)
    removed_inputs: list[str] = field(default_factory=list)
    removed_outputs: list[str] = field(default_factory=list)

    @property
    def removed(self) -> list[str]:
        """Disappeared ports as readable text, e.g. ``["input b", "output diff"]``."""
        return [f"input {p}" for p in self.removed_inputs] + [
            f"output {p}" for p in self.removed_outputs
        ]


def match_ports(old: BlockSpec, new: BlockSpec) -> PortMatch:
    """Match the ports of two versions of a block by name."""

    def split(before: list[str], after: list[str]) -> tuple[list, list, list]:
        kept = [p for p in after if p in before]
        added = [p for p in after if p not in before]
        removed = [p for p in before if p not in after]
        return kept, added, removed

    ki, ai, ri = split(old.inputs, new.inputs)
    ko, ao, ro = split(old.outputs, new.outputs)
    return PortMatch(ki, ko, ai, ao, ri, ro)


@dataclass
class SourceResult:
    """What ``check_source`` / ``add_source`` found in a piece of code."""

    specs: list[BlockSpec] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    port_changes: dict[str, PortMatch] = field(default_factory=dict)
    """For blocks that existed before: how their ports were re-matched."""
    removed_blocks: list[str] = field(default_factory=list)
    """Blocks the document defined before but no longer does."""

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)

    @property
    def removed_ports(self) -> list[str]:
        """``["add_sub: input b", ...]`` for every port that disappeared."""
        return [
            f"{name}: {port}" for name, match in self.port_changes.items() for port in match.removed
        ]


class BlockRegistry:
    """Built-in blocks plus the blocks defined in user source documents."""

    def __init__(self, builtins: Iterable[BlockSpec] = ()) -> None:
        self._specs: dict[str, BlockSpec] = {}
        self._builtin: set[str] = set()
        self._owner: dict[str, str] = {}  # block name -> document
        self._sources: dict[str, str] = {}  # document -> code
        for spec in builtins:
            self.add_spec(spec, builtin=True)

    # ---- queries

    def specs(self) -> list[BlockSpec]:
        """All blocks: built-ins first, then user blocks, each in the order added."""
        builtin = [s for n, s in self._specs.items() if n in self._builtin]
        user = [s for n, s in self._specs.items() if n not in self._builtin]
        return builtin + user

    def get(self, name: str) -> BlockSpec | None:
        return self._specs.get(name)

    def is_builtin(self, name: str) -> bool:
        return name in self._builtin

    def document_of(self, name: str) -> str | None:
        """The document that defines user block ``name``."""
        return self._owner.get(name)

    def source(self, document: str) -> str:
        """The last code added for ``document`` ("" if none)."""
        return self._sources.get(document, "")

    def documents(self) -> list[str]:
        return list(self._sources)

    # ---- changes

    def add_spec(self, spec: BlockSpec, builtin: bool = False) -> None:
        """Register a spec built elsewhere (the built-in library)."""
        if spec.name in self._specs:
            raise ValueError(f"a block named {spec.name!r} already exists")
        self._specs[spec.name] = spec
        if builtin:
            self._builtin.add(spec.name)

    def check_source(self, code: str, document: str | None = None) -> SourceResult:
        """Read ``code`` and report its blocks and problems, without changing anything.

        ``document`` is the document the code would replace; its own blocks do not
        count as name clashes.
        """
        specs, diagnostics = read_source(code)
        result = SourceResult(specs=specs, diagnostics=diagnostics)
        lines = _def_lines(code)
        for spec in specs:
            owner = self._owner.get(spec.name)
            line = lines.get(spec.name, 1)
            if spec.name in self._builtin:
                result.diagnostics.append(
                    Diagnostic(line, 1, ERROR, f"a built-in block is already named {spec.name!r}")
                )
            elif owner is not None and owner != document:
                result.diagnostics.append(
                    Diagnostic(
                        line, 1, ERROR, f"another function already defines block {spec.name!r}"
                    )
                )
            elif owner is not None:
                result.port_changes[spec.name] = match_ports(self._specs[spec.name], spec)
        if document is not None:
            new_names = {s.name for s in specs}
            result.removed_blocks = [
                n for n, d in self._owner.items() if d == document and n not in new_names
            ]
        result.diagnostics.sort(key=lambda d: (d.line, d.col))
        if not result.ok:
            result.specs = []
        return result

    def add_source(self, code: str, document: str) -> SourceResult:
        """Check ``code`` and, if it has no errors, make it the code of ``document``.

        The document's previous blocks are replaced. On errors nothing changes.
        """
        result = self.check_source(code, document)
        if not result.ok:
            result.port_changes = {}
            result.removed_blocks = []
            return result
        for name in result.removed_blocks:
            self._remove_block(name)
        for spec in result.specs:
            self._specs[spec.name] = spec
            self._owner[spec.name] = document
        self._sources[document] = code
        return result

    def remove_document(self, document: str) -> list[str]:
        """Forget a document and its blocks. Returns the removed block names."""
        names = [n for n, d in self._owner.items() if d == document]
        for name in names:
            self._remove_block(name)
        self._sources.pop(document, None)
        return names

    def _remove_block(self, name: str) -> None:
        self._specs.pop(name, None)
        self._owner.pop(name, None)


# ---- reading source code


def read_source(code: str) -> tuple[list[BlockSpec], list[Diagnostic]]:
    """Find the ``@block`` definitions in ``code`` without running it."""
    try:
        tree = ast.parse(code)
    except SyntaxError as err:
        return [], [_syntax_diagnostic(code, err)]
    except ValueError as err:  # e.g. null bytes in the source
        return [], [Diagnostic(1, 1, ERROR, str(err))]

    specs: list[BlockSpec] = []
    diagnostics: list[Diagnostic] = []
    seen: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        decorator = _block_decorator(node)
        if decorator is None:
            continue
        try:
            spec = _spec_from_node(node, decorator)
        except _NodeError as err:
            diagnostics.append(Diagnostic(err.line, err.col, ERROR, err.message))
            continue
        if spec.name in seen:
            diagnostics.append(
                Diagnostic(
                    node.lineno,
                    node.col_offset + 1,
                    ERROR,
                    f"block {spec.name!r} is already defined on line {seen[spec.name]}",
                )
            )
            continue
        seen[spec.name] = node.lineno
        specs.append(spec)
    if not specs and not diagnostics:
        diagnostics.append(Diagnostic(1, 1, WARNING, "no @block function or class found"))
    return specs, diagnostics


def _syntax_diagnostic(code: str, err: SyntaxError) -> Diagnostic:
    line_count = max(1, len(code.splitlines()))
    line = min(max(err.lineno or 1, 1), line_count)
    col = max(err.offset or 1, 1) if err.lineno == line else 1
    message = err.msg or "invalid syntax"
    if isinstance(err, IndentationError):
        message = f"indentation: {message}"
    return Diagnostic(line, col, ERROR, f"syntax error: {message}")


class _NodeError(Exception):
    def __init__(self, node: ast.AST, message: str) -> None:
        super().__init__(message)
        self.line = getattr(node, "lineno", 1)
        self.col = getattr(node, "col_offset", 0) + 1
        self.message = message


def _is_block_name(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name):
        return expr.id == DECORATOR_NAME
    if isinstance(expr, ast.Attribute):
        return expr.attr == DECORATOR_NAME
    return False


def _block_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> ast.expr | None:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if _is_block_name(target):
            return dec
    return None


def _options(decorator: ast.expr) -> BlockOptions:
    if not isinstance(decorator, ast.Call):
        return check_options()
    if decorator.args:
        raise _NodeError(
            decorator.args[0], "@block takes keyword arguments only, e.g. @block(shape='circle')"
        )
    values: dict[str, object] = {}
    for kw in decorator.keywords:
        if kw.arg is None:
            raise _NodeError(kw.value, "@block does not accept **kwargs")
        if kw.arg not in OPTION_NAMES:
            raise _NodeError(
                kw.value,
                f"unknown @block argument {kw.arg!r} (use {', '.join(OPTION_NAMES)})",
            )
        try:
            values[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raise _NodeError(kw.value, f"@block argument {kw.arg!r} must be a constant") from None
    # Check each argument on its own first, so an error points at that argument.
    for kw in decorator.keywords:
        try:
            check_options(**{str(kw.arg): values[str(kw.arg)]})
        except BlockSpecError as err:
            raise _NodeError(kw.value, str(err)) from None
    return check_options(**values)


def _params(args: ast.arguments, skip_self: bool) -> tuple[list[Param], dict[str, ast.AST]]:
    """Params of a def, plus the AST node of each (for error positions)."""
    nodes: dict[str, ast.AST] = {}
    params: list[Param] = []
    positional = [*args.posonlyargs, *args.args]
    first_default = len(positional) - len(args.defaults)

    def default_of(arg: ast.arg, expr: ast.expr | None) -> object:
        if expr is None:
            return EMPTY
        try:
            return ast.literal_eval(expr)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raise _NodeError(
                expr,
                f"default of setting {arg.arg!r} must be a plain constant "
                "(number, string, bool, None or a list of those)",
            ) from None

    for i, arg in enumerate(positional):
        expr = args.defaults[i - first_default] if i >= first_default else None
        kind = "posonly" if i < len(args.posonlyargs) else "normal"
        params.append(Param(arg.arg, default_of(arg, expr), kind))
        nodes[arg.arg] = arg
    if args.vararg is not None:
        params.append(Param(args.vararg.arg, kind="varargs"))
        nodes[args.vararg.arg] = args.vararg
    for arg, expr in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        params.append(Param(arg.arg, default_of(arg, expr)))
        nodes[arg.arg] = arg
    if args.kwarg is not None:
        params.append(Param(args.kwarg.arg, kind="varkw"))
        nodes[args.kwarg.arg] = args.kwarg
    if skip_self and params:
        first = params.pop(0)
        nodes.pop(first.name, None)
    return params, nodes


def _spec_from_node(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, decorator: ast.expr
) -> BlockSpec:
    options = _options(decorator)
    doc = ast.get_docstring(node) or ""
    nodes: dict[str, ast.AST] = {}
    if isinstance(node, ast.AsyncFunctionDef):
        raise _NodeError(node, f"block {node.name!r} cannot be an async function")
    try:
        if isinstance(node, ast.FunctionDef):
            params, nodes = _params(node.args, skip_self=False)
            return function_spec(node.name, params, options, doc)
        methods = {item.name: item for item in node.body if isinstance(item, ast.FunctionDef)}
        init_params: list[Param] = []
        if "__init__" in methods:
            init_params, init_nodes = _params(methods["__init__"].args, skip_self=True)
            nodes.update(init_nodes)
        step_params: list[Param] | None = None
        if "step" in methods:
            step_params, step_nodes = _params(methods["step"].args, skip_self=True)
            nodes.update(step_nodes)
        return class_spec(node.name, init_params, step_params, "output" in methods, options, doc)
    except BlockSpecError as err:
        where: ast.AST = nodes.get(err.param, node) if err.param else node
        if where is node and "stateful=True" in str(err):
            where = decorator
        raise _NodeError(where, str(err)) from None


def _def_lines(code: str) -> dict[str, int]:
    """Line of each top-level def/class (for clash diagnostics)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return {}
    return {
        node.name: node.lineno
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
