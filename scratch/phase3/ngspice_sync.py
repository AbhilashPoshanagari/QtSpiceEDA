"""Scratch ctypes binding for ngspice's synchronised (co-simulation) API.

Spike code for v0.3 only. Nothing under app/, bridge/ or core/ imports this file.
Signatures follow third_party/ngspice/include/sharedspice.h:

    int ngSpice_Init(SendChar*, SendStat*, ControlledExit*, SendData*,
                     SendInitData*, BGThreadRunning*, void*)
    int ngSpice_Init_Sync(GetVSRCData*, GetISRCData*, GetSyncData*, int*, void*)
    typedef int (GetVSRCData)(double*, double, char*, int, void*);
    typedef int (GetISRCData)(double*, double, char*, int, void*);
    typedef int (GetSyncData)(double, double*, double, int, int, int, void*);
    typedef int (SendData)(pvecvaluesall, int, int, void*);
    NG_BOOL ngSpice_SetBkpt(double);

ngspice is global to its process, so make one ``SyncSession`` per process. The
spike tests run every spike in a fresh child process for that reason.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.sim.ngspice_adapter import library_candidates

# --- ctypes mirror of sharedspice.h ------------------------------------------


class VecValues(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("creal", ctypes.c_double),
        ("cimag", ctypes.c_double),
        ("is_scale", ctypes.c_bool),
        ("is_complex", ctypes.c_bool),
    ]


class VecValuesAll(ctypes.Structure):
    _fields_ = [
        ("veccount", ctypes.c_int),
        ("vecindex", ctypes.c_int),
        ("vecsa", ctypes.POINTER(ctypes.POINTER(VecValues))),
    ]


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


SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
SendStat = SendChar
ControlledExit = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p
)
SendData = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(VecValuesAll), ctypes.c_int, ctypes.c_int, ctypes.c_void_p
)
# typedef int (SendInitData)(pvecinfoall, int, void*);  struct not needed here.
SendInitData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
GetSRCData = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_double),
    ctypes.c_double,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_void_p,
)
GetSyncData = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
    ctypes.c_double,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
)


def load_library() -> ctypes.CDLL | None:
    """Find and load libngspice the same way the app does (or None)."""
    for candidate in library_candidates():
        if isinstance(candidate, Path) and not candidate.is_file():
            continue
        try:
            if isinstance(candidate, Path) and sys.platform == "win32":
                with os.add_dll_directory(str(candidate.parent)):
                    return ctypes.CDLL(str(candidate))
            return ctypes.CDLL(str(candidate))
        except OSError:
            continue
    return None


def tran_card(ts: float, t_stop: float) -> str:
    """The .tran card for a ticked run that ``SyncSession.run_ticks`` ends."""
    return f".tran {ts!r} {t_stop + 2 * ts!r} 0 {ts!r}"


# --- handler interface ---------------------------------------------------------


@dataclass
class Handlers:
    """Python callbacks. Every one of them is optional.

    vsrc(time, name) -> value      GetVSRCData / GetISRCData
    sync(time, delta, olddelta, redostep, location) -> (ret, new_delta | None)
    data(time, values: dict[str, float], index)    SendData (accepted points)
    """

    vsrc: Callable[[float, str], float] | None = None
    isrc: Callable[[float, str], float] | None = None
    sync: Callable[[float, float, float, int, int], tuple[int, float | None]] | None = None
    data: Callable[[float, dict[str, float], int], None] | None = None
    console: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class SyncSession:
    """One ngspice instance with the sync callbacks registered."""

    def __init__(self, handlers: Handlers, want_data: bool = True) -> None:
        lib = load_library()
        if lib is None:
            raise RuntimeError("ngspice shared library not found")
        self.lib = lib
        self.h = handlers
        self._declare()
        self._ident = ctypes.c_int(0)
        # Keep every CFUNCTYPE object alive for the life of the library.
        self._cb_char = SendChar(self._on_char)
        self._cb_stat = SendStat(lambda *_: 0)
        self._cb_exit = ControlledExit(self._on_exit)
        self._cb_data = SendData(self._on_data) if want_data else SendData()
        # Finding (ngspice 44): SendData is only called when SendInitData is not NULL.
        self._cb_init = SendInitData(lambda *_: 0) if want_data else SendInitData()
        self._cb_bg = BGThreadRunning(lambda *_: 0)
        self._cb_vsrc = GetSRCData(self._on_vsrc)
        self._cb_isrc = GetSRCData(self._on_isrc)
        self._cb_sync = GetSyncData(self._on_sync)
        status = lib.ngSpice_Init(
            self._cb_char,
            self._cb_stat,
            self._cb_exit,
            self._cb_data,
            self._cb_init,
            self._cb_bg,
            None,
        )
        if status != 0:
            raise RuntimeError(f"ngSpice_Init returned {status}")
        status = lib.ngSpice_Init_Sync(
            self._cb_vsrc, self._cb_isrc, self._cb_sync, ctypes.byref(self._ident), None
        )
        if status != 0:
            raise RuntimeError(f"ngSpice_Init_Sync returned {status}")

    def _declare(self) -> None:
        lib = self.lib
        lib.ngSpice_Init.argtypes = [
            SendChar,
            SendStat,
            ControlledExit,
            SendData,  # a NULL SendData is passed as SendData() (null pointer)
            SendInitData,
            BGThreadRunning,
            ctypes.c_void_p,
        ]
        lib.ngSpice_Init.restype = ctypes.c_int
        lib.ngSpice_Init_Sync.argtypes = [
            GetSRCData,
            GetSRCData,
            GetSyncData,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_void_p,
        ]
        lib.ngSpice_Init_Sync.restype = ctypes.c_int
        lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
        lib.ngSpice_Command.restype = ctypes.c_int
        lib.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
        lib.ngSpice_Circ.restype = ctypes.c_int
        lib.ngGet_Vec_Info.argtypes = [ctypes.c_char_p]
        lib.ngGet_Vec_Info.restype = ctypes.POINTER(_VectorInfo)
        lib.ngSpice_SetBkpt.argtypes = [ctypes.c_double]
        lib.ngSpice_SetBkpt.restype = ctypes.c_bool

    # --- callbacks (never let a Python error unwind into ngspice) -----------

    def _on_char(self, text: bytes | None, _ident: int, _user: object) -> int:
        line = text.decode("utf-8", "replace") if text else ""
        self.h.console.append(line)
        return 0

    def _on_exit(self, status: int, *_: object) -> int:
        self.h.errors.append(f"ngspice exit {status}")
        return 0

    def _on_vsrc(self, ret, time, name, _ident, _user) -> int:
        try:
            if self.h.vsrc is not None:
                ret[0] = self.h.vsrc(time, name.decode() if name else "")
        except Exception as exc:
            self.h.errors.append(f"vsrc: {exc!r}")
        return 0

    def _on_isrc(self, ret, time, name, _ident, _user) -> int:
        try:
            if self.h.isrc is not None:
                ret[0] = self.h.isrc(time, name.decode() if name else "")
        except Exception as exc:
            self.h.errors.append(f"isrc: {exc!r}")
        return 0

    def _on_sync(self, time, delta, olddelta, redo, _ident, location, _user) -> int:
        try:
            if self.h.sync is not None:
                ret, new_delta = self.h.sync(time, delta[0], olddelta, redo, location)
                if new_delta is not None:
                    delta[0] = new_delta
                return ret
        except Exception as exc:
            self.h.errors.append(f"sync: {exc!r}")
        return 0

    def _on_data(self, pall, _count, _ident, _user) -> int:
        try:
            if self.h.data is not None:
                all_ = pall.contents
                values: dict[str, float] = {}
                for i in range(all_.veccount):
                    v = all_.vecsa[i].contents
                    values[v.name.decode().lower()] = v.creal
                self.h.data(values.get("time", 0.0), values, all_.vecindex)
        except Exception as exc:
            self.h.errors.append(f"data: {exc!r}")
        return 0

    # --- commands ----------------------------------------------------------

    def command(self, text: str) -> int:
        return self.lib.ngSpice_Command(text.encode())

    def load(self, deck: str) -> None:
        lines = deck.strip().splitlines()
        if not lines[-1].strip().lower().startswith(".end"):
            lines.append(".end")
        arr = (ctypes.c_char_p * (len(lines) + 1))(*[ln.encode() for ln in lines], None)
        if self.lib.ngSpice_Circ(arr) != 0:
            raise RuntimeError("ngSpice_Circ failed:\n" + "\n".join(self.h.console[-20:]))

    def set_breakpoint(self, t: float) -> bool:
        return bool(self.lib.ngSpice_SetBkpt(t))

    def run(self) -> None:
        """Foreground run: callbacks arrive on this thread; returns when done."""
        self.command("run")

    def run_ticks(self, t_stop: float, ts: float) -> None:
        """Run a deck made with ``tran_card`` and stop just after the tick at t_stop.

        Finding (ngspice 46): with GetSyncData registered, ngspice often fails to end
        at the deck's tstop. Either it offers half the remaining gap again and again
        and aborts with "Timestep too small", or it lands ~127 ulp short of tstop
        (just outside its own 100-ulp "at final time" test) and loops forever with a
        delta below the float resolution of t; a delta returned from GetSyncData is
        clipped away there. So the deck runs two ticks past t_stop and ngspice's own
        ``stop when`` halts it half a tick after the last tick.
        """
        self.command(f"stop when time > {t_stop + 0.5 * ts!r}")
        self.command("run")

    def finished(self) -> bool:
        """True if the last run ended normally or at its ``stop when`` condition."""
        bad = ("aborted", "too small", "rror")
        return not any(any(b in line for b in bad) for line in self.h.console)

    def vector(self, name: str) -> np.ndarray:
        info = self.lib.ngGet_Vec_Info(name.encode())
        if not info:
            raise KeyError(name)
        v = info.contents
        return np.ctypeslib.as_array(v.v_realdata, shape=(v.v_length,)).copy()
