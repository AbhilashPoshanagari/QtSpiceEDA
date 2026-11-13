"""Spike 4: every IEEE 13 element can be read back through the API; the values that
need Properties() are exactly the listed ones; unit codes, switch, taps, coordinates
and voltage bases match the feeder files and the reference results."""

from __future__ import annotations

import common
import pytest
import spike4_api

pytestmark = pytest.mark.opendss


@pytest.fixture(scope="module")
def cov13() -> spike4_api.Coverage:
    return spike4_api.coverage("ieee13")


def _el(cov: spike4_api.Coverage, element_id: str) -> spike4_api.Element:
    return next(e for e in cov.elements if e.id.lower() == element_id)


def test_every_element_has_a_reader(cov13: spike4_api.Coverage) -> None:
    assert cov13.unsupported == []
    counts = {c: sum(1 for e in cov13.elements if e.cls == c) for c in spike4_api.READERS}
    assert counts == {
        "Vsource": 1,
        "LineCode": 36,  # 29 from IEEELineCodes.DSS + 7 local mtx6xx
        "Line": 12,
        "Transformer": 5,
        "RegControl": 3,
        "Load": 15,
        "Capacitor": 2,
        "PVSystem": 0,
    }


def test_values_that_need_properties(cov13: spike4_api.Coverage) -> None:
    assert cov13.props_needed() == {
        "Vsource": ["basefreq", "isc1", "isc3", "mvasc1", "mvasc3", "r0", "r1", "x0", "x1"],
        "LineCode": ["basefreq", "kron"],
        "Line": ["basefreq"],
        "Transformer": ["bank", "basefreq", "pct_imag", "pct_loadloss", "pct_noloadloss"],
        "RegControl": ["ptphase"],
        "Load": ["vlowpu"],
        "Capacitor": [],
    }


def test_unit_codes_and_conversion(cov13: spike4_api.Coverage) -> None:
    lc = _el(cov13, "linecode.mtx601").values
    line = _el(cov13, "line.650632").values
    assert lc["units"] == 1  # mi
    assert (line["units"], line["length"], line["linecode"]) == (5, 2000.0, "mtx601")  # ft
    # Lines.Rmatrix is reported per *line* unit: the linecode's ohm/mi over 5280 ft/mi.
    assert line["rmatrix"][0] * 5280 == pytest.approx(lc["rmatrix"][0], rel=1e-12)


def test_switch_keeps_its_impedance(cov13: spike4_api.Coverage) -> None:
    sw = _el(cov13, "line.671692").values
    assert sw["is_switch"] is True
    assert (sw["r1"], sw["r0"], sw["units"], sw["linecode"]) == (1e-4, 1e-4, 0, "")
    assert sw["open"] == [False, False]


def test_taps_before_and_after_solve(cov13: spike4_api.Coverage) -> None:
    ref = common.load_reference("ieee13")
    assert set(cov13.taps_before.values()) == {1.0}
    assert cov13.taps_after == pytest.approx(ref["taps"])
    reg = _el(cov13, "transformer.reg1").values
    assert reg["winding_list"][1]["tap"] == pytest.approx(ref["taps"]["reg1"])


def test_buses_and_bases(cov13: spike4_api.Coverage) -> None:
    ref = common.load_reference("ieee13")
    assert all(p is not None for p in cov13.bus_xy.values())
    assert cov13.voltage_bases == ref["voltage_bases"]
    assert cov13.base_frequency == ref["base_frequency"]
    assert cov13.bus_kv_base["671"] == pytest.approx(4.16 / 3**0.5)


@pytest.mark.parametrize("feeder", ["ieee34", "ieee123"])
def test_other_feeders_read_completely(feeder: str) -> None:
    cov = spike4_api.coverage(feeder)
    assert cov.unsupported == []
    assert cov.taps_after == pytest.approx(common.load_reference(feeder)["taps"])
    missing_xy = sorted(b for b, p in cov.bus_xy.items() if p is None)
    assert missing_xy == ([] if feeder == "ieee34" else ["300_open", "94_open"])
