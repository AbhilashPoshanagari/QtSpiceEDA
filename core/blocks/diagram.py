"""Diagram: block instances and the wires between them. Qt-free.

A diagram only stores *what* is placed and connected; running it is the engine's job.
It needs a spec lookup (block name -> BlockSpec, e.g. ``BlockRegistry.get``) to know
which ports are inputs and which are outputs.

Wiring rules (each broken rule raises DiagramError):

- a wire goes from an output port to an input port;
- an input port has at most one wire;
- a wire cannot go from a port back to that same port.

Instance settings hold only the values the user changed; the spec's defaults fill in
the rest (``effective_settings``). ``to_dict`` / ``from_dict`` give the JSON form
stored in a project file::

    {"blocks": [{"id": "Gain1", "spec": "Gain", "x": 0, "y": 0, "settings": {"k": 2}}],
     "wires":  [{"src": "Sine1.y", "dst": "Gain1.u"}]}
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.blocks.spec import BlockSpec, is_plain_constant

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


class Diagram:
    """Block instances (by id, in the order placed) and wires (in the order drawn)."""

    def __init__(self, specs: SpecLookup) -> None:
        self._lookup = specs
        self._blocks: dict[str, BlockInstance] = {}
        self._wires: list[Wire] = []

    # ---- queries

    @property
    def blocks(self) -> list[BlockInstance]:
        return list(self._blocks.values())

    @property
    def wires(self) -> list[Wire]:
        return list(self._wires)

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

    # ---- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
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

    @classmethod
    def from_dict(cls, data: object, specs: SpecLookup) -> Diagram:
        """Rebuild a diagram, checking every rule. Raises DiagramError on bad data."""
        if not isinstance(data, dict):
            raise DiagramError("a diagram must be a JSON object")
        blocks = data.get("blocks", [])
        wires = data.get("wires", [])
        if not isinstance(blocks, list) or not isinstance(wires, list):
            raise DiagramError("'blocks' and 'wires' must be lists")
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
