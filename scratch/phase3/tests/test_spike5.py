"""Spike 5: process locale and setlocale(LC_NUMERIC, "C").

Each spike runs in a fresh spawned process (ngspice is global per
process). Timings are printed, never asserted."""

from __future__ import annotations

import sys

import pytest
import spike5_locale
from fresh import in_fresh_process

pytestmark = pytest.mark.ngspice


def test_spike5_c_locale_reads_numbers_correctly() -> None:
    r = in_fresh_process(spike5_locale.run, "c")
    assert r.values_ok, r
    assert not r.printed_comma


def test_spike5_setlocale_c_fixes_german_locale() -> None:
    r = in_fresh_process(spike5_locale.run, "de+c")
    if r.error == "no German locale installed":
        pytest.skip(r.error)
    assert r.decimal_point == "."
    assert r.values_ok, r
    assert not r.printed_comma


def test_spike5_german_locale_without_fix() -> None:
    r = in_fresh_process(spike5_locale.run, "de")
    if r.error == "no German locale installed":
        pytest.skip(r.error)
    assert r.decimal_point == ","
    print(
        f"{sys.platform} de: values_ok={r.values_ok} printed_comma={r.printed_comma} "
        f"values={r.values} printed={r.printed} error={r.error}"
    )
    if sys.platform == "win32":
        # ngspice.dll has its own static C runtime: Python's setlocale cannot reach it.
        assert r.values_ok and not r.printed_comma
