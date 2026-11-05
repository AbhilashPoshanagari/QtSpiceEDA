"""NgspiceAdapter: the only module that knows the ngspice shared-library API.

Signatures follow third_party/ngspice/include/sharedspice.h. The library is loaded
lazily with ctypes; if it cannot be found, available() returns False and nothing
else in the app breaks.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

from core.sim.backend import (
    MessageCallback,
    ProgressCallback,
    SimulationError,
    SimulatorBackend,
)
from core.sim.result import SimulationResult

# Environment variable that points at a specific ngspice shared library file.
LIBRARY_ENV_VAR = "QTSPICE_NGSPICE_LIB"

_THIRD_PARTY = Path(__file__).resolve().parents[2] / "third_party" / "ngspice"

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


# typedef int (SendChar)(char*, int, void*);  SendStat has the same shape.
_SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_SendStat = _SendChar
# typedef int (ControlledExit)(int, NG_BOOL, NG_BOOL, int, void*);
_ControlledExit = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p
)
# typedef int (BGThreadRunning)(NG_BOOL, int, void*);
_BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)

# v_type values from ngspice's sim.h (SV_*), mapped to display units.
_UNITS_BY_TYPE = {1: "s", 2: "Hz", 3: "V", 4: "A"}
_SCALE_NAMES = ("time", "frequency")
_STAT_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")


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
    forwarded to the adapter that last loaded a deck (``owner``).
    """

    def __init__(self) -> None:
        self.lib: ctypes.CDLL | None = None
        self.error = ""
        self.tried = False
        self.owner: NgspiceAdapter | None = None
        self.lock = threading.Lock()
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

    def _forward(self, method: str, *args: object) -> int:
        owner = self.owner
        if owner is None:
            return 0
        try:
            return int(getattr(owner, method)(*args))
        except Exception:  # never let a Python error unwind into ngspice
            return 0

    def ensure(self) -> ctypes.CDLL | None:
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
            return lib


_SESSION = _Session()


def _declare(lib: ctypes.CDLL) -> None:
    lib.ngSpice_Init.argtypes = [
        _SendChar,
        _SendStat,
        _ControlledExit,
        ctypes.c_void_p,  # SendData*: not used
        ctypes.c_void_p,  # SendInitData*: not used
        _BGThreadRunning,
        ctypes.c_void_p,
    ]
    lib.ngSpice_Init.restype = ctypes.c_int
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


def _decode(raw: bytes | None) -> str:
    return raw.decode("utf-8", errors="replace") if raw else ""


def display_name(raw: str, unit: str) -> str:
    """Turn ngspice vector names into what users write: out -> V(out), v1#branch -> I(v1)."""
    if raw.lower().endswith("#branch"):
        return f"I({raw[: -len('#branch')]})"
    if unit == "V" and "(" not in raw and "#" not in raw:
        return f"V({raw})"
    return raw


class NgspiceAdapter(SimulatorBackend):
    """SimulatorBackend for the ngspice shared library.

    ngspice keeps one circuit per process, so use one adapter at a time; the adapter
    that last loaded a deck receives ngspice's callbacks.
    Console lines are collected in ``console`` and also sent to ``on_message``.
    """

    def __init__(
        self,
        on_message: MessageCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        super().__init__(on_message, on_progress)
        self.console: list[str] = []
        self.last_error = ""
        self._lib: ctypes.CDLL | None = None
        self._loaded = False
        self._result: SimulationResult | None = None
        self._stop_requested = threading.Event()
        self._bg_done = threading.Event()
        self._bg_calls = 0
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
        _SESSION.owner = self
        self._stop_requested.clear()
        self._result = None
        self._loaded = False
        self.console.clear()
        lines = deck.splitlines()
        if not lines or not lines[-1].strip().lower().startswith(".end"):
            lines.append(".end")
        self._command("destroy all")
        errors_before = self._error_count()
        array = (ctypes.c_char_p * (len(lines) + 1))(
            *[line.encode("utf-8") for line in lines], None
        )
        if lib.ngSpice_Circ(array) != 0 or self._error_count() > errors_before:
            raise SimulationError(self._first_error() or "ngspice could not load the deck")
        self._loaded = True

    def run(self) -> None:
        lib = self._require()
        if not self._loaded or _SESSION.owner is not self:
            raise SimulationError("no deck loaded")
        if self._stop_requested.is_set():  # stop() arrived before the run started
            raise SimulationError("simulation stopped")
        errors_before = self._error_count()
        self._bg_calls = 0
        self._bg_done.clear()
        started = time.monotonic()
        halted = False
        self._command("bg_run")
        # bg_run returns before the background thread reports it is running, so
        # ngSpice_running() alone is racy. ngspice calls BGThreadRunning once when the
        # thread starts and once when it ends; wait for the second call. If no thread
        # ever starts (e.g. bg_run refused), fall back after a short grace period.
        # A stop request is turned into bg_halt here, on the thread that sent bg_run.
        while not self._bg_done.wait(0.02):
            running = lib.ngSpice_running()
            if self._stop_requested.is_set() and running and not halted:
                self._command("bg_halt")
                halted = True
            no_thread = self._bg_calls == 0 and not running
            if no_thread and time.monotonic() - started > 2.0:
                break
        if self._stop_requested.is_set():
            raise SimulationError("simulation stopped")
        if self._error_count() > errors_before:
            raise SimulationError(self._first_error(errors_before) or "simulation failed")
        self._result = self._collect()
        if self.on_progress:
            self.on_progress(100)

    def results(self) -> SimulationResult | None:
        return self._result

    def stop(self) -> None:
        # Only sets a flag: run() sends bg_halt from its own thread within ~20 ms.
        self._stop_requested.set()

    # --- internals -----------------------------------------------------------

    def _ensure_library(self) -> ctypes.CDLL | None:
        if self._lib is None:
            self._lib = _SESSION.ensure()
            if self._lib is None:
                self.last_error = _SESSION.error
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
        if self._bg_calls >= 2:
            self._bg_done.set()
        return 0
