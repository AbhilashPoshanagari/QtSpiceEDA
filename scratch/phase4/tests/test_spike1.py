"""Spike 1: IEEE 13 compiles and solves, and matches the OpenDSS reference file."""

from __future__ import annotations

import common
import pytest
import spike1_install

pytestmark = pytest.mark.opendss


def test_versions_are_the_pinned_ones() -> None:
    info = common.versions()
    assert info["dss_python"].startswith("0.15.")
    assert info["engine"].startswith("DSS C-API Library version")


def test_ieee13_matches_reference() -> None:
    ref = common.load_reference("ieee13")
    r = common.load_and_solve("ieee13")
    assert r.converged
    assert r.iterations == ref["iterations"]
    assert r.voltage_bases == ref["voltage_bases"]
    assert r.base_frequency == ref["base_frequency"]
    assert common.compare_to_reference(r, ref) < 1e-8
    assert r.total_power_kw == pytest.approx(ref["total_power_kw"], rel=1e-9)
    assert r.losses_kw == pytest.approx(ref["losses_kw"], rel=1e-9)


def test_bus_671_voltages_match_reference() -> None:
    ref = common.load_reference("ieee13")
    ctx = common.new_context()
    common.load_and_solve("ieee13", ctx)
    for node, v, pu in spike1_install.bus_voltages(ctx, "671"):
        i = ref["node_names"].index(node)
        assert complex(*ref["node_v"][i]) == pytest.approx(v, abs=1e-6)
        assert pu == pytest.approx(ref["node_pu"][i], abs=1e-9)


@pytest.mark.parametrize("feeder", ["ieee34", "ieee123"])
def test_other_feeders_match_reference(feeder: str) -> None:
    ref = common.load_reference(feeder)
    r = common.load_and_solve(feeder)
    assert r.converged
    assert common.compare_to_reference(r, ref) < 1e-8
    assert r.taps == pytest.approx(ref["taps"])
