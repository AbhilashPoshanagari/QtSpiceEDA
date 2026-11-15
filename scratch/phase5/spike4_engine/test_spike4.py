"""S4 spike checks: `pytest -q scratch/phase5/spike4_engine` (not part of `pytest -q`).

The measurements are run_all.py; these only pin down what the README claims.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("dss")

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

import spike4a_amps as a  # noqa: E402
import spike4b_voltage_bases as b  # noqa: E402
import spike4c_sequence as c  # noqa: E402
import spike4d_loading as d  # noqa: E402
import spike4e_compile_errors as e  # noqa: E402
from common import FEEDERS  # noqa: E402

pytestmark = pytest.mark.opendss


@pytest.mark.parametrize("feeder", FEEDERS)
def test_a_ratings_typed_default_and_saved(feeder: str) -> None:
    r = a.feeder_case(feeder)
    assert r["typed_equals_properties"] and r["typed_equals_cktelement"]
    assert r["survive_save_circuit"]
    assert set(r["values"]) == {"line 400/600", "linecode 400/600"}  # all defaults
    assert r["linecodes_with_normamps_set"] == 0


def test_a_linecode_after_normamps_resets_the_line_rating() -> None:
    r = a.non_default_case()
    assert r["line.after"]["typed"] == (111.0, 222.0)
    assert r["line.before"]["typed"] == (250.0, 350.0)  # linecode= came later and won
    assert all(v["after_save"] == v["typed"] for v in r.values())
    assert (
        r["linecode.lc_typed"]["set_per_tojson"] and not r["linecode.lc_default"]["set_per_tojson"]
    )


def test_b_derived_bases_same_kvbase_except_single_phase_ll_on_ieee123() -> None:
    for feeder in FEEDERS:
        v = b.feeder_case(feeder)["variants"]
        for variant in ("as_stored", "three_phase_only", "ll_3ph_first"):
            tight = v[variant]["tight"]
            assert tight["buses_with_other_kvbase"] == 0, (feeder, variant)
            assert tight["max_pu_vs_original_same_solve"] < 1e-8, (feeder, variant)
            assert tight["max_pu_vs_reference"] < 2e-5
        if feeder == "ieee123":
            assert v["ll"]["tight"]["buses_with_other_kvbase"] == 130
            assert v["ll"]["tight"]["max_pu_vs_original_same_solve"] > 9e-5


def test_c_textbook_matches_opendss_except_one_phase_lines() -> None:
    r = c.run()["max"]
    for kind in c.KINDS:
        for n in (1, 2, 3):
            textbook = r[f"{kind} n={n} textbook"]["rel"]
            if kind != "linecode" and n == 1:
                assert textbook > 0.1
                assert r[f"{kind} n=1 z1_only"]["rel"] < 1e-12
            else:
                assert textbook < 1e-12, (kind, n)


@pytest.mark.parametrize("feeder", FEEDERS)
def test_d_line_loading_equals_opendss_pctnorm(feeder: str) -> None:
    r = d.feeder_case(feeder)
    assert r["line_ours_vs_opendss_max_abs_pct"] < 1e-9
    for t in r["transformers"].values():
        assert t["normhkva"] == pytest.approx(1.1 * t["kva"])
        assert t["normal_amps"] == pytest.approx(1.1 * t["rated_i1"])


def test_d_signs_ieee13() -> None:
    s = d.feeder_case("ieee13")["signs"]
    assert s["vsource.source"][0] < 0 and s["circuit_total_power"][0] < 0
    assert s["load.671"][0] > 0
    assert s["capacitor.cap1"][1] < 0


def test_e_only_a_missing_linecode_is_an_error() -> None:
    r = e.run()
    assert "(#401)" in r["missing_linecode"]["by_compile"]["error"]
    silent = [k for k in e.CASES if k != "missing_linecode"]
    for key in silent:
        assert r[key]["by_command"]["errors"] == [], key
        assert r[key]["by_compile"]["error"] is None, key
        assert r[key]["by_command"]["solve"]["converged"], key
    assert r["load_kw_not_set"]["by_command"]["values"]["load.ld1.kW"] == "10"
    assert r["line_phases_vs_linecode"]["by_command"]["values"]["line.l1.phases"] == "3"
    assert r["writer_with_none"]["load.kw=None"]["raised"].startswith("TypeError")
