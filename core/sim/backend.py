"""Abstract simulator backend. Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from core.sim.result import SimulationResult

MessageCallback = Callable[[str], None]
ProgressCallback = Callable[[int], None]


# ngspice compatibility mode sent before every circuit load (Spike A): "ltpsa" makes
# ngspice accept LTspice and PSpice syntax; "" switches compatibility off.
DEFAULT_COMPAT_MODE = "ltpsa"
_COMPAT_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyz")


def valid_compat_mode(mode: object) -> bool:
    """True for "" or a short lower-case word such as "ltpsa" (nothing else can be
    smuggled into the ngspice command)."""
    return isinstance(mode, str) and len(mode) <= 16 and set(mode) <= _COMPAT_LETTERS


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
