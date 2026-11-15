"""S6 spike: the GridModels that are sent to the child, and the edit between runs.

The models are the extracted GridModels of the fixtures (IEEE 13, IEEE 123 with its
AFTER_COMPILE commands), read back after one solve so the regulator taps are the
solved ones (as G3 and GridRunner do). They are made in a ChildProcess too, so
OpenDSS never runs in the benchmark's own process.
"""

from __future__ import annotations

import dataclasses
import pickle
import sys
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.blocks.process import ChildProcess  # noqa: E402

FEEDERS = ("ieee13", "ieee123")


def config() -> Any:
    """The run settings: controls on (STATIC), IEEE 123 needs MaxControlIter 30."""
    from core.grid.config import CONTROL_STATIC, PowerFlowConfig

    return PowerFlowConfig(control_mode=CONTROL_STATIC, max_control_iterations=30)


def _extract_child(conn: Connection, feeder: str) -> None:
    from core.grid.engines.opendss.engine import OpenDSSEngine
    from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES

    engine = OpenDSSEngine()
    try:
        assert engine.load_native(FEEDER_FILES[feeder]) == []
        for command in AFTER_COMPILE[feeder]:
            engine.context.Text.Command = command
        result = engine.run(config())
        assert result.converged, result.diagnostics
        model, _ = engine.extract_model()
        conn.send(pickle.dumps((model, result)))
    finally:
        engine.close()
        conn.close()


def extracted(feeder: str) -> tuple[Any, Any]:
    """(GridModel, GridResult of the original file) of ``feeder``."""
    outcome = ChildProcess(_extract_child, (feeder,), timeout=60, name="extract").wait()
    assert outcome.ok, outcome.error
    return pickle.loads(outcome.message)


def edited(model: Any, i: int) -> Any:
    """The model after edit ``i``: the first load's kW scaled by (1 + 0.01 i).

    Stands in for a D4 SetProperty command; every run solves a different model, so no
    run can be served from anything cached by the one before.
    """
    name = next(iter(model.loads))
    load = model.loads[name]
    loads = dict(model.loads)
    loads[name] = dataclasses.replace(load, kw=load.kw * (1 + 0.01 * i))
    return dataclasses.replace(model, loads=loads)
