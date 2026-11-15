"""S6 spike, child side: what runs in the grid child process.

Two module-level targets for the spawned process (rule 5: spawn + Pipe + daemon):

- ``cold_main(conn, request)``: today's pattern, one run per process, one message back
  (used through core/blocks/process.ChildProcess, the helper G1 factored out).
- ``warm_main(conn, reuse_engine)``: a warm child that serves runs until it is told to
  stop or its pipe closes. Its only state is local to this function (the engine when
  ``reuse_engine``); nothing is kept in module globals.

Both run one request with ``run_once``: unpickle the GridModel, write the DSS script
with writer.py into the request's folder, compile it (OpenDSSEngine.load_native), check,
solve, pickle the GridResult. Every stage is stamped with ``time.perf_counter()``, so
the parent can line the child's stages up with its own clock: QueryPerformanceCounter
on Windows and CLOCK_MONOTONIC on Linux are one clock for every process on the machine
(``time.time()`` has only about 1 ms resolution on Windows with Python 3.12).

Heavy imports (numpy, core.grid, dss) are done inside the functions, so the cost of
importing them is measured as a stage and is not hidden in the spawn.
"""

from __future__ import annotations

import os
import pickle
import time
import traceback
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from typing import Any

RUN = "run"
STOP = "stop"


@dataclass
class Request:
    run_id: int
    model_bytes: bytes
    config: Any  # PowerFlowConfig (plain dataclass, pickled with the request)
    out_dir: str
    """Folder for the written script (the app: name_results/grid/<design>/)."""
    also_load_model: bool = False
    """Also time OpenDSSEngine.load_model (command by command, no file) for comparison."""
    hang_s: float = 0.0
    """Spike test hook: the child sleeps this long inside the run (Stop, timeout tests)."""
    crash: bool = False
    """Spike test hook: the child dies inside the run with os._exit(3)."""


@dataclass
class Reply:
    run_id: int
    pid: int
    ok: bool
    result_bytes: bytes = b""
    stamps: dict[str, float] = field(default_factory=dict)
    """Stage name -> time.perf_counter() at the END of that stage (plus "enter")."""
    extra: dict[str, float] = field(default_factory=dict)
    script_bytes: int = 0
    error: str = ""


def _import_engine(stamps: dict[str, float]) -> Any:
    """Import core.grid + dss; returns the OpenDSSEngine class."""
    from core.grid.engines.opendss.engine import OpenDSSEngine  # numpy, core.grid

    stamps["import_core"] = time.perf_counter()
    import dss  # noqa: F401  the engine imports it lazily; do it here to time it

    stamps["import_dss"] = time.perf_counter()
    return OpenDSSEngine


def run_once(engine: Any, request: Request, stamps: dict[str, float]) -> Reply:
    """One edit-run request in ``engine`` (an OpenDSSEngine, already created)."""
    from core.grid.engines.opendss.writer import write_dss
    from core.netlist.model import ERROR

    reply = Reply(request.run_id, os.getpid(), ok=False, stamps=stamps)
    if request.hang_s:
        time.sleep(request.hang_s)
    if request.crash:
        os._exit(3)
    if engine.context is None:
        engine._context()  # spike: create the DSS context here to time it on its own
    stamps["context"] = time.perf_counter()
    model = pickle.loads(request.model_bytes)
    stamps["unpickle_model"] = time.perf_counter()
    text, diagnostics = write_dss(model)
    stamps["write_dss"] = time.perf_counter()
    if any(d.severity == ERROR for d in diagnostics):
        reply.error = "; ".join(d.message for d in diagnostics)
        return reply
    path = os.path.join(request.out_dir, f"run_{request.run_id}.dss")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    reply.script_bytes = len(text.encode("utf-8"))
    stamps["write_file"] = time.perf_counter()
    diagnostics = engine.load_native(path)
    stamps["compile"] = time.perf_counter()
    if diagnostics:
        reply.error = "; ".join(d.message for d in diagnostics)
        return reply
    checks = engine.validation()  # engine.run would do this first (extract + validate)
    stamps["validate"] = time.perf_counter()
    if any(d.severity == ERROR for d in checks):
        reply.error = "; ".join(d.message for d in checks)
        return reply
    result = engine.run(request.config)
    stamps["solve"] = time.perf_counter()
    reply.result_bytes = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
    stamps["pickle_result"] = time.perf_counter()
    reply.ok = result.converged
    if request.also_load_model:
        t0 = time.perf_counter()
        engine.load_model(model)
        reply.extra["load_model_s"] = time.perf_counter() - t0
    return reply


def cold_main(conn: Connection, request: Request) -> None:
    """Today's pattern: a new process, a new engine, one run, one message."""
    stamps = {"enter": time.perf_counter()}
    try:
        engine_cls = _import_engine(stamps)
        engine = engine_cls()
        try:
            reply = run_once(engine, request, stamps)
        finally:
            engine.close()
        conn.send(reply)
    except BaseException as err:  # everything goes back to the parent
        message = "".join(traceback.format_exception_only(err)).strip()
        conn.send(Reply(request.run_id, os.getpid(), ok=False, error=message))
    finally:
        conn.close()


def warm_main(conn: Connection, reuse_engine: bool) -> None:
    """A warm child: imports once, then serves (kind, payload) messages.

    ``(RUN, Request)`` -> one Reply. ``(STOP, None)`` or a closed pipe (the parent went
    away) -> close the engine and return, so the process exits with code 0.
    With ``reuse_engine`` one OpenDSSEngine (one DSS context) serves every run
    (load_native starts with ``clear``); otherwise each run gets a new engine/context.
    """
    stamps = {"enter": time.perf_counter()}
    engine_cls = _import_engine(stamps)
    conn.send(("ready", os.getpid(), stamps))
    engine = engine_cls() if reuse_engine else None
    try:
        while True:
            try:
                kind, payload = conn.recv()
            except (EOFError, OSError):
                return  # the parent closed its end or died
            if kind == STOP:
                return
            request: Request = payload
            stamps = {"enter": time.perf_counter()}
            try:
                if reuse_engine:
                    reply = run_once(engine, request, stamps)
                else:
                    fresh = engine_cls()
                    try:
                        reply = run_once(fresh, request, stamps)
                    finally:
                        fresh.close()
            except BaseException as err:
                message = "".join(traceback.format_exception_only(err)).strip()
                reply = Reply(request.run_id, os.getpid(), ok=False, error=message)
            try:
                conn.send(reply)
            except (EOFError, OSError):
                return
    finally:
        if engine is not None:
            engine.close()
        conn.close()
