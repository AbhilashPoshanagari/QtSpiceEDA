"""Run a function in a brand-new spawned process (ngspice is global per process)."""

from __future__ import annotations

import multiprocessing as mp
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor


def in_fresh_process[T](fn: Callable[..., T], *args: object, timeout: float = 600) -> T:
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
        return pool.submit(fn, *args).result(timeout=timeout)
