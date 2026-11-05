"""Schematic data model. Qt-free.

Only what the netlister needs so far (L7); wires, positions and the sheet come with
the S tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.symbols.asy import Symbol


@dataclass
class PartInstance:
    """A placed symbol.

    ``name`` is the InstName as the user sees it (``R1``). ``nets`` holds one net name
    per pin, in the order of ``symbol.pins`` (SpiceOrder). ``attrs`` are this part's
    own attribute values; they override the symbol's SYMATTR defaults (case-insensitive
    keys).
    """

    name: str
    symbol: Symbol
    nets: list[str] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)

    def attr(self, key: str, default: str = "") -> str:
        folded = key.casefold()
        for name, value in self.attrs.items():
            if name.casefold() == folded:
                return value
        return self.symbol.attr(key, default)
