"""PhysicalComponent: the API for physical blocks. Qt-free.

A component declares typed ports, parameters with units and defaults, and turns
them into circuit *primitives*::

    @physical_component
    class Damper(PhysicalComponent):
        "Rotational viscous damper between shafts A and B."

        ports = (Port("A", ROTATIONAL), Port("B", ROTATIONAL))
        parameters = (Parameter("b", 0.01, "N*m*s/rad", "damping"),)

        def network(self, ports, params):
            r = get_domain(ROTATIONAL).resistance_from(params["b"])
            return [Resistor("R", ports.A, ports.B, r)]

Primitives refer to nodes by *name*: a conserving port, an internal node declared in
``internal``, or ``GROUND`` (every domain's reference is 0, so they all share the
circuit's node 0). The mapper (F6) assigns real circuit nodes later. Element values
come from the Domain registry: a component never hard-codes "R = 1/b" (rule 12).

Built-in components use exactly this API, the same one users get in M5 (rule 13).

``role`` tells the network checks (F5) what a component is for:

- ``"component"`` (the default): an ordinary part;
- ``"reference"``: a domain reference (ground). It has exactly one conserving
  port; the mapper (F6) puts that port's node on circuit node 0;
- ``"solver"``: the Solver Configuration. It has exactly one conserving port,
  usually of domain ``ANY_DOMAIN`` so it joins a network of any domain. Every
  physical network needs exactly one.

Only a ``"solver"`` component may have an ``ANY_DOMAIN`` port.

Primitive sign conventions are those of SPICE:

- two-terminal elements: current is positive from ``a`` through the element to
  ``b``, i.e. positive into node ``a`` -- the through variable of the component's
  port A (see ``core.physical.domains``);
- ``VoltageSource``: V(a) - V(b) = value;
- ``CurrentSource``: ``value`` flows from ``a`` through the source to ``b``.

``build_network`` and ``check_network`` never raise on bad input: they return the
primitives they could build plus ``NetworkDiagnostic`` entries (rule 6). The
decorator raises ``PhysicalComponentError`` for a broken declaration, like ``@block``.
"""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from core.blocks.spec import CONSERVING, BlockSpec, PortSpec, is_plain_constant
from core.netlist.model import ERROR, WARNING
from core.physical.domains import ANY_DOMAIN, DOMAINS

GROUND = "0"
"""Name of the reference node, shared by all domains (every reference value is 0)."""

SPEC_ATTR = "__physical_spec__"

SIGNAL_IN = "input"
SIGNAL_OUT = "output"
PORT_ROLES = (CONSERVING, SIGNAL_IN, SIGNAL_OUT)

COMPONENT = "component"
REFERENCE = "reference"
SOLVER = "solver"
COMPONENT_ROLES = (COMPONENT, REFERENCE, SOLVER)

_ANY_COLOR = "#4a5568"


class PhysicalComponentError(ValueError):
    """A component declaration is invalid."""


# ---- declarations


@dataclass(frozen=True)
class Port:
    """A conserving port of ``domain``."""

    name: str
    domain: str
    label: str = ""

    @property
    def role(self) -> str:
        return CONSERVING


@dataclass(frozen=True)
class SignalInput:
    """A signal input port; external sources read their value from one."""

    name: str
    unit: str = ""
    label: str = ""

    @property
    def role(self) -> str:
        return SIGNAL_IN


@dataclass(frozen=True)
class SignalOutput:
    """A signal output port (sensor values; their primitive comes with the sensors)."""

    name: str
    unit: str = ""
    label: str = ""

    @property
    def role(self) -> str:
        return SIGNAL_OUT


PortDecl = Port | SignalInput | SignalOutput


@dataclass(frozen=True)
class Parameter:
    """A component parameter. ``default`` and values are SI, in ``unit``."""

    name: str
    default: float | int | bool | str
    unit: str = ""
    label: str = ""


# ---- primitives


@dataclass(frozen=True)
class Resistor:
    name: str
    a: str
    b: str
    value: float


@dataclass(frozen=True)
class Capacitor:
    name: str
    a: str
    b: str
    value: float
    ic: float | None = None
    """Initial voltage V(a) - V(b), or None for the operating point."""


@dataclass(frozen=True)
class Inductor:
    name: str
    a: str
    b: str
    value: float
    ic: float | None = None
    """Initial current from a to b, or None for the operating point."""


@dataclass(frozen=True)
class VoltageSource:
    name: str
    a: str
    b: str
    value: float


@dataclass(frozen=True)
class CurrentSource:
    name: str
    a: str
    b: str
    value: float


@dataclass(frozen=True)
class VCVS:
    """V(a) - V(b) = gain * (V(ctrl_a) - V(ctrl_b)). SPICE E element."""

    name: str
    a: str
    b: str
    ctrl_a: str
    ctrl_b: str
    gain: float


@dataclass(frozen=True)
class VCCS:
    """gain * (V(ctrl_a) - V(ctrl_b)) flows from a through the source to b. SPICE G."""

    name: str
    a: str
    b: str
    ctrl_a: str
    ctrl_b: str
    gain: float


@dataclass(frozen=True)
class CCCS:
    """gain * I(control) flows from a through the source to b. SPICE F element.

    ``control`` names a ``VoltageSource`` primitive of the same component.
    """

    name: str
    a: str
    b: str
    control: str
    gain: float


@dataclass(frozen=True)
class CCVS:
    """V(a) - V(b) = gain * I(control). SPICE H element.

    ``control`` names a ``VoltageSource`` primitive of the same component.
    """

    name: str
    a: str
    b: str
    control: str
    gain: float


BEHAVIOURAL_KINDS = ("v", "i")


@dataclass(frozen=True)
class Behavioural:
    """A behavioural source (SPICE B element).

    ``kind`` "v": V(a) - V(b) = expression; "i": expression flows from a to b.
    The expression uses ``V(node)``, ``V(node1,node2)`` and ``I(source)`` with the
    component's own node and primitive names; the mapper rewrites them.
    """

    name: str
    a: str
    b: str
    kind: str
    expression: str


@dataclass(frozen=True)
class ExternalSource:
    """A source whose value comes from signal input ``signal`` at every tick.

    Becomes an ngspice ``external`` source in the combined run (C1). ``kind`` "v" or
    "i", with the sign conventions of VoltageSource / CurrentSource.
    """

    name: str
    a: str
    b: str
    kind: str
    signal: str
    initial: float = 0.0


Primitive = (
    Resistor
    | Capacitor
    | Inductor
    | VoltageSource
    | CurrentSource
    | VCVS
    | VCCS
    | CCCS
    | CCVS
    | Behavioural
    | ExternalSource
)
PRIMITIVE_TYPES = (
    Resistor,
    Capacitor,
    Inductor,
    VoltageSource,
    CurrentSource,
    VCVS,
    VCCS,
    CCCS,
    CCVS,
    Behavioural,
    ExternalSource,
)


# ---- the base class


class Ports:
    """Node names handed to ``network``: ``ports.A == "A"``, ``ports.ground == "0"``.

    Covers conserving ports and internal nodes; a typo raises AttributeError, so it
    shows up as a network diagnostic instead of a silently new node.
    """

    def __init__(self, names: Sequence[str]) -> None:
        self._names = tuple(names)

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._names:
            return name
        raise AttributeError(f"no port or internal node named {name!r}")

    def __getitem__(self, name: str) -> str:
        return self.__getattr__(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    @property
    def ground(self) -> str:
        return GROUND


class PhysicalComponent:
    """Base class of every physical component, built-in or user-defined."""

    ports: ClassVar[Sequence[PortDecl]] = ()
    parameters: ClassVar[Sequence[Parameter]] = ()
    internal: ClassVar[Mapping[str, str]] = {}
    """Internal node name -> domain."""
    role: ClassVar[str] = COMPONENT
    """"component", "reference" or "solver" (see the module docstring)."""

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        raise NotImplementedError


@dataclass(frozen=True)
class PhysicalSpec:
    """What ``@physical_component`` found in a class."""

    name: str
    ports: tuple[PortDecl, ...]
    parameters: tuple[Parameter, ...]
    internal: dict[str, str]
    doc: str
    block: BlockSpec
    """The component as a block in the v0.1 engine (conserving ports included)."""
    role: str = COMPONENT

    def node_domains(self) -> dict[str, str]:
        """Domain of every named node: conserving ports, then internal nodes."""
        nodes = {p.name: p.domain for p in self.ports if isinstance(p, Port)}
        nodes.update(self.internal)
        return nodes

    def defaults(self) -> dict[str, object]:
        return {p.name: p.default for p in self.parameters}

    def signal_inputs(self) -> list[str]:
        return [p.name for p in self.ports if isinstance(p, SignalInput)]


def _check_name(name: object, what: str) -> None:
    if not isinstance(name, str) or not name.isidentifier():
        raise PhysicalComponentError(f"{what} name {name!r} is not a valid identifier")


def component_spec(cls: type, name: str | None = None) -> PhysicalSpec:
    """Check a component class and build its spec. Raises PhysicalComponentError."""
    if not isinstance(cls, type) or not issubclass(cls, PhysicalComponent):
        raise PhysicalComponentError("@physical_component must decorate a PhysicalComponent")
    if cls.network is PhysicalComponent.network:
        raise PhysicalComponentError(f"{cls.__name__} must implement network(ports, params)")
    comp_name = name or cls.__name__
    _check_name(comp_name, "component")

    ports = tuple(cls.ports)
    parameters = tuple(cls.parameters)
    internal = dict(cls.internal)
    seen: set[str] = set()

    def claim(n: str, what: str) -> None:
        _check_name(n, what)
        if n == GROUND or n in seen:
            raise PhysicalComponentError(f"{comp_name}: name {n!r} is used twice")
        seen.add(n)

    for port in ports:
        if not isinstance(port, Port | SignalInput | SignalOutput):
            raise PhysicalComponentError(f"{comp_name}: {port!r} is not a port declaration")
        claim(port.name, "port")
        if isinstance(port, Port) and port.domain == ANY_DOMAIN:
            if getattr(cls, "role", COMPONENT) != SOLVER:
                raise PhysicalComponentError(
                    f"{comp_name}: only a role='solver' component may have a port of "
                    f"domain {ANY_DOMAIN!r}"
                )
        elif isinstance(port, Port) and port.domain not in DOMAINS:
            raise PhysicalComponentError(
                f"{comp_name}: port {port.name!r} has unknown domain {port.domain!r}"
            )
    if not any(isinstance(p, Port) for p in ports):
        raise PhysicalComponentError(f"{comp_name} needs at least one conserving Port")
    role = getattr(cls, "role", COMPONENT)
    if role not in COMPONENT_ROLES:
        raise PhysicalComponentError(
            f"{comp_name}: role must be one of {', '.join(COMPONENT_ROLES)}, not {role!r}"
        )
    if role != COMPONENT and sum(isinstance(p, Port) for p in ports) != 1:
        raise PhysicalComponentError(
            f"{comp_name}: a {role} component has exactly one conserving Port"
        )
    for node, domain in internal.items():
        claim(node, "internal node")
        if domain not in DOMAINS:
            raise PhysicalComponentError(
                f"{comp_name}: internal node {node!r} has unknown domain {domain!r}"
            )
    param_names: set[str] = set()
    for param in parameters:
        if not isinstance(param, Parameter):
            raise PhysicalComponentError(f"{comp_name}: {param!r} is not a Parameter")
        _check_name(param.name, "parameter")
        if param.name in param_names:
            raise PhysicalComponentError(f"{comp_name}: parameter {param.name!r} is used twice")
        param_names.add(param.name)
        if not is_plain_constant(param.default) or isinstance(param.default, list | tuple):
            raise PhysicalComponentError(
                f"{comp_name}: default of {param.name!r} must be a number, bool or string"
            )

    conserving = [PortSpec(p.name, CONSERVING, p.domain) for p in ports if isinstance(p, Port)]
    first = conserving[0].domain or ""
    color = DOMAINS[first].color if first in DOMAINS else _ANY_COLOR
    raw_doc = cls.__doc__
    doc = inspect.cleandoc(raw_doc) if isinstance(raw_doc, str) else ""
    block = BlockSpec(
        name=comp_name,
        inputs=[p.name for p in ports if isinstance(p, SignalInput)],
        outputs=[p.name for p in ports if isinstance(p, SignalOutput)],
        settings={p.name: p.default for p in parameters},
        shape="rectangle",
        color=color,
        stateful=True,  # rule 14: sensor outputs are the previous tick's values
        doc=doc,
        conserving=conserving,
    )
    return PhysicalSpec(comp_name, ports, parameters, internal, doc, block, role)


def physical_component(cls: type | None = None, *, name: str | None = None) -> Any:
    """Mark a PhysicalComponent class; its spec is attached as ``__physical_spec__``.

    Works bare (``@physical_component``) and with ``name=``. The class is returned
    unchanged; ``core.physical.registry.PhysicalRegistry`` collects marked classes.
    """

    def mark(target: type) -> type:
        spec = component_spec(target, name)
        setattr(target, SPEC_ATTR, spec)
        return target

    if cls is not None:
        return mark(cls)
    return mark


def physical_spec_of(obj: object) -> PhysicalSpec | None:
    """The spec attached by ``@physical_component`` to ``obj`` itself (not inherited)."""
    spec = vars(obj).get(SPEC_ATTR) if isinstance(obj, type) else None
    return spec if isinstance(spec, PhysicalSpec) else None


# ---- building and checking a network


@dataclass(frozen=True)
class NetworkDiagnostic:
    """A problem in one component's network. ``primitive`` is "" for the whole block."""

    block: str
    severity: str
    message: str
    primitive: str = ""


@dataclass
class NetworkResult:
    primitives: list[Primitive] = field(default_factory=list)
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)


def build_network(
    cls: type, params: Mapping[str, object] | None = None, block: str | None = None
) -> NetworkResult:
    """Run ``cls().network`` with the defaults overridden by ``params``, then check it.

    ``block`` names the instance in diagnostics (defaults to the component name).
    Never raises: an unknown class, unknown parameters or an exception in
    ``network`` come back as diagnostics.
    """
    spec = physical_spec_of(cls)
    label = block or getattr(cls, "__name__", "component")
    if spec is None:
        return NetworkResult(
            diagnostics=[NetworkDiagnostic(label, ERROR, "not a @physical_component class")]
        )
    label = block or spec.name
    result = NetworkResult()
    values = spec.defaults()
    for key, value in (params or {}).items():
        if key not in values:
            result.diagnostics.append(NetworkDiagnostic(label, ERROR, f"unknown parameter {key!r}"))
        else:
            values[key] = value
    if not result.ok:
        return result
    nodes = list(spec.node_domains())
    try:
        primitives = cls().network(Ports(nodes), dict(values))
    except Exception as err:  # user code (M5) must not take the caller down
        result.diagnostics.append(
            NetworkDiagnostic(label, ERROR, f"network() failed: {type(err).__name__}: {err}")
        )
        return result
    if not isinstance(primitives, list | tuple):
        result.diagnostics.append(
            NetworkDiagnostic(label, ERROR, "network() must return a list of primitives")
        )
        return result
    result.primitives = list(primitives)
    result.diagnostics.extend(check_network(spec, result.primitives, label))
    return result


_REF_RE = re.compile(r"\b([VI])\s*\(\s*([^()]*?)\s*\)", re.IGNORECASE)


def _values_of(prim: Primitive) -> list[tuple[str, object]]:
    names = ("value", "gain", "initial", "ic")
    return [(n, getattr(prim, n)) for n in names if hasattr(prim, n)]


def check_network(
    spec: PhysicalSpec, primitives: Sequence[object], block: str | None = None
) -> list[NetworkDiagnostic]:
    """Check primitives against the component's nodes, domains and signal inputs."""
    label = block or spec.name
    out: list[NetworkDiagnostic] = []
    nodes = spec.node_domains()
    inputs = set(spec.signal_inputs())

    def err(message: str, prim: str = "", severity: str = ERROR) -> None:
        out.append(NetworkDiagnostic(label, severity, message, prim))

    good: list[Primitive] = []
    names: set[str] = set()
    for prim in primitives:
        if not isinstance(prim, PRIMITIVE_TYPES):
            err(f"{prim!r} is not a primitive")
            continue
        if not isinstance(prim.name, str) or not prim.name.isidentifier():
            err(f"primitive name {prim.name!r} is not a valid identifier")
            continue
        if prim.name in names:
            err(f"primitive name {prim.name!r} is used twice", prim.name)
            continue
        names.add(prim.name)
        good.append(prim)
    sources = {p.name for p in good if isinstance(p, VoltageSource)}

    def domain_of(node: object, prim: str) -> str | None:
        if node == GROUND:
            return None
        if not isinstance(node, str) or node not in nodes:
            err(f"unknown node {node!r}", prim)
            return None
        return nodes[node]

    def same_domain(a: object, b: object, prim: str) -> None:
        da, db = domain_of(a, prim), domain_of(b, prim)
        if da is not None and db is not None and da != db:
            err(
                f"connects {a!r} ({da}) to {b!r} ({db}): only controlled sources may "
                "couple domains",
                prim,
            )

    for prim in good:
        n = prim.name
        same_domain(prim.a, prim.b, n)
        if prim.a == prim.b:
            err(f"both terminals are {prim.a!r}", n, WARNING)
        if isinstance(prim, VCVS | VCCS):
            domain_of(prim.ctrl_a, n)
            domain_of(prim.ctrl_b, n)
        if isinstance(prim, CCCS | CCVS) and prim.control not in sources:
            err(f"control {prim.control!r} is not a VoltageSource of this component", n)
        if isinstance(prim, Behavioural | ExternalSource) and prim.kind not in BEHAVIOURAL_KINDS:
            err(f"kind must be 'v' or 'i', not {prim.kind!r}", n)
        if isinstance(prim, ExternalSource) and prim.signal not in inputs:
            err(f"signal {prim.signal!r} is not a SignalInput of this component", n)
        if isinstance(prim, Behavioural):
            _check_expression(prim, nodes, sources, err)
        for field_name, value in _values_of(prim):
            if value is None and field_name == "ic":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            ):
                err(f"{field_name} must be a finite number, not {value!r}", n)
    return out


def _check_expression(
    prim: Behavioural,
    nodes: Mapping[str, str],
    sources: set[str],
    err: Callable[..., None],
) -> None:
    if not isinstance(prim.expression, str) or not prim.expression.strip():
        err("expression is empty", prim.name)
        return
    for match in _REF_RE.finditer(prim.expression):
        what, args = match.group(1).upper(), [a.strip() for a in match.group(2).split(",")]
        if what == "V":
            if not 1 <= len(args) <= 2:
                err(f"V() takes one or two nodes: {match.group(0)!r}", prim.name)
            for node in args:
                if node != GROUND and node not in nodes:
                    err(f"unknown node {node!r} in expression", prim.name)
        elif len(args) != 1 or args[0] not in sources:
            err(f"I() must name a VoltageSource of this component: {match.group(0)!r}", prim.name)
