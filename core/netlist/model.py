"""Data model for a parsed SPICE netlist. Qt-free.

Positions (``source_line`` and the ``*_col`` fields) are 1-based and do not take part in
equality, so a circuit parsed from a rebuilt deck compares equal to the original.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"

GROUND_NODES = frozenset({"0", "gnd"})


@dataclass
class Element:
    """One element line, e.g. ``R1 in out 1k``.

    ``kind`` is the upper-case first letter of the name. ``nodes`` are lower-case.
    ``value`` is everything positional after the nodes, as written (whitespace
    normalised): the value of R/C/L, the source spec of V/I, the model name (plus any
    area) of D/Q/M, the controlling source and gain of F/H, the subcircuit name of X.
    ``params`` holds ``key=value`` pairs with lower-case keys.
    """

    name: str
    kind: str
    nodes: list[str]
    value: str = ""
    params: dict[str, str] = field(default_factory=dict)
    source_line: int = field(default=0, compare=False)
    name_col: int = field(default=1, compare=False, repr=False)
    node_cols: list[int] = field(default_factory=list, compare=False, repr=False)
    value_col: int = field(default=1, compare=False, repr=False)

    def reference(self) -> str | None:
        """Name of the model (D, Q, M) or subcircuit (X) this element uses, if any."""
        if self.kind in ("D", "Q", "M"):
            return self.value.split()[0] if self.value else None
        if self.kind == "X":
            return self.value or None
        return None


@dataclass
class Model:
    """A ``.model name type (params)`` card."""

    name: str
    type: str
    params: dict[str, str] = field(default_factory=dict)
    source_line: int = field(default=0, compare=False)


@dataclass
class Subcircuit:
    """A ``.subckt name ports... params: k=v`` ... ``.ends`` block."""

    name: str
    ports: list[str]
    elements: list[Element] = field(default_factory=list)
    models: list[Model] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)
    source_line: int = field(default=0, compare=False)


@dataclass
class Analysis:
    """An analysis card. ``kind`` is ``op``, ``dc``, ``ac`` or ``tran``; ``args`` as written."""

    kind: str
    args: list[str] = field(default_factory=list)
    source_line: int = field(default=0, compare=False)


@dataclass
class Circuit:
    """A whole netlist. ``end_line`` is the ``.end`` line, or the last line if there is none."""

    title: str = ""
    elements: list[Element] = field(default_factory=list)
    models: list[Model] = field(default_factory=list)
    subcircuits: list[Subcircuit] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)
    analyses: list[Analysis] = field(default_factory=list)
    end_line: int = field(default=1, compare=False)

    def nodes(self) -> list[str]:
        """Top-level node names in order of first appearance (subcircuit internals excluded)."""
        seen: dict[str, None] = {}
        for element in self.elements:
            for node in element.nodes:
                seen.setdefault(node, None)
        return list(seen)


@dataclass(frozen=True)
class Diagnostic:
    """A problem found in the netlist, reported at a 1-based line and column."""

    line: int
    col: int
    severity: str
    message: str


@dataclass
class ParseResult:
    circuit: Circuit
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)
