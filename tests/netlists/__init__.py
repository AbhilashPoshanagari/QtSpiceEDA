"""Example netlists used by the tests and the Phase 2 demo."""

from __future__ import annotations

from pathlib import Path

NETLISTS = Path(__file__).resolve().parent
GOOD = NETLISTS / "good"
BROKEN = NETLISTS / "broken"

# The four Phase 2 examples: they must check clean when pasted into the editor.
GOOD_FILES = sorted(p for p in GOOD.glob("*.cir") if p.name != "opamp_include.cir")
# Also good, but needs its file location to resolve ".include opamp.lib".
INCLUDE_FILE = GOOD / "opamp_include.cir"

# Every error each broken example must report: (line, message fragment).
BROKEN_EXPECTED: dict[str, list[tuple[int, str]]] = {
    "missing_ground.cir": [(1, "no ground node")],
    "floating_node.cir": [(5, "node 'nc' has only one connection")],
    "duplicate_name.cir": [(4, "duplicate element name 'R1'")],
    "unknown_model.cir": [(3, "unknown model 'dfast'")],
    "bad_number.cir": [(3, "bad number '4.7.2k'")],
}
