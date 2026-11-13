"""M5: DiagramController slots for the component editor (user physical components)."""

from __future__ import annotations

import numpy as np
import pytest

from bridge.diagram_controller import USER_PHYSICAL_GROUP, DiagramController, DiagramState
from tests.core.physical.test_user import TC_RESISTOR

ADD_SUB = '@block(outputs=["s"])\ndef add_sub(a, b): return a + b\n'


@pytest.fixture
def ctl(qapp: object) -> DiagramController:
    c = DiagramController()
    yield c
    c.shutdown()


def entry(ctl: DiagramController, name: str) -> dict | None:
    return next((b for b in ctl.property("library") if b["name"] == name), None)


def test_apply_component_adds_a_user_physical_block(ctl: DiagramController) -> None:
    assert ctl.applyComponent(TC_RESISTOR) is True
    block = entry(ctl, "TCThermalResistor")
    assert block is not None
    assert block["group"] == USER_PHYSICAL_GROUP and block["physical"] is True
    assert block["builtin"] is False
    assert [p["name"] for p in block["conserving"]] == ["A", "B"]
    assert ctl.isUserComponent("TCThermalResistor") is True
    assert ctl.isUserComponent("Resistor") is False
    assert USER_PHYSICAL_GROUP in ctl.property("libraryGroups")
    assert "TCThermalResistor" in ctl.property("componentStatusText")
    assert ctl.property("componentDiagnostics") == []


def test_component_errors_keep_the_last_good_version(ctl: DiagramController) -> None:
    ctl.applyComponent(TC_RESISTOR)
    assert ctl.applyComponent(TC_RESISTOR.replace("return [Behavioural", "return [eval")) is False
    (diag,) = ctl.property("componentDiagnostics")
    assert diag["severity"] == "error" and "eval" in diag["message"]
    assert (
        diag["line"]
        == TC_RESISTOR.splitlines().index(
            next(x for x in TC_RESISTOR.splitlines() if "return [Behavioural" in x)
        )
        + 1
    )
    assert entry(ctl, "TCThermalResistor") is not None
    assert "not updated" in ctl.property("componentStatusText")
    # the function editor's diagnostics are separate
    assert ctl.property("diagnostics") == []


def test_names_of_blocks_and_components_do_not_clash(ctl: DiagramController) -> None:
    assert ctl.apply(ADD_SUB)
    clash = TC_RESISTOR.replace("class TCThermalResistor", "class add_sub")
    assert ctl.applyComponent(clash) is False
    assert "a block is already named" in ctl.property("componentDiagnostics")[0]["message"]
    assert ctl.applyComponent(TC_RESISTOR)
    block = "@block\ndef TCThermalResistor(a): return a\n"
    assert ctl.check(block) is False
    assert "physical component" in ctl.property("diagnostics")[0]["message"]


def test_new_and_open_component_documents(ctl: DiagramController) -> None:
    first = ctl.property("componentDocument")
    ctl.applyComponent(TC_RESISTOR)
    second = ctl.newComponentDocument()
    assert second != first
    assert ctl.openComponent("TCThermalResistor") == TC_RESISTOR
    assert ctl.property("componentDocument") == first
    assert ctl.openComponent("Resistor") == ""


def test_project_state_keeps_the_component_code_and_the_placed_block(
    ctl: DiagramController, qapp: object
) -> None:
    ctl.applyComponent(TC_RESISTOR)
    block_id = ctl.addBlock("TCThermalResistor", 0, 0)
    state = ctl.export_state()
    assert state.physical_sources == [(ctl.property("componentDocument"), TC_RESISTOR)]

    other = DiagramController()
    try:
        replaced: list[str] = []
        other.componentDocumentReplaced.connect(replaced.append)
        warnings = other.load_state(state)
        assert warnings == []
        assert [b.id for b in other.diagram.blocks] == [block_id]
        assert replaced == [TC_RESISTOR]
        assert other.isUserComponent("TCThermalResistor")
        # loading another state forgets the components
        other.load_state(DiagramState())
        assert not other.isUserComponent("TCThermalResistor")
        assert entry(other, "TCThermalResistor") is None
    finally:
        other.shutdown()


def test_bad_component_code_in_a_project_is_a_warning(ctl: DiagramController) -> None:
    warnings = ctl.load_state(DiagramState(physical_sources=[("component1", "import os\n")]))
    assert any("component code 'component1' was not loaded" in w for w in warnings)


def _thermal_network(ctl: DiagramController) -> None:
    ids = {
        "tref": ctl.addBlock("ThermalReference", 0, 300),
        "solver": ctl.addBlock("SolverConfiguration", 100, 300),
        "amb": ctl.addBlock("TemperatureSource", 0, 0),
        "rth": ctl.addBlock("TCThermalResistor", 200, 0),
        "heat": ctl.addBlock("HeatFlowSource", 400, 200),
        "mass": ctl.addBlock("ThermalMass", 400, 0),
        "ts": ctl.addBlock("TemperatureSensor", 600, 0),
        "scope": ctl.addBlock("Scope", 800, 0),
    }
    pairs = [
        ("solver.port", "tref.R"),
        ("amb.B", "tref.R"),
        ("amb.A", "rth.B"),
        ("rth.A", "mass.A"),
        ("heat.A", "mass.A"),
        ("heat.B", "tref.R"),
        ("ts.A", "mass.A"),
        ("ts.B", "tref.R"),
    ]
    for a, b in pairs:
        ka, pa = a.split(".")
        kb, pb = b.split(".")
        assert ctl.connectPhysical(f"{ids[ka]}.{pa}", f"{ids[kb]}.{pb}"), (a, b)
    assert ctl.connectPorts(f"{ids['ts']}.T", f"{ids['scope']}.u")
    ctl.setSetting(ids["heat"], "Q", "50")
    assert ctl.setSetting(ids["scope"], "label", "T")


@pytest.mark.ngspice
def test_run_with_a_user_component_loads_it_in_the_runner(ctl: DiagramController, qtbot) -> None:
    assert ctl.applyComponent(TC_RESISTOR)
    _thermal_network(ctl)
    ctl.timeout = 120
    assert ctl.run(0.05, 1e-3) is True, ctl.property("runStatus")
    qtbot.waitUntil(lambda: not ctl.property("running"), timeout=120000)
    assert ctl.property("runStatus").startswith("Finished"), ctl.property("runDetails")
    assert "User components are checked when the run starts" in ctl.property("consoleText")
    temps = np.asarray(ctl.result.vectors["T"]).real  # type: ignore[union-attr]
    x = 50.0 * 1.0 / (1 - 50.0 * 1.0 * 0.004 / 2)
    assert temps[-1] == pytest.approx(298.15 + x, abs=1e-3)


@pytest.mark.ngspice
def test_a_failing_user_network_fails_the_run_with_its_message(
    ctl: DiagramController, qtbot
) -> None:
    assert ctl.applyComponent(TC_RESISTOR)
    _thermal_network(ctl)
    rth = next(b.id for b in ctl.diagram.blocks if b.spec_name == "TCThermalResistor")
    ctl.setSetting(rth, "R0", "0")
    ctl.timeout = 120
    assert ctl.run(0.05, 1e-3) is True  # passes the UI-side checks
    qtbot.waitUntil(lambda: not ctl.property("running"), timeout=120000)
    status = ctl.property("runStatus") + ctl.property("runDetails")
    assert "network() failed" in status and rth in status
