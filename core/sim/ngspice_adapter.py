"""NgspiceAdapter: the only module that knows the ngspice shared-library API.

Signatures follow third_party/ngspice/include/sharedspice.h. The library is loaded
lazily with ctypes; if it cannot be found, available() returns False and nothing
else in the app breaks.

ngspice is set up once per process, in one of two modes chosen by the first adapter
that asks for the library:

- plain (``NgspiceAdapter()``): no SendData and no sync callbacks, as in v0.1/v0.2.
  Netlist and schematic runs use it.
- sync (``NgspiceAdapter(sync=True)``, C1): SendData + SendInitData (ngspice calls
  SendData only when SendInitData is not NULL) and ``ngSpice_Init_Sync`` with
  GetVSRCData, GetISRCData and GetSyncData. Only the BlockRunner child uses it, for
  combined physical runs. ``SyncHandlers.data`` receives the accepted time points;
  ``SyncHandlers.source`` gives the value of every ``external`` source.

  GetSyncData always returns 0: a nonzero value makes ngspice redo the step, and the
  redo steps over the tick breakpoint. It only records the redo information
  (``location=1, redostep=1`` = rejected); a SendData point that follows a rejected
  step is never passed on.

Ticked runs (``run_ticks``) end with ngspice's ``stop when``: with GetSyncData
registered ngspice often fails to finish at the deck's tstop, so the deck runs two
ticks further (``tran_card``) and stops half a tick after the last tick.

setlocale(LC_NUMERIC, "C") is called before every load (rule 4).
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import locale
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.sim.backend import (
    DEFAULT_COMPAT_MODE,
    MessageCallback,
    ProgressCallback,
    SimulationError,
    SimulatorBackend,
    valid_compat_mode,
)
from core.sim.result import SimulationResult

# Environment variable that points at a specific ngspice shared library file.
LIBRARY_ENV_VAR = "QTSPICE_NGSPICE_LIB"

_THIRD_PARTY = Path(__file__).resolve().parents[2] / "third_party" / "ngspice"

PLAIN = "plain"
SYNC = "sync"

# --- ctypes mirror of sharedspice.h ------------------------------------------


class _NgComplex(ctypes.Structure):
    _fields_ = [("cx_real", ctypes.c_double), ("cx_imag", ctypes.c_double)]


class _VectorInfo(ctypes.Structure):
    _fields_ = [
        ("v_name", ctypes.c_char_p),
        ("v_type", ctypes.c_int),
        ("v_flags", ctypes.c_short),
        ("v_realdata", ctypes.POINTER(ctypes.c_double)),
        ("v_compdata", ctypes.POINTER(_NgComplex)),
        ("v_length", ctypes.c_int),
    ]


class _VecValues(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("creal", ctypes.c_double),
        ("cimag", ctypes.c_double),
        ("is_scale", ctypes.c_bool),
        ("is_complex", ctypes.c_bool),
    ]


class _VecValuesAll(ctypes.Structure):
    _fields_ = [
        ("veccount", ctypes.c_int),
        ("vecindex", ctypes.c_int),
        ("vecsa", ctypes.POINTER(ctypes.POINTER(_VecValues))),
    ]


# typedef int (SendChar)(char*, int, void*);  SendStat has the same shape.
_SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_SendStat = _SendChar
# typedef int (ControlledExit)(int, NG_BOOL, NG_BOOL, int, void*);
_ControlledExit = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p
)
# typedef int (BGThreadRunning)(NG_BOOL, int, void*);
_BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
# typedef int (SendData)(pvecvaluesall, int, int, void*);
_SendData = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(_VecValuesAll), ctypes.c_int, ctypes.c_int, ctypes.c_void_p
)
# typedef int (SendInitData)(pvecinfoall, int, void*);  the struct is not read.
_SendInitData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
# typedef int (GetVSRCData)(double*, double, char*, int, void*);  GetISRCData alike.
_GetSRCData = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_double),
    ctypes.c_double,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_void_p,
)
# typedef int (GetSyncData)(double, double*, double, int, int, int, void*);
#   time, delta, olddelta, redostep, ident, location, userdata
_GetSyncData = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
    ctypes.c_double,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
)

# v_type values from ngspice's sim.h (SV_*), mapped to display units.
_UNITS_BY_TYPE = {1: "s", 2: "Hz", 3: "V", 4: "A"}
_SCALE_NAMES = ("time", "frequency")
_STAT_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")


@dataclass
class SyncHandlers:
    """Python side of a sync-mode run. The callbacks run on ngspice's thread.

    ``data(time, values)``: one accepted time point; ``values`` maps lower-case
    ngspice vector names (``time``, ``v(out)``, ``v_x#branch``, ...) to real values.
    ``source(name, time)``: the value of the ``external`` source ``name`` (lower
    case). ngspice asks at trial points too, rejected ones included, so it must only
    return a held value.
    """

    data: Callable[[float, dict[str, float]], None] | None = None
    source: Callable[[str, float], float] | None = None
    errors: list[str] = field(default_factory=list)
    """Exceptions raised by the callbacks (they never unwind into ngspice)."""


def tran_card(ts: float, t_stop: float, max_step: float | None = None, *, uic: bool = False) -> str:
    """The ``.tran`` card for a ticked run that ``NgspiceAdapter.run_ticks`` ends.

    The largest internal step is Ts, or ``max_step`` when that is smaller. ``uic``
    (M6): start from the elements' ``ic=`` values instead of an operating point;
    ngspice then sends no point at t = 0, its first point is the first step.
    """
    tmax = ts if not max_step or max_step >= ts else float(max_step)
    return f".tran {ts!r} {t_stop + 2 * ts!r} 0 {tmax!r}" + (" uic" if uic else "")


def library_candidates() -> list[Path | str]:
    """Places to look for the ngspice shared library, most specific first."""
    candidates: list[Path | str] = []
    env = os.environ.get(LIBRARY_ENV_VAR)
    if env:
        candidates.append(Path(env))

    if sys.platform == "win32":
        names = ["ngspice.dll", "libngspice-0.dll", "libngspice.dll"]
    elif sys.platform == "darwin":
        names = ["libngspice.dylib", "libngspice.0.dylib"]
    else:
        names = ["libngspice.so", "libngspice.so.0"]

    bundle = getattr(sys, "_MEIPASS", None)
    roots = [Path(bundle) / "ngspice"] if bundle else []
    roots += [_THIRD_PARTY, _THIRD_PARTY / "lib", _THIRD_PARTY / "bin"]
    if sys.platform == "darwin":
        roots += [Path("/opt/homebrew/lib"), Path("/usr/local/lib")]
    for root in roots:
        candidates += [root / name for name in names]
    if bundle:
        # A packaged app keeps the library under its real file name, which may carry
        # a full version (libngspice.so.0.0.0, libngspice.0.dylib, ...).
        bundled = Path(bundle) / "ngspice"
        candidates += sorted(
            p
            for p in bundled.glob("*ngspice*")
            if p.suffix in (".dll", ".dylib") or ".so" in p.suffixes
        )

    found = ctypes.util.find_library("ngspice")
    if found:
        candidates.append(found)
    candidates += names  # let the OS loader search its normal path
    return candidates


def _load_library() -> tuple[ctypes.CDLL | None, str]:
    errors: list[str] = []
    for candidate in library_candidates():
        if isinstance(candidate, Path) and not candidate.is_file():
            continue
        try:
            if isinstance(candidate, Path) and sys.platform == "win32":
                # Dependent DLLs (e.g. libgomp) usually sit next to ngspice.dll.
                with os.add_dll_directory(str(candidate.parent)):
                    return ctypes.CDLL(str(candidate)), ""
            return ctypes.CDLL(str(candidate)), ""
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    detail = "; ".join(errors) if errors else "no ngspice shared library found"
    return None, detail


class _Session:
    """The one ngspice instance in this process.

    ngspice is process-global: ngSpice_Init must run once, and the callback objects
    it receives must stay alive for as long as the library is loaded. Callbacks are
    forwarded to the adapter that last loaded a deck (``owner``). ``mode`` is the
    mode the first caller asked for (PLAIN or SYNC).
    """

    def __init__(self) -> None:
        self.lib: ctypes.CDLL | None = None
        self.error = ""
        self.tried = False
        self.mode = ""
        self.owner: NgspiceAdapter | None = None
        self.lock = threading.Lock()
        self.ident = ctypes.c_int(0)
        self.callbacks = (
            _SendChar(lambda text, ident, user: self._forward("_send_char", text, ident, user)),
            _SendStat(lambda text, ident, user: self._forward("_send_stat", text, ident, user)),
            _ControlledExit(
                lambda status, unload, quit_, ident, user: self._forward(
                    "_controlled_exit", status, unload, quit_, ident, user
                )
            ),
            _BGThreadRunning(
                lambda running, ident, user: self._forward("_bg_running", running, ident, user)
            ),
        )
        self.sync_callbacks = (
            _SendData(lambda data, _count, _ident, _user: self._forward("_send_data", data)),
            _SendInitData(lambda *_: 0),
            _GetSRCData(lambda ret, t, name, _i, _u: self._forward("_get_src", ret, t, name)),
            _GetSRCData(lambda ret, t, name, _i, _u: self._forward("_get_src", ret, t, name)),
            _GetSyncData(
                lambda _t, _d, _o, redo, _i, loc, _u: self._forward("_get_sync", redo, loc)
            ),
        )

    def _forward(self, method: str, *args: object) -> int:
        owner = self.owner
        if owner is None:
            return 0
        try:
            return int(getattr(owner, method)(*args))
        except Exception:  # never let a Python error unwind into ngspice
            return 0

    def ensure(self, mode: str = PLAIN) -> ctypes.CDLL | None:
        """The library, set up by the first call in that call's ``mode``."""
        with self.lock:
            if self.tried:
                return self.lib
            self.tried = True
            lib, error = _load_library()
            if lib is None:
                self.error = error
                return None
            try:
                _declare(lib)
                send_char, send_stat, controlled_exit, bg_running = self.callbacks
                if mode == SYNC:
                    send_data, send_init, vsrc, isrc, sync = self.sync_callbacks
                    status = lib.ngSpice_Init(
                        send_char,
                        send_stat,
                        controlled_exit,
                        send_data,
                        send_init,
                        bg_running,
                        None,
                    )
                    if status == 0:
                        status = lib.ngSpice_Init_Sync(
                            vsrc, isrc, sync, ctypes.byref(self.ident), None
                        )
                else:
                    status = lib.ngSpice_Init(
                        send_char, send_stat, controlled_exit, None, None, bg_running, None
                    )
            except (AttributeError, OSError) as exc:
                self.error = f"not an ngspice shared library: {exc}"
                return None
            if status != 0:
                self.error = f"ngSpice_Init returned {status}"
                return None
            self.lib = lib
            self.mode = mode
            return lib


_SESSION = _Session()


def _declare(lib: ctypes.CDLL) -> None:
    lib.ngSpice_Init.argtypes = [
        _SendChar,
        _SendStat,
        _ControlledExit,
        ctypes.c_void_p,  # SendData*: NULL in plain mode
        ctypes.c_void_p,  # SendInitData*: NULL in plain mode
        _BGThreadRunning,
        ctypes.c_void_p,
    ]
    lib.ngSpice_Init.restype = ctypes.c_int
    lib.ngSpice_Init_Sync.argtypes = [
        _GetSRCData,
        _GetSRCData,
        _GetSyncData,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p,
    ]
    lib.ngSpice_Init_Sync.restype = ctypes.c_int
    lib.ngSpice_SetBkpt.argtypes = [ctypes.c_double]
    lib.ngSpice_SetBkpt.restype = ctypes.c_bool
    lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
    lib.ngSpice_Command.restype = ctypes.c_int
    lib.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
    lib.ngSpice_Circ.restype = ctypes.c_int
    lib.ngGet_Vec_Info.argtypes = [ctypes.c_char_p]
    lib.ngGet_Vec_Info.restype = ctypes.POINTER(_VectorInfo)
    lib.ngSpice_CurPlot.argtypes = []
    lib.ngSpice_CurPlot.restype = ctypes.c_char_p
    lib.ngSpice_AllVecs.argtypes = [ctypes.c_char_p]
    lib.ngSpice_AllVecs.restype = ctypes.POINTER(ctypes.c_char_p)
    lib.ngSpice_running.argtypes = []
    lib.ngSpice_running.restype = ctypes.c_bool


def compat_command(mode: str) -> str:
    """The ngspice command that selects compatibility ``mode`` ("" = off)."""
    if not valid_compat_mode(mode):
        raise SimulationError(f"invalid ngspice compatibility mode {mode!r}")
    return f"set ngbehavior={mode}" if mode else "unset ngbehavior"


def _decode(raw: bytes | None) -> str:
    return raw.decode("utf-8", errors="replace") if raw else ""


# Device currents saved by ".options savecurrents" (schematic runs, S11): @r1[i],
# @q1[ic], @m1[id], @d1[id], @i1[current] ... (only devices at the top level).
_DEVICE_CURRENT = re.compile(r"^@([^\[\]]+)\[([a-z]+)\]$", re.IGNORECASE)
_TERMINAL_CURRENTS = frozenset({"ic", "ib", "ie", "is", "id", "ig"})


def display_name(raw: str, unit: str) -> str:
    """Turn ngspice vector names into what users write: out -> V(out), v1#branch -> I(v1),
    @r1[i] -> I(r1), @q1[ic] -> Ic(q1), @m1[id] -> Id(m1), @d1[id] -> I(d1)."""
    if raw.lower().endswith("#branch"):
        return f"I({raw[: -len('#branch')]})"
    device = _DEVICE_CURRENT.match(raw)
    if device is not None:
        name, what = device.group(1), device.group(2).lower()
        if what in ("i", "current") or (what == "id" and name[:1].lower() == "d"):
            return f"I({name})"
        if what in _TERMINAL_CURRENTS:
            return f"{what.capitalize()}({name})"
        return raw
    if unit == "V" and "(" not in raw and "#" not in raw:
        return f"V({raw})"
    return raw


class NgspiceAdapter(SimulatorBackend):
    """SimulatorBackend for the ngspice shared library.

    ngspice keeps one circuit per process, so use one adapter at a time; the adapter
    that last loaded a deck receives ngspice's callbacks.
    Console lines are collected in ``console`` and also sent to ``on_message``.
    ``compat_mode`` is sent as ``set ngbehavior=<mode>`` before each load ("" sends
    ``unset ngbehavior``); ngspice reads it on every circuit load.

    ``sync=True`` asks for the sync mode (see the module docstring). A process uses
    one mode only: an adapter whose mode differs from the one ngspice was set up with
    is not ``available()``.
    """

    def __init__(
        self,
        on_message: MessageCallback | None = None,
        on_progress: ProgressCallback | None = None,
        compat_mode: str = DEFAULT_COMPAT_MODE,
        *,
        sync: bool = False,
        handlers: SyncHandlers | None = None,
    ) -> None:
        super().__init__(on_message, on_progress)
        self.compat_mode = compat_mode
        self.mode = SYNC if sync else PLAIN
        self.handlers = handlers or SyncHandlers()
        self.console: list[str] = []
        self.last_error = ""
        self.redo_count = 0
        """Steps that ngspice rejected and redid in the last run (sync mode)."""
        self._lib: ctypes.CDLL | None = None
        self._loaded = False
        self._result: SimulationResult | None = None
        self._stop_requested = threading.Event()
        self._bg_done = threading.Event()
        self._bg_calls = 0
        self._paused = False
        self._halted_for_stop = False
        self._started_at = 0.0
        self._errors_before = 0
        self._rejected = False
        self._lock = threading.Lock()

    # --- SimulatorBackend --------------------------------------------------

    def available(self) -> bool:
        try:
            return self._ensure_library() is not None
        except Exception as exc:  # available() must never raise
            self.last_error = str(exc)
            return False

    def load(self, deck: str) -> None:
        lib = self._require()
        if lib.ngSpice_running():
            raise SimulationError("ngspice is still running another simulation")
        # Rule 4: ngspice reads numbers with the C runtime's strtod.
        with contextlib.suppress(locale.Error):
            locale.setlocale(locale.LC_NUMERIC, "C")
        _SESSION.owner = self
        self._stop_requested.clear()
        self._result = None
        self._loaded = False
        self._rejected = False
        self.redo_count = 0
        self.console.clear()
        lines = deck.splitlines()
        if not lines or not lines[-1].strip().lower().startswith(".end"):
            lines.append(".end")
        self._command("destroy all")
        if self.mode == SYNC:
            self._command("delete all")  # the previous run's `stop when`
        self._command(compat_command(self.compat_mode))
        errors_before = self._error_count()
        array = (ctypes.c_char_p * (len(lines) + 1))(
            *[line.encode("utf-8") for line in lines], None
        )
        if lib.ngSpice_Circ(array) != 0 or self._error_count() > errors_before:
            raise SimulationError(self._first_error() or "ngspice could not load the deck")
        self._loaded = True

    def run(self) -> None:
        self.start()
        self.wait()

    def results(self) -> SimulationResult | None:
        return self._result

    def stop(self) -> None:
        # Only sets a flag: wait() sends bg_halt from its own thread within ~20 ms.
        self._stop_requested.set()

    # --- background runs (C1) ----------------------------------------------

    def start(self) -> None:
        """Start the loaded deck on ngspice's background thread (returns at once)."""
        self._require()
        if not self._loaded or _SESSION.owner is not self:
            raise SimulationError("no deck loaded")
        if self._stop_requested.is_set():  # stop() arrived before the run started
            raise SimulationError("simulation stopped")
        self._errors_before = self._error_count()
        self._bg_calls = 0
        self._paused = False
        self._halted_for_stop = False
        self._bg_done.clear()
        self._started_at = time.monotonic()
        self._command("bg_run")

    def wait(self) -> None:
        """Block until the background run ends; then collect ``results()``.

        Raises SimulationError if it was stopped or ngspice reported an error.
        """
        lib = self._require()
        # bg_run returns before the background thread reports it is running, so
        # ngSpice_running() alone is racy. ngspice calls BGThreadRunning once when the
        # thread starts and once when it ends; wait for the second call. If no thread
        # ever starts (e.g. bg_run refused), fall back after a short grace period.
        # A stop request is turned into bg_halt here, on the thread that sent bg_run.
        while True:
            done = self._bg_done.wait(0.02)
            if self._stop_requested.is_set():
                if lib.ngSpice_running() and not self._halted_for_stop:
                    self._command("bg_halt")
                    self._halted_for_stop = True
                if done or not lib.ngSpice_running():
                    break
                continue
            if self._paused:
                time.sleep(0.02)
                continue
            if done:
                break
            no_thread = self._bg_calls == 0 and not lib.ngSpice_running()
            if no_thread and time.monotonic() - self._started_at > 2.0:
                break
        if self._stop_requested.is_set():
            raise SimulationError("simulation stopped")
        if self._error_count() > self._errors_before:
            raise SimulationError(self._first_error(self._errors_before) or "simulation failed")
        self._result = self._collect()
        if self.on_progress:
            self.on_progress(100)

    def halt(self, timeout: float = 5.0) -> bool:
        """Pause a background run (``bg_halt``); True once ngspice's thread has ended.

        ``wait()`` keeps waiting while the run is paused; ``resume()`` continues it.
        """
        lib = self._require()
        self._paused = True
        self._command("bg_halt")
        deadline = time.monotonic() + timeout
        while lib.ngSpice_running() or self._bg_calls % 2:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    def resume(self) -> None:
        """Continue a run paused with ``halt()`` (``bg_resume``)."""
        self._require()
        if not self._paused:
            return
        self._bg_done.clear()
        self._paused = False
        self._command("bg_resume")

    @property
    def running(self) -> bool:
        """True while ngspice's background thread is simulating."""
        lib = self._ensure_library()
        return bool(lib is not None and lib.ngSpice_running())

    def set_breakpoint(self, t: float) -> bool:
        """``ngSpice_SetBkpt(t)``: ngspice lands a time point exactly on ``t``."""
        return bool(self._require().ngSpice_SetBkpt(float(t)))

    def run_ticks(self, t_stop: float, ts: float, *, background: bool = False) -> None:
        """Run a deck made with ``tran_card(ts, t_stop)`` and end it after the tick at
        ``t_stop``: ngspice's ``stop when`` halts it half a tick after that tick.

        With ``background=True`` this only starts the run; call ``wait()``.
        """
        self._require()
        if not self._loaded:
            raise SimulationError("no deck loaded")
        self._command(f"stop when time > {t_stop + 0.5 * ts!r}")
        self.start()
        if not background:
            self.wait()

    def command(self, text: str) -> int:
        """Send one ngspice command (e.g. ``print``); returns ngspice's status."""
        self._require()
        return self._command(text)

    # --- internals -----------------------------------------------------------

    def _ensure_library(self) -> ctypes.CDLL | None:
        if self._lib is None:
            lib = _SESSION.ensure(self.mode)
            if lib is None:
                self.last_error = _SESSION.error
                return None
            if _SESSION.mode != self.mode:
                self.last_error = (
                    f"ngspice is already set up in {_SESSION.mode} mode in this process; "
                    f"a {self.mode} run needs a process of its own"
                )
                return None
            self._lib = lib
        return self._lib

    def _require(self) -> ctypes.CDLL:
        lib = self._ensure_library()
        if lib is None:
            raise SimulationError(f"ngspice is not available: {self.last_error}")
        return lib

    def _command(self, command: str) -> int:
        assert self._lib is not None
        return int(self._lib.ngSpice_Command(command.encode("utf-8")))

    def _error_lines(self) -> list[str]:
        with self._lock:
            return [line for line in self.console if line.lower().startswith("error")]

    def _error_count(self) -> int:
        return len(self._error_lines())

    def _first_error(self, after: int = 0) -> str:
        errors = self._error_lines()[after:]
        return errors[0] if errors else ""

    def _collect(self) -> SimulationResult:
        assert self._lib is not None
        plot = _decode(self._lib.ngSpice_CurPlot())
        names_ptr = self._lib.ngSpice_AllVecs(plot.encode("utf-8"))
        raw_names: list[str] = []
        i = 0
        while names_ptr and names_ptr[i]:
            raw_names.append(_decode(names_ptr[i]))
            i += 1

        analysis = re.sub(r"\d+$", "", plot).lower()
        vectors: dict[str, np.ndarray] = {}
        units: dict[str, str] = {}
        axis = ""
        for raw in raw_names:
            info_ptr = self._lib.ngGet_Vec_Info(f"{plot}.{raw}".encode())
            if not info_ptr:
                continue
            info = info_ptr.contents
            n = info.v_length
            if info.v_realdata:
                data = np.ctypeslib.as_array(info.v_realdata, shape=(n,)).copy()
            elif info.v_compdata:
                pairs = np.ctypeslib.as_array(
                    ctypes.cast(info.v_compdata, ctypes.POINTER(ctypes.c_double)), shape=(2 * n,)
                )
                data = pairs[0::2] + 1j * pairs[1::2]
            else:
                data = np.zeros(0)
            unit = _UNITS_BY_TYPE.get(info.v_type, "")
            is_scale = raw.lower() in _SCALE_NAMES or raw.lower().endswith("-sweep")
            name = raw if is_scale else display_name(raw, unit)
            if is_scale and not axis:
                axis = name
                if np.iscomplexobj(data):  # ac frequency is stored as complex
                    data = data.real.copy()
            vectors[name] = data
            units[name] = unit

        if axis:  # axis first
            vectors = {axis: vectors[axis], **{k: v for k, v in vectors.items() if k != axis}}
        return SimulationResult(axis=axis, vectors=vectors, units=units, analysis=analysis)

    # --- ngspice callbacks (may run on ngspice's background thread) ----------

    def _send_char(self, text: bytes | None, _ident: int, _user: int | None) -> int:
        line = _decode(text)
        for prefix in ("stdout ", "stderr "):
            if line.startswith(prefix):
                line = line[len(prefix) :]
                break
        with self._lock:
            self.console.append(line)
        if self.on_message:
            with contextlib.suppress(Exception):
                self.on_message(line)
        return 0

    def _send_stat(self, text: bytes | None, _ident: int, _user: int | None) -> int:
        match = _STAT_PERCENT.search(_decode(text))
        if match and self.on_progress:
            with contextlib.suppress(Exception):
                self.on_progress(min(100, int(float(match.group(1)))))
        return 0

    def _controlled_exit(
        self, status: int, _unload: bool, quit_: bool, _ident: int, _user: int | None
    ) -> int:
        if not quit_:
            self.last_error = f"ngspice requested exit with status {status}"
        return 0

    def _bg_running(self, _running: bool, _ident: int, _user: int | None) -> int:
        # The flag's meaning is inverted in some ngspice versions, so count calls.
        self._bg_calls += 1
        if self._bg_calls >= 2 and self._bg_calls % 2 == 0:
            self._bg_done.set()
        return 0

    def _get_sync(self, redo: int, location: int) -> int:
        # location 0: before a step; location 1: after it, redostep says whether ngspice
        # rejected it. The SendData of an accepted point follows location 1, redo 0.
        if location == 1:
            self._rejected = bool(redo)
            if redo:
                self.redo_count += 1
        return 0  # never ask for a redo (see the module docstring)

    def _send_data(self, pdata: ctypes._Pointer) -> int:  # type: ignore[type-arg]
        handler = self.handlers.data
        if handler is None or self._rejected:
            return 0
        try:
            all_ = pdata.contents
            values: dict[str, float] = {}
            for i in range(all_.veccount):
                vec = all_.vecsa[i].contents
                values[_decode(vec.name).lower()] = vec.creal
            handler(values.get("time", 0.0), values)
        except Exception as exc:
            self.handlers.errors.append(f"{type(exc).__name__}: {exc}")
        return 0

    def _get_src(self, ret: ctypes._Pointer, t: float, name: bytes | None) -> int:  # type: ignore[type-arg]
        handler = self.handlers.source
        if handler is None:
            return 0
        try:
            ret[0] = float(handler(_decode(name).lower(), t))
        except Exception as exc:
            self.handlers.errors.append(f"{type(exc).__name__}: {exc}")
        return 0
