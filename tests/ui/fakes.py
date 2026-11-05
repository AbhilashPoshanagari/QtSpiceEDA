"""Test doubles shared by the UI tests."""

from __future__ import annotations

import threading

import numpy as np

from core.sim.backend import SimulationError, SimulatorBackend
from core.sim.result import SimulationResult


def tran_result(points: int = 101) -> SimulationResult:
    t = np.linspace(0, 5e-3, points)
    return SimulationResult(
        axis="time",
        vectors={"time": t, "V(out)": 1 - np.exp(-t / 1e-3), "V(in)": np.ones_like(t)},
        units={"time": "s", "V(out)": "V", "V(in)": "V"},
        analysis="tran",
    )


class FakeBackend(SimulatorBackend):
    """A SimulatorBackend that returns a canned result.

    ``fail_in`` ("load" or "run") raises SimulationError there; ``block`` makes run()
    wait until stop() is called, like a long simulation.
    """

    def __init__(
        self,
        lines: list[str] | None = None,
        progress: list[int] | None = None,
        fail_in: str = "",
        block: bool = False,
        result: SimulationResult | None = None,
    ) -> None:
        super().__init__()
        self.lines = lines or []
        self.percent = progress or []
        self.fail_in = fail_in
        self.block = block
        self.result = result or tran_result()
        self.loaded = ""
        self.started = threading.Event()
        self._stopped = threading.Event()
        self._done: SimulationResult | None = None

    def available(self) -> bool:
        return True

    def load(self, deck: str) -> None:
        if self.fail_in == "load":
            raise SimulationError("load failed")
        self.loaded = deck
        self._stopped.clear()

    def run(self) -> None:
        self.started.set()
        for line in self.lines:
            if self.on_message:
                self.on_message(line)
        for percent in self.percent:
            if self.on_progress:
                self.on_progress(percent)
        if self.fail_in == "run":
            raise SimulationError("run failed")
        if self.block:
            self._stopped.wait(10)
        if self._stopped.is_set():
            raise SimulationError("simulation stopped")
        self._done = self.result

    def results(self) -> SimulationResult | None:
        return self._done

    def stop(self) -> None:
        self._stopped.set()
