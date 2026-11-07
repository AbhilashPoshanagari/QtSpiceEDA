"""Diagram: block instances and the wires between them. Qt-free.

A diagram only stores *what* is placed and connected; running it is the engine's job.
It needs a spec lookup (block name -> BlockSpec, e.g. ``BlockRegistry.get``) to know
which ports are inputs and which are outputs.

Wiring rules (each broken rule raises DiagramError):

- a wire goes from an output port to an input port;
- an input port has at most one wire;
- a wire cannot go from a port back to that same port;
- a signal wire never touches a conserving (physical) port.

Physical wires (v0.3) join two conserving ports. They have no direction, so
``PhysicalWire("A", "p", "B", "n")`` equals ``PhysicalWire("B", "n", "A", "p")``.
Both ends must be conserving ports of the same domain (a Solver Configuration port,
domain ``any``, joins any domain). A port may have many physical wires: the ports
they join form one node. ``check_physical_wire`` gives the reason a wire is refused
("" when it is allowed) without raising; ``add_physical_wire`` raises DiagramError
with that reason.

Instance settings hold only the values the user changed; the spec's defaults fill in
the rest (``effective_settings``). ``to_dict`` / ``from_dict`` give the JSON form
stored in a project file::

    {"blocks": [{"id": "Gain1", "spec": "Gain", "x": 0, "y": 0, "settings": {"k": 2}}],
     "wires":  [{"src": "Sine1.y", "dst": "Gain1.u"}],
     "physical_wires": [{"a": "Mass1.A", "b": "Spring1.A"}]}

``physical_wires`` is written only when there are some, so a diagram without
physical wires gives exactly the v0.1 JSON.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.blocks.spec import BlockSpec, is_plain_constant
from core.physical.domains import ANY_DOMAIN

SpecLookup = Callable[[str], BlockSpec | None]


class DiagramError(ValueError):
    """A diagram edit or a diagram file breaks a rule."""


@dataclass
class BlockInstance:
    """One placed block."""

    id: str
    spec_name: str
    x: float = 0.0
    y: float = 0.0
    settings: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Wire:
    """A connection from an output port to an input port."""

    src_block: str
    src_port: str
    dst_block: str
    dst_port: str

    @property
    def src(self) -> str:
        return f"{self.src_block}.{self.src_port}"

    @property
    def dst(self) -> str:
        return f"{self.dst_block}.{self.dst_port}"


@dataclass(frozen=True, init=False)
class PhysicalWire:
    """An undirected connection between two conserving ports.

    The ends are stored in sorted order, so the same two ports always give an equal
    (and equally hashed) wire, whichever end was named first.
    """

    a_block: str
    a_port: str
    b_block: str
    b_port: str

    def __init__(self, a_block: str, a_port: str, b_block: str, b_port: str) -> None:
        (ab, ap), (bb, bp) = sorted([(a_block, a_port), (b_block, b_port)])
        object.__setattr__(self, "a_block", ab)
        object.__setattr__(self, "a_port", ap)
        object.__setattr__(self, "b_block", bb)
        object.__setattr__(self, "b_port", bp)

    @property
    def a(self) -> str:
        return f"{self.a_block}.{self.a_port}"

    @property
    def b(self) -> str:
        return f"{self.b_block}.{self.b_port}"

    @property
    def ends(self) -> tuple[tuple[str, str], tuple[str, str]]:
        return (self.a_block, self.a_port), (self.b_block, self.b_port)

    def touches(self, block_id: str) -> bool:
        return block_id in (self.a_block, self.b_block)


class Diagram:
    """Block instances (by id, in the order placed) and wires (in the order drawn)."""

    def __init__(self, specs: SpecLookup) -> None:
        self._lookup = specs
        self._blocks: dict[str, BlockInstance] = {}
        self._wires: list[Wire] = []
        self._physical_wires: list[PhysicalWire] = []

    # ---- queries

    @property
    def blocks(self) -> list[BlockInstance]:
        return list(self._blocks.values())

    @property
    def wires(self) -> list[Wire]:
        return list(self._wires)

    @property
    def physical_wires(self) -> list[PhysicalWire]:
        return list(self._physical_wires)

    def physical_wires_at(self, block_id: str, port: str) -> list[PhysicalWire]:
        """Physical wires with one end at ``block_id.port``."""
        return [w for w in self._physical_wires if (block_id, port) in w.ends]

    def block(self, block_id: str) -> BlockInstance:
        try:
            return self._blocks[block_id]
        except KeyError:
            raise DiagramError(f"no block with id {block_id!r}") from None

    def spec(self, block_id: str) -> BlockSpec:
        """The spec of a placed block."""
        return self._spec(self.block(block_id).spec_name)

    def effective_settings(self, block_id: str) -> dict[str, object]:
        """The spec's default settings, overridden by the instance's own."""
        inst = self.block(block_id)
        return {**self._spec(inst.spec_name).settings, **inst.settings}

    def wire_into(self, block_id: str, port: str) -> Wire | None:
        """The wire that feeds input ``port`` of ``block_id``, if any."""
        for wire in self._wires:
            if wire.dst_block == block_id and wire.dst_port == port:
                return wire
        return None

    # ---- edits

    def add_block(
        self,
        spec_name: str,
        x: float = 0.0,
        y: float = 0.0,
        settings: dict[str, object] | None = None,
        block_id: str | None = None,
    ) -> BlockInstance:
        """Place a block. Without ``block_id`` the id is the spec name plus a number."""
        spec = self._spec(spec_name)
        if block_id is None:
            block_id = self._new_id(spec_name)
        elif not block_id.isidentifier():
            raise DiagramError(f"block id {block_id!r} must be a valid identifier")
        elif block_id in self._blocks:
            raise DiagramError(f"a block with id {block_id!r} already exists")
        values = dict(settings or {})
        _check_settings(block_id, spec, values)
        inst = BlockInstance(block_id, spec_name, float(x), float(y), values)
        self._blocks[block_id] = inst
        return inst

    def remove_block(self, block_id: str) -> None:
        """Remove a block and every wire attached to it."""
        self.block(block_id)
        del self._blocks[block_id]
        self._wires = [w for w in self._wires if block_id not in (w.src_block, w.dst_block)]
        self._physical_wires = [w for w in self._physical_wires if not w.touches(block_id)]

    def move_block(self, block_id: str, x: float, y: float) -> None:
        inst = self.block(block_id)
        inst.x, inst.y = float(x), float(y)

    def set_settings(self, block_id: str, settings: dict[str, object]) -> None:
        """Replace the instance's own settings (values left out use the defaults)."""
        inst = self.block(block_id)
        values = dict(settings)
        _check_settings(block_id, self._spec(inst.spec_name), values)
        inst.settings = values

    def add_wire(self, src_block: str, src_port: str, dst_block: str, dst_port: str) -> Wire:
        """Connect output ``src_block.src_port`` to input ``dst_block.dst_port``."""
        if src_block == dst_block and src_port == dst_port:
            raise DiagramError(f"cannot wire {src_block}.{src_port} to itself")
        src_spec = self.spec(src_block)
        dst_spec = self.spec(dst_block)
        for block_id, port, spec in (
            (src_block, src_port, src_spec),
            (dst_block, dst_port, dst_spec),
        ):
            found = spec.port(port)
            if found is not None and found.is_conserving:
                raise DiagramError(
                    f"{block_id}.{port} is a physical ({found.domain}) port: "
                    "use a physical wire, not a signal wire"
                )
        if src_port not in src_spec.outputs:
            if src_port in src_spec.inputs:
                raise DiagramError(
                    f"{src_block}.{src_port} is an input: a wire must start at an output"
                )
            raise DiagramError(f"block {src_block!r} has no output {src_port!r}")
        if dst_port not in dst_spec.inputs:
            if dst_port in dst_spec.outputs:
                raise DiagramError(
                    f"{dst_block}.{dst_port} is an output: a wire must end at an input"
                )
            raise DiagramError(f"block {dst_block!r} has no input {dst_port!r}")
        existing = self.wire_into(dst_block, dst_port)
        if existing is not None:
            raise DiagramError(f"input {dst_block}.{dst_port} is already wired from {existing.src}")
        wire = Wire(src_block, src_port, dst_block, dst_port)
        self._wires.append(wire)
        return wire

    def remove_wire(self, wire: Wire) -> None:
        try:
            self._wires.remove(wire)
        except ValueError:
            raise DiagramError(f"no wire from {wire.src} to {wire.dst}") from None

    def check_physical_wire(self, a_block: str, a_port: str, b_block: str, b_port: str) -> str:
        """Why a physical wire between these ports is refused, or "" if it is allowed."""
        if (a_block, a_port) == (b_block, b_port):
            return f"cannot wire {a_block}.{a_port} to itself"
        domains = []
        for block_id, port in ((a_block, a_port), (b_block, b_port)):
            inst = self._blocks.get(block_id)
            if inst is None:
                return f"no block with id {block_id!r}"
            spec = self._lookup(inst.spec_name)
            if spec is None:
                return f"unknown block type {inst.spec_name!r}"
            found = spec.port(port)
            if found is None:
                return f"block {block_id!r} has no port {port!r}"
            if not found.is_conserving:
                return (
                    f"{block_id}.{port} is a signal port: a physical wire joins "
                    "two physical (conserving) ports"
                )
            domains.append(found.domain)
        da, db = domains
        if da != db and ANY_DOMAIN not in (da, db):
            return (
                f"cannot connect {a_block}.{a_port} ({da}) to {b_block}.{b_port} ({db}): "
                "ports of different domains never connect"
            )
        if PhysicalWire(a_block, a_port, b_block, b_port) in self._physical_wires:
            return f"{a_block}.{a_port} and {b_block}.{b_port} are already wired"
        return ""

    def add_physical_wire(
        self, a_block: str, a_port: str, b_block: str, b_port: str
    ) -> PhysicalWire:
        """Join two conserving ports. Raises DiagramError with the reason if refused."""
        reason = self.check_physical_wire(a_block, a_port, b_block, b_port)
        if reason:
            raise DiagramError(reason)
        wire = PhysicalWire(a_block, a_port, b_block, b_port)
        self._physical_wires.append(wire)
        return wire

    def remove_physical_wire(self, wire: PhysicalWire) -> None:
        try:
            self._physical_wires.remove(wire)
        except ValueError:
            raise DiagramError(f"no physical wire between {wire.a} and {wire.b}") from None

    # ---- serialisation

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "blocks": [
                {
                    "id": b.id,
                    "spec": b.spec_name,
                    "x": b.x,
                    "y": b.y,
                    "settings": dict(b.settings),
                }
                for b in self._blocks.values()
            ],
            "wires": [{"src": w.src, "dst": w.dst} for w in self._wires],
        }
        if self._physical_wires:
            data["physical_wires"] = [{"a": w.a, "b": w.b} for w in self._physical_wires]
        return data

    @classmethod
    def from_dict(cls, data: object, specs: SpecLookup) -> Diagram:
        """Rebuild a diagram, checking every rule. Raises DiagramError on bad data."""
        if not isinstance(data, dict):
            raise DiagramError("a diagram must be a JSON object")
        blocks = data.get("blocks", [])
        wires = data.get("wires", [])
        physical = data.get("physical_wires", [])
        if not all(isinstance(v, list) for v in (blocks, wires, physical)):
            raise DiagramError("'blocks', 'wires' and 'physical_wires' must be lists")
        diagram = cls(specs)
        for item in blocks:
            if not isinstance(item, dict):
                raise DiagramError("each block must be a JSON object")
            block_id, spec_name = item.get("id"), item.get("spec")
            if not isinstance(block_id, str) or not isinstance(spec_name, str):
                raise DiagramError("each block needs a string 'id' and 'spec'")
            settings = item.get("settings", {})
            if not isinstance(settings, dict):
                raise DiagramError(f"settings of block {block_id!r} must be a JSON object")
            x, y = item.get("x", 0.0), item.get("y", 0.0)
            if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (x, y)):
                raise DiagramError(f"x and y of block {block_id!r} must be numbers")
            diagram.add_block(spec_name, x, y, settings, block_id=block_id)
        for item in wires:
            if not isinstance(item, dict):
                raise DiagramError("each wire must be a JSON object")
            src = _split_port(item.get("src"))
            dst = _split_port(item.get("dst"))
            diagram.add_wire(*src, *dst)
        for item in physical:
            if not isinstance(item, dict):
                raise DiagramError("each physical wire must be a JSON object")
            a = _split_port(item.get("a"))
            b = _split_port(item.get("b"))
            diagram.add_physical_wire(*a, *b)
        return diagram

    # ---- helpers

    def _spec(self, spec_name: str) -> BlockSpec:
        spec = self._lookup(spec_name)
        if spec is None:
            raise DiagramError(f"unknown block type {spec_name!r}")
        return spec

    def _new_id(self, spec_name: str) -> str:
        base = spec_name if spec_name.isidentifier() else "Block"
        n = 1
        while f"{base}{n}" in self._blocks:
            n += 1
        return f"{base}{n}"


def _check_settings(block_id: str, spec: BlockSpec, values: dict[str, object]) -> None:
    for name, value in values.items():
        if name not in spec.settings:
            raise DiagramError(f"block {block_id!r} ({spec.name}) has no setting {name!r}")
        if not is_plain_constant(value):
            raise DiagramError(
                f"setting {name!r} of block {block_id!r} must be a plain constant "
                "(number, string, bool, None or a list of those)"
            )


def _split_port(text: object) -> tuple[str, str]:
    if not isinstance(text, str) or text.count(".") != 1:
        raise DiagramError(f"a wire end must look like 'Block1.port', not {text!r}")
    block_id, port = text.split(".")
    return block_id, port
