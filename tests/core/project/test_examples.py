"""The sample projects in examples/: they load, check clean and give the expected
answers (v0.1: RC low-pass, diode rectifier, first-order feedback loop; v0.2: RC
low-pass schematic, op-amp amplifier with its own .lib, circuit symbol used twice;
v0.3: the physical samples, checked in test_examples_physical.py)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.blocks.runner import run_diagram
from core.hierarchy.symbolgen import SymbolGenerator
from core.netlist.builder import build
from core.netlist.parser import parse
from core.netlist.validate import validate
from core.project.io import Project, load_project
from core.schematic.netlister import netlist_schematic
from core.sim.decimate import to_db
from core.sim.ngspice_adapter import NgspiceAdapter
from core.sim.result import SimulationResult
from core.symbols.asy import AsyParser
from core.symbols.library import SymbolLibrary

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SAMPLES = [
    "rc_filter.qsp",
    "diode_rectifier.qsp",
    "first_order_feedback.qsp",
    "diode_log.qsp",
    "rlc_bandpass.qsp",
    "rc_filter_schematic.qsp",
    "hierarchy/rc2.qsp",
    "hierarchy/rclp_cell.qsp",
    "opamp_amp/opamp_amp.qsp",
    "buck_cpm/buck_cpm.qsp",
    # v0.3 physical samples (O2): tests in test_examples_physical.py
    "dc_motor_speed.qsp",
    "heatsink_warmup.qsp",
    "battery_discharge.qsp",
]
SCHEMATIC_SAMPLES = [
    "rlc_bandpass.qsp",
    "rc_filter_schematic.qsp",
    "hierarchy/rc2.qsp",
    "opamp_amp/opamp_amp.qsp",
    "buck_cpm/buck_cpm.qsp",
]
# Samples whose block diagram needs its own Python blocks (opening asks to trust them).
BLOCK_CODE_SAMPLES = ["buck_cpm/buck_cpm.qsp"]


def test_examples_folder_holds_only_the_listed_samples() -> None:
    """No stray .qsp ships in examples/: every project there is a listed sample."""
    found = sorted(p.relative_to(EXAMPLES).as_posix() for p in EXAMPLES.rglob("*.qsp"))
    assert found == sorted(SAMPLES)


def test_readme_describes_every_sample() -> None:
    readme = (EXAMPLES.parent / "README.md").read_text(encoding="utf-8")
    for name in SAMPLES:
        if name == "hierarchy/rclp_cell.qsp":  # mentioned inside the rc2 entry
            assert name in readme
        else:
            assert f"**`{name}`**" in readme, name


def load(name: str) -> Project:
    loaded = load_project(EXAMPLES / name)
    assert loaded.ok, loaded.errors
    assert loaded.warnings == []
    assert loaded.project is not None
    return loaded.project


def netlist(name: str) -> str:
    return load(name).netlists[0].text


def simulate(text: str, card: str | None = None) -> SimulationResult:
    result = parse(text)
    assert result.ok, result.diagnostics
    adapter = NgspiceAdapter()
    adapter.load(build(result.circuit, card))
    adapter.run()
    sim = adapter.results()
    assert sim is not None
    return sim


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_loads_and_has_block_code_only_if_listed(name: str) -> None:
    project = load(name)
    # Only built-in blocks, so opening the sample never asks to trust code, except in
    # the samples listed as needing their own blocks.
    has_code = any(s.code.strip() for s in project.block_sources)
    assert has_code == (name in BLOCK_CODE_SAMPLES)
    assert project.results == {}


@pytest.mark.parametrize(
    "name", ["rc_filter.qsp", "diode_rectifier.qsp", "diode_log.qsp", "rlc_bandpass.qsp"]
)
def test_sample_netlist_checks_clean(name: str) -> None:
    result = parse(netlist(name))
    assert result.diagnostics == []
    assert validate(result.circuit) == []


def test_feedback_diagram_matches_first_order_step_response() -> None:
    project = load("first_order_feedback.qsp")
    settings = project.analysis
    result = run_diagram(
        project.diagrams[0].diagram, {}, settings.diagram_t_end, settings.diagram_dt
    )
    t, y, r = result.vectors["time"], result.vectors["y"], result.vectors["r"]
    tau, t0 = 0.5, 0.5  # Gain k = 1/tau = 2, step at 0.5 s
    assert t[-1] == pytest.approx(4.0)
    np.testing.assert_array_equal(r, np.where(t >= t0, 1.0, 0.0))
    expected = np.where(t >= t0, 1 - np.exp(-(t - t0) / tau), 0.0)
    # Forward Euler with dt = 1 ms: within 0.5 % of the 1.0 final value everywhere.
    assert np.max(np.abs(y - expected)) < 5e-3
    assert y[-1] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.ngspice
def test_rc_filter_tran_and_ac() -> None:
    text = netlist("rc_filter.qsp")
    tran = simulate(text)  # the first card, .tran
    t, v = tran.vectors["time"], tran.vectors["V(out)"]
    assert np.interp(1e-3, t, v) == pytest.approx(1 - np.exp(-1), rel=1e-3)

    ac = simulate(text, ".ac dec 50 1 100k")
    f, db = ac.vectors["frequency"], to_db(ac.vectors["V(out)"])
    f_3db = np.interp(-3.0103, db[::-1], f[::-1])
    assert f_3db == pytest.approx(1 / (2 * np.pi * 1e-3), rel=0.01)


@pytest.mark.ngspice
def test_diode_rectifier_peak_and_ripple() -> None:
    result = simulate(netlist("diode_rectifier.qsp"))
    t, vin, vout = (result.vectors[k] for k in ("time", "V(in)", "V(out)"))
    assert vin.max() == pytest.approx(5.0, rel=1e-3)
    assert vout.min() > -0.05  # a half-wave rectifier never drives the output negative
    last = vout[t >= 0.08]  # the last cycle, once C1 has charged
    assert 4.0 < last.max() < 4.6  # 5 V minus one diode drop
    ripple = last.max() - last.min()
    assert 0.2 < ripple < 0.6  # ~ Vout / (f R C) = 0.4 V


# ---------------------------------------------------------------- H3 demo


HIERARCHY = EXAMPLES / "hierarchy"


def test_hierarchy_demo_symbol_matches_its_cell() -> None:
    """examples/hierarchy/rclp.{asy,sub} are what Make Symbol writes for rclp_cell.qsp."""
    cell = load("hierarchy/rclp_cell.qsp").schematic
    lib = SymbolLibrary()
    made = SymbolGenerator.from_schematic("rclp", cell, lambda n: lib.lookup(n)[0], HIERARCHY)
    assert made.ok, made.diagnostics
    sub = (HIERARCHY / "rclp.sub").read_bytes().decode("utf-8")
    asy = (HIERARCHY / "rclp.asy").read_bytes().decode("cp1252")
    assert sub == made.sub_text.replace("\n", "\r\n")
    assert asy == made.asy_text.replace("\n", "\r\n")
    assert AsyParser.parse(HIERARCHY / "rclp.asy")[1] == []


@pytest.mark.ngspice
def test_hierarchy_demo_is_second_order() -> None:
    project = load("hierarchy/rc2.qsp")
    assert project.symbols == ["rclp.asy"]
    lib = SymbolLibrary(project_files=[HIERARCHY / s for s in project.symbols])
    result = netlist_schematic(project.schematic, lambda n: lib.lookup(n)[0], HIERARCHY)
    assert result.ok, result.diagnostics
    adapter = NgspiceAdapter()
    adapter.load(result.deck)
    adapter.run()
    data = adapter.results()
    f = np.real(data.vectors["frequency"])
    db = to_db(data.vectors["V(out)"])
    f0 = 1 / (2 * np.pi * 1e3 * 1e-6)
    assert np.interp(np.log10(f0), np.log10(f), db) == pytest.approx(-9.54, abs=0.05)
    assert np.interp(5.0, np.log10(f), db) - np.interp(4.0, np.log10(f), db) == pytest.approx(
        -40, abs=0.5
    )


@pytest.mark.ngspice
def test_diode_log_sweeps_one_nanoamp_to_ten_milliamps() -> None:
    """examples/diode_log.qsp (W5): I(vm) spans about 1 nA .. 10 mA, shown on log Y."""
    project = load("diode_log.qsp")
    sim = simulate(project.netlists[0].text)
    current = sim.vectors["I(vm)"]
    assert current.min() == pytest.approx(1e-9, rel=0.2)
    assert current.max() == pytest.approx(1e-2, rel=0.3)
    panes = project.plots["dc"].panes
    assert [p.traces for p in panes] == [["V(in)"], ["I(vm)"]] and panes[1].y.log


# ---------------------------------------------------------------- v0.2 samples


def netlist_sample(name: str):
    """Netlist a schematic sample the way the app does: project symbols by absolute path,
    includes resolved from the project folder."""
    path = (EXAMPLES / name).resolve()
    project = load(name)
    lib = SymbolLibrary(project_files=[path.parent / s for s in project.symbols])
    result = netlist_schematic(project.schematic, lambda n: lib.lookup(n)[0], path.parent)
    assert result.ok, result.diagnostics
    return project, result


@pytest.mark.parametrize("name", SCHEMATIC_SAMPLES)
def test_schematic_sample_netlists_clean(name: str) -> None:
    project, result = netlist_sample(name)
    # The analysis comes from the schematic's directive; a panel card would replace it.
    assert project.analysis.card == ""
    assert sum(t.directive for t in project.schematic.texts) == 1


def test_opamp_sample_uses_its_own_symbol_and_library() -> None:
    project, result = netlist_sample("opamp_amp/opamp_amp.qsp")
    assert project.symbols == ["opamp5.asy"]
    # SpiceOrder in+ in- vcc vee out, not the drawing order (OUT is drawn first).
    assert "XU1 in inn vcc vee out opamp5" in result.deck.splitlines()
    assert ".subckt opamp5" in result.deck


@pytest.mark.ngspice
def test_rc_filter_schematic_corner_frequency() -> None:
    _, result = netlist_sample("rc_filter_schematic.qsp")
    adapter = NgspiceAdapter()
    adapter.load(result.deck)
    adapter.run()
    data = adapter.results()
    f = np.real(data.vectors["frequency"])
    db = to_db(data.vectors["V(out)"])
    f_3db = np.interp(-3.0103, db[::-1], f[::-1])
    assert f_3db == pytest.approx(1 / (2 * np.pi * 10e3 * 0.1e-6), rel=0.01)


@pytest.mark.ngspice
def test_opamp_sample_has_gain_ten() -> None:
    _, result = netlist_sample("opamp_amp/opamp_amp.qsp")
    adapter = NgspiceAdapter()
    adapter.load(result.deck)
    adapter.run()
    data = adapter.results()
    t = data.vectors["time"]
    vin, vout = np.real(data.vectors["V(in)"]), np.real(data.vectors["V(out)"])
    assert t[-1] == pytest.approx(5e-3)
    gain = 1 / (1 + 10 / 1e5)  # 10 with an open-loop gain of 100k
    np.testing.assert_allclose(vout, 10 * gain * vin, atol=2e-3)
    assert vout.max() == pytest.approx(5.0, rel=1e-3)


# ---------------------------------------------------------------- buck converter

BUCK = "buck_cpm/buck_cpm.qsp"


def time_mean(t: np.ndarray, x: np.ndarray, lo: float, hi: float) -> float:
    """Time-weighted mean of x over [lo, hi): ngspice's time points are not uniform."""
    m = (t >= lo) & (t < hi)
    return float(np.trapezoid(x[m], t[m]) / (t[m][-1] - t[m][0]))


def test_buck_sample_netlists_to_the_ltspice_topology() -> None:
    """The schematic is the circuit of SyncBuck_switching_CPM_CL.asc, with the
    sample's own stand-in models (no vendor files) and the inductor current sensed
    by Vsense."""
    project, result = netlist_sample(BUCK)
    assert sorted(project.symbols) == [
        "ccvs.asy",
        "cpm_modulator.asy",
        "dead_time.asy",
        "driver.asy",
        "nfet_pwr.asy",
    ]
    lines = set(result.text.splitlines())
    for line in [
        "XM1 in N001 sw nfet_pwr",
        "XM2 sw N002 0 nfet_pwr",
        "L1 sw N003 1u ic=0",
        "Vsense N003 N004 0",
        "RL N004 out 15m",
        "C1 N005 0 200u ic={Vref}",
        "Iload out 0 PULSE(0 2 110u 1u 1u 30u 160u)",
        "XU3 c1 bootstrap sw 0 N006 driver",
        "XU4 c2 in 0 0 N007 driver",
        "D1 in bootstrap schottky_1a",
        "Cb bootstrap sw 10u ic=5",
        "XU2 c c1 c2 dead_time",
        "XU1 ctrl vs c ramp CPM_modulator",
        "Hsense vs 0 Vsense {Rf}",
        "XU5 N008 fb in 0 ctrl ea_opamp",
        "R1 fb out 1.2k",
        "C3 fb ctrl 10p",
        "R2 fb N009 15k",
        "C2 N009 ctrl 220p",
        ".tran 10n 160u 100u 10n uic",
        ".param Rf=0.1 Vref=3.3",
    ]:
        assert line in lines, line
    # One library, included once; no B or A devices (QtSpice and ngspice both run it).
    assert sum(line.startswith(".include") for line in lines) == 1
    lib = (EXAMPLES / "buck_cpm" / "buck_cpm.lib").read_text(encoding="utf-8")
    assert parse("t\n.include buck_cpm.lib\n.end\n", EXAMPLES / "buck_cpm").diagnostics == []
    assert "LTC" not in lib and "LT1630" not in lib


def test_buck_symbols_match_their_subcircuits() -> None:
    """Each sample symbol's SpiceOrder pins are its .subckt ports, in order."""
    lib = (EXAMPLES / "buck_cpm" / "buck_cpm.lib").read_text(encoding="utf-8").lower()
    ports = {
        line.split()[1]: line.split()[2:] for line in lib.splitlines() if line.startswith(".subckt")
    }
    for asy in ["cpm_modulator", "dead_time", "driver", "nfet_pwr"]:
        symbol, diagnostics = AsyParser.parse(EXAMPLES / "buck_cpm" / f"{asy}.asy")
        assert diagnostics == []
        pins = [p.attrs["PinName"].lower() for p in symbol.pins]
        assert pins == ports[symbol.attr("Value").lower()], asy


def test_buck_block_diagram_regulates_and_takes_the_load_step() -> None:
    project = load(BUCK)
    sources = {s.document: s.code for s in project.block_sources}
    settings = project.analysis
    assert (settings.diagram_t_end, settings.diagram_dt) == (160e-6, 5e-9)
    result = run_diagram(
        project.diagrams[0].diagram, sources, settings.diagram_t_end, settings.diagram_dt
    )
    t, vout, il, ctrl = (result.vectors[k] for k in ("time", "vout", "iL", "ctrl"))
    assert time_mean(t, vout, 100e-6, 110e-6) == pytest.approx(3.3, abs=0.01)
    assert time_mean(t, il, 125e-6, 140e-6) == pytest.approx(2.0, abs=0.1)  # 2 A load
    assert time_mean(t, vout, 150e-6, 160e-6) == pytest.approx(3.3, abs=0.01)
    window = (t >= 100e-6) & (t < 160e-6)
    assert np.all(np.abs(vout[window] - 3.3) < 0.02)
    assert np.all((ctrl[window] > 1.0) & (ctrl[window] < 1.8))  # the CPM control range


# (Vin - Vout) * D * Ts / L = 1.7 V * 0.66 us / 1 uH: the ripple without subharmonics.
IDEAL_RIPPLE = 1.12


def run_buck_blocks(va: float, t_end: float) -> SimulationResult:
    project = load(BUCK)
    diagram = json.loads(json.dumps(project.diagrams[0].diagram))
    cpm = next(b for b in diagram["blocks"] if b["id"] == "CPM")
    assert cpm["settings"]["Va"] == 0.3  # the sample has slope compensation
    cpm["settings"]["Va"] = va
    sources = {s.document: s.code for s in project.block_sources}
    return run_diagram(diagram, sources, t_end, project.analysis.diagram_dt)


def test_buck_block_diagram_has_the_ideal_ripple() -> None:
    """Va = 0.3 V of slope compensation: the same ripple in every cycle."""
    result = run_buck_blocks(0.3, 110e-6)
    t, il = result.vectors["time"], result.vectors["iL"]
    m = t >= 90e-6
    assert il[m].max() - il[m].min() == pytest.approx(IDEAL_RIPPLE, rel=0.1)


def test_buck_block_diagram_without_slope_compensation_is_subharmonic() -> None:
    """Va = 0 (as in the original LTspice example) at D = 0.66: the peak current
    alternates from cycle to cycle, so the ripple is well above the ideal one."""
    result = run_buck_blocks(0.0, 110e-6)
    t, il = result.vectors["time"], result.vectors["iL"]
    m = t >= 90e-6
    assert il[m].max() - il[m].min() > 1.5 * IDEAL_RIPPLE


@pytest.mark.ngspice
def test_buck_schematic_regulates_and_takes_the_load_step() -> None:
    _, result = netlist_sample(BUCK)
    adapter = NgspiceAdapter()
    adapter.load(result.deck)
    adapter.run()
    data = adapter.results()
    t = np.real(data.vectors["time"])
    vout, il = np.real(data.vectors["V(out)"]), np.real(data.vectors["I(vsense)"])
    pwm = np.real(data.vectors["V(c)"])
    assert t[0] == pytest.approx(100e-6, abs=1e-8) and t[-1] == pytest.approx(160e-6)
    assert time_mean(t, vout, 100e-6, 110e-6) == pytest.approx(3.3, abs=0.01)
    assert time_mean(t, il, 125e-6, 140e-6) == pytest.approx(2.0, abs=0.1)
    assert time_mean(t, vout, 150e-6, 160e-6) == pytest.approx(3.3, abs=0.01)
    assert np.all(np.abs(vout - 3.3) < 0.02)
    steady = t < 110e-6
    assert il[steady].max() - il[steady].min() == pytest.approx(IDEAL_RIPPLE, rel=0.1)
    # The 1 MHz clock sets the latch at the start of every period.
    high = pwm > 2.5
    rises = t[1:][high[1:] & ~high[:-1]]
    assert len(rises) >= 59
    assert np.all(np.abs(rises * 1e6 - np.round(rises * 1e6)) < 0.02)
