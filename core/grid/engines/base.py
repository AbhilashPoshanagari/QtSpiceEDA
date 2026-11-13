"""GridEngine: what every grid engine offers. Qt-free.

Everything outside core/grid/engines/opendss/ uses this protocol, never ``dss``
(rule 15). Engines report problems as Diagnostics and never raise on bad input
(rule 6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from core.grid.config import PowerFlowConfig
from core.grid.result import GridResult
from core.physical.component import NetworkDiagnostic

if TYPE_CHECKING:
    from core.grid.model import GridModel


@runtime_checkable
class GridEngine(Protocol):
    def available(self) -> bool:
        """True when the engine's library can be loaded."""
        ...

    def load_native(self, path: str) -> list[NetworkDiagnostic]:
        """Load a circuit from a file in the engine's own format (.dss for OpenDSS)."""
        ...

    def load_model(self, model: GridModel) -> list[NetworkDiagnostic]:
        """Load a circuit from an engine-independent GridModel."""
        ...

    def extract_model(self) -> tuple[GridModel | None, list[NetworkDiagnostic]]:
        """Read the loaded circuit back as a GridModel, with the extractor's
        Diagnostics (unsupported elements, ...); None when nothing is loaded."""
        ...

    def run(self, config: PowerFlowConfig) -> GridResult:
        """Solve the loaded circuit once (snapshot power flow)."""
        ...

    def close(self) -> None:
        """Release the engine; the object is not used afterwards."""
        ...
