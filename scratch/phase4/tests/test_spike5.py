"""Spike 5: DSS-Python reads "1.5" and writes numbers with a decimal point under a
German locale, with and without setlocale(LC_NUMERIC, "C")."""

from __future__ import annotations

import pytest
import spike5_locale

pytestmark = pytest.mark.opendss


@pytest.mark.parametrize("mode", ["c", "de", "de+c"])
def test_numbers_survive_the_locale(mode: str) -> None:
    r = spike5_locale.run_fresh(mode)
    if r.error == "no German locale installed":
        pytest.skip(r.error)
    assert r.error == ""
    print(f"{r.platform} {mode}: locale {r.locale_name!r}, decimal point {r.decimal_point!r}")
    assert r.load_kw_typed == 1.5
    assert r.line_length_typed == 1.5
    assert r.load_kw_props == "1.5"
    assert r.load_kw_query == "1.5"
    assert "kW=1.5" in r.saved_load_line
    assert r.saved_numbers_with_comma == []
    assert r.ieee13_max_dev < 1e-8
