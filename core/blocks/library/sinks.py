"""Sink blocks: Scope."""

from __future__ import annotations

from core.blocks.decorator import block

SCOPE_COLOR = "#8e5cc7"


@block(shape="rectangle", outputs=[], color=SCOPE_COLOR)
def Scope(u, label=""):  # noqa: N802  block names are shown to the user as written
    """Records u; the engine plots it in the waveform viewer under ``label``."""
    return None
