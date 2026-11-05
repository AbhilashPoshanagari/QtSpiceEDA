"""Abstract simulator backend. Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from core.sim.result import SimulationResult

MessageCallback = Callable[[str], None]
ProgressCallback = Callable[[int], None]


class SimulationError(RuntimeError):
    """Raised when a deck cannot be loaded or a run fails."""


class SimulatorBackend(ABC):
    """A circuit simulator that takes a SPICE deck and returns SimulationResults.

    ``on_message`` receives console lines and ``on_progress`` a percentage (0-100).
    Both may be called from a background thread.
    """

    def __init__(
        self,
        on_message: MessageCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.on_message = on_message
        self.on_progress = on_progress

    @abstractmethod
    def available(self) -> bool:
        """True if the simulator can be used. Must not raise."""

    @abstractmethod
    def load(self, deck: str) -> None:
        """Load a complete SPICE deck (title line first, ``.end`` last)."""

    @abstractmethod
    def run(self) -> None:
        """Run the loaded deck and block until it finishes or is stopped."""

    @abstractmethod
    def results(self) -> SimulationResult | None:
        """Result of the last run, or None if nothing has run."""

    @abstractmethod
    def stop(self) -> None:
        """Ask a running simulation to stop. Safe to call from another thread."""
