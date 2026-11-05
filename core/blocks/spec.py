"""BlockSpec: the ports, settings and look of a function block. Qt-free.

A block is a Python function (or, for stateful blocks, a class) marked with ``@block``.
Its spec is built from its signature:

- parameters without a default become input ports;
- parameters with a default become settings (the default must be a plain constant:
  None, bool, int, float, str, or a list/tuple of those);
- the parameters ``t`` (simulation time) and ``dt`` (time step) are supplied by the
  engine and are not ports;
- the outputs come from the decorator (default ``["y"]``).

For a stateful class the inputs are the parameters of ``step(self, ..., dt)`` and the
settings are the parameters of ``__init__``, which must all have defaults. The class
also needs an ``output(self)`` method.

Two paths build the same spec: ``spec_from_callable`` inspects a live object (built-in
blocks, and later the block runner process), and ``core.blocks.registry`` reads source
code with ``ast`` so user code is never executed in the UI process. Both go through
``function_spec`` / ``class_spec`` so they cannot drift apart.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

SHAPES = ("square", "rectangle", "circle")
DEFAULT_OUTPUTS = ("y",)
TIME_ARGS = ("t", "dt")
OPTION_NAMES = ("shape", "outputs", "stateful", "color")

USER_COLOR = "#4a90d9"
STATEFUL_COLOR = "#e08a2e"

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SCALAR_TYPES = (bool, int, float, str, type(None))


class BlockSpecError(ValueError):
    """A block definition is invalid. ``param`` names the parameter at fault, if any."""

    def __init__(self, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


class _Empty:
    def __repr__(self) -> str:
        return "EMPTY"


EMPTY = _Empty()
"""Marks a parameter without a default."""


@dataclass(frozen=True)
class Param:
    """One parameter as written in a signature (``self`` already removed).

    ``kind`` is "normal" (positional-or-keyword or keyword-only), "posonly",
    "varargs" or "varkw".
    """

    name: str
    default: object = EMPTY
    kind: str = "normal"

    @property
    def has_default(self) -> bool:
        return self.default is not EMPTY


@dataclass(frozen=True)
class BlockOptions:
    """The arguments of ``@block``, checked and normalised."""

    shape: str = "square"
    outputs: tuple[str, ...] = DEFAULT_OUTPUTS
    stateful: bool = False
    color: str | None = None


@dataclass
class BlockSpec:
    """Everything the UI and the engine need to know about a block."""

    name: str
    inputs: list[str]
    outputs: list[str]
    settings: dict[str, object] = field(default_factory=dict)
    shape: str = "square"
    color: str = USER_COLOR
    stateful: bool = False
    doc: str = ""
    time_args: list[str] = field(default_factory=list)
    """Which of ``t`` / ``dt`` the block's function (or ``step``) asks for."""


def check_options(
    shape: object = "square",
    outputs: object = None,
    stateful: object = False,
    color: object = None,
) -> BlockOptions:
    """Validate the ``@block`` arguments. Raises BlockSpecError."""
    if shape not in SHAPES:
        raise BlockSpecError(
            f"shape must be one of {', '.join(repr(s) for s in SHAPES)}, not {shape!r}"
        )
    if outputs is None:
        outs: tuple[str, ...] = DEFAULT_OUTPUTS
    else:
        if not isinstance(outputs, list | tuple) or not all(isinstance(o, str) for o in outputs):
            raise BlockSpecError("outputs must be a list of names, e.g. outputs=['y']")
        outs = tuple(outputs)
        for name in outs:
            if not name.isidentifier():
                raise BlockSpecError(f"output name {name!r} is not a valid identifier")
        duplicates = sorted({o for o in outs if outs.count(o) > 1})
        if duplicates:
            raise BlockSpecError(f"duplicate output name(s): {', '.join(duplicates)}")
    if not isinstance(stateful, bool):
        raise BlockSpecError("stateful must be True or False")
    if color is not None and (not isinstance(color, str) or not _COLOR_RE.match(color)):
        raise BlockSpecError(f"color must look like '#rrggbb', not {color!r}")
    return BlockOptions(shape=str(shape), outputs=outs, stateful=stateful, color=color)


def is_plain_constant(value: object) -> bool:
    """True for values a setting may hold (they must survive a JSON project file)."""
    if isinstance(value, _SCALAR_TYPES):
        return True
    if isinstance(value, list | tuple):
        return all(isinstance(v, _SCALAR_TYPES) for v in value)
    return False


def _split_params(params: Sequence[Param], where: str) -> tuple[list[str], dict, list[str]]:
    inputs: list[str] = []
    settings: dict[str, object] = {}
    time_args: list[str] = []
    for p in params:
        if p.kind == "varargs":
            raise BlockSpecError(
                f"*{p.name} is not allowed in {where}: ports must be named", p.name
            )
        if p.kind == "varkw":
            raise BlockSpecError(
                f"**{p.name} is not allowed in {where}: ports must be named", p.name
            )
        if p.kind == "posonly":
            raise BlockSpecError(
                f"positional-only parameter {p.name!r} is not allowed in {where}", p.name
            )
        if p.name in TIME_ARGS:
            time_args.append(p.name)
        elif p.has_default:
            if not is_plain_constant(p.default):
                raise BlockSpecError(
                    f"default of setting {p.name!r} must be a plain constant "
                    "(number, string, bool, None or a list of those)",
                    p.name,
                )
            settings[p.name] = p.default
        else:
            inputs.append(p.name)
    return inputs, settings, time_args


def _color(options: BlockOptions) -> str:
    if options.color is not None:
        return options.color
    return STATEFUL_COLOR if options.stateful else USER_COLOR


def function_spec(
    name: str, params: Sequence[Param], options: BlockOptions, doc: str = ""
) -> BlockSpec:
    """Spec of a function block."""
    if options.stateful:
        raise BlockSpecError(
            "stateful=True needs a class with output(self) and step(self, ..., dt)"
        )
    inputs, settings, time_args = _split_params(params, "a block function")
    return BlockSpec(
        name=name,
        inputs=inputs,
        outputs=list(options.outputs),
        settings=settings,
        shape=options.shape,
        color=_color(options),
        stateful=False,
        doc=doc,
        time_args=time_args,
    )


def class_spec(
    name: str,
    init_params: Sequence[Param],
    step_params: Sequence[Param] | None,
    has_output: bool,
    options: BlockOptions,
    doc: str = "",
) -> BlockSpec:
    """Spec of a stateful class block. ``step_params`` is None if ``step`` is missing."""
    if not options.stateful:
        raise BlockSpecError("a class block needs stateful=True")
    if step_params is None:
        raise BlockSpecError(f"stateful block {name!r} needs a step(self, ..., dt) method")
    if not has_output:
        raise BlockSpecError(f"stateful block {name!r} needs an output(self) method")
    for p in init_params:
        if p.kind == "normal" and not p.has_default:
            raise BlockSpecError(
                f"__init__ parameter {p.name!r} needs a default: it becomes a setting",
                p.name,
            )
    _, settings, init_time = _split_params(init_params, "__init__")
    if init_time:
        raise BlockSpecError(f"{init_time[0]!r} is passed to step(), not __init__()", init_time[0])
    inputs, step_settings, time_args = _split_params(step_params, "step()")
    if step_settings:
        first = next(iter(step_settings))
        raise BlockSpecError(
            f"step() parameter {first!r} has a default: put settings in __init__", first
        )
    return BlockSpec(
        name=name,
        inputs=inputs,
        outputs=list(options.outputs),
        settings=settings,
        shape=options.shape,
        color=_color(options),
        stateful=True,
        doc=doc,
        time_args=time_args,
    )


# ---- live objects


_KINDS = {
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "normal",
    inspect.Parameter.KEYWORD_ONLY: "normal",
    inspect.Parameter.POSITIONAL_ONLY: "posonly",
    inspect.Parameter.VAR_POSITIONAL: "varargs",
    inspect.Parameter.VAR_KEYWORD: "varkw",
}


def params_of(func: object, skip_self: bool = False) -> list[Param]:
    """Parameters of a live callable, via ``inspect.signature``."""
    sig = inspect.signature(func)  # type: ignore[arg-type]
    params = list(sig.parameters.values())
    if skip_self and params:
        params = params[1:]
    return [
        Param(
            p.name,
            EMPTY if p.default is inspect.Parameter.empty else p.default,
            _KINDS[p.kind],
        )
        for p in params
    ]


def spec_from_callable(obj: object, options: BlockOptions) -> BlockSpec:
    """Build the spec of a live function or class."""
    name = getattr(obj, "__name__", "block")
    raw_doc = getattr(obj, "__doc__", None)
    # cleandoc of the object's own docstring, exactly like ast.get_docstring().
    doc = inspect.cleandoc(raw_doc) if isinstance(raw_doc, str) else ""
    if inspect.isclass(obj):
        own_init = "__init__" in vars(obj)
        init_params = params_of(obj.__init__, skip_self=True) if own_init else []
        step = getattr(obj, "step", None)
        step_params = params_of(step, skip_self=True) if callable(step) else None
        has_output = callable(getattr(obj, "output", None))
        return class_spec(name, init_params, step_params, has_output, options, doc)
    if not callable(obj):
        raise BlockSpecError("@block must decorate a function or a class")
    return function_spec(name, params_of(obj), options, doc)
