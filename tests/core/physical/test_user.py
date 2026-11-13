"""M5: user-defined physical components, read with ast and loaded in the runner."""

from __future__ import annotations

import math
import textwrap

import numpy as np
import pytest

from core.blocks.runner import BlockRunner, run_diagram
from core.netlist.model import ERROR
from core.physical.component import build_network, physical_spec_of
from core.physical.library import physical_registry
from core.physical.registry import PhysicalRegistry
from core.physical.user import (
    ComponentLoadError,
    UserComponents,
    load_components,
    read_component_source,
)
from tests.fixtures.physical.library import new_diagram, wire
from tests.fresh import in_fresh_process

TC_RESISTOR = textwrap.dedent(
    '''
    """A thermal resistor whose resistance rises with its mean temperature."""

    T0 = 298.15


    @physical_component
    class TCThermalResistor(PhysicalComponent):
        """Thermal resistance R0 * (1 + alpha * (Tm - T0)) between A and B, where Tm is
        the mean of the two port temperatures."""

        ports = (Port("A", THERMAL), Port("B", THERMAL))
        parameters = (
            Parameter("R0", 1.0, "K/W", "resistance at T0"),
            Parameter("alpha", 0.004, "1/K", "temperature coefficient"),
        )

        def network(self, ports, params):
            th = get_domain(THERMAL)
            r0 = th.resistance_from(float(params["R0"]))
            alpha = float(params["alpha"])
            if r0 <= 0:
                raise ValueError("R0 must be positive")
            mean = f"({th.across_expr(ports.A)} + {th.across_expr(ports.B)}) / 2"
            r = f"{r0!r} * (1 + {alpha!r} * ({mean} - {T0!r}))"
            heat = f"{th.across_expr(ports.A, ports.B)} / ({r})"
            return [Behavioural("R", ports.A, ports.B, "i", heat)]
    '''
)


def _messages(code: str) -> list[str]:
    _, diagnostics = read_component_source(textwrap.dedent(code))
    return [d.message for d in diagnostics if d.severity == ERROR]


# ---- reading


def test_spec_read_from_the_source_equals_the_loaded_class() -> None:
    specs, diagnostics = read_component_source(TC_RESISTOR)
    assert diagnostics == []
    (spec,) = specs
    assert spec.name == "TCThermalResistor"
    assert [p.name for p in spec.ports] == ["A", "B"]
    assert spec.defaults() == {"R0": 1.0, "alpha": 0.004}
    assert spec.block.color and spec.block.is_physical
    (cls,) = load_components({"doc1": TC_RESISTOR})
    assert physical_spec_of(cls) == spec


def test_imports_of_the_api_are_allowed() -> None:
    code = """
    import math
    from core.physical.component import PhysicalComponent, Port, Resistor, physical_component
    from core.physical.domains import ELECTRICAL, get_domain

    @physical_component(name="Half")
    class HalfOhm(PhysicalComponent):
        ports = (Port("p", ELECTRICAL), Port("n", domain="electrical"))

        def network(self, ports, params):
            return [Resistor("R", ports.p, ports.n, math.sqrt(0.25))]
    """
    specs, diagnostics = read_component_source(textwrap.dedent(code))
    assert diagnostics == [] and [s.name for s in specs] == ["Half"]
    (cls,) = load_components({"d": textwrap.dedent(code)})
    result = build_network(cls)
    assert result.ok and result.primitives[0].value == 0.5  # type: ignore[union-attr]


def test_syntax_error_has_its_line() -> None:
    _, diagnostics = read_component_source("x = (\n")
    assert diagnostics[0].severity == ERROR and diagnostics[0].message.startswith("syntax error")


def test_no_component_is_a_warning() -> None:
    specs, diagnostics = read_component_source("X = 1\n")
    assert specs == [] and [d.severity for d in diagnostics] == ["warning"]


_HEAD = """
@physical_component
class Part(PhysicalComponent):
"""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        # typed ports
        ('    ports = (Port("A"),)\n', "needs a domain"),
        ('    ports = (Port("A", "hydraulic"),)\n', "unknown domain 'hydraulic'"),
        ('    ports = (Port("A", "any"),)\n', "unknown domain 'any'"),
        ("    ports = make_ports()\n", "must be a tuple or list"),
        ('    ports = (SignalInput("u"),)\n', "needs at least one conserving Port"),
        ('    ports = (Port("A", THERMAL),)\n    role = "solver"\n', "role 'component'"),
        ('    ports = (Port("A", THERMAL),)\n    colour = 1\n', "unknown class attribute"),
        ('    ports = (Port("A", THERMAL),)\n    parameters = (Parameter("R"),)\n', "default"),
    ],
)
def test_declaration_errors(body: str, message: str) -> None:
    code = _HEAD + body + "\n    def network(self, ports, params):\n        return []\n"
    assert any(message in m for m in _messages(code)), _messages(code)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("return eval('1')", "'eval' is not available"),
        ("return [open('x')]", "'open' is not available"),
        ("return __import__('os')", "'__import__': names starting with '__'"),
        ("return ports.__class__", "attribute '__class__'"),
        ("return [SubcircuitInstance('X', (), 's', '')]", "SubcircuitInstance is for Circuit"),
        ("return compose(Part, 'p', {})", "compose() is for built-in"),
        ("import os", "import is not allowed"),
        ("global X", "global is not allowed"),
        ("return [Thing('R', 'A', '0', 1.0)]", "cannot call 'Thing'"),
        ("return [math.system('x')]", "math has no function 'system'"),
        ("return (lambda: 1)()", "only named functions"),
        ("print(params)", "'print' is not available"),
    ],
)
def test_network_uses_only_allowed_calls(line: str, message: str) -> None:
    code = (
        _HEAD
        + '    ports = (Port("A", THERMAL),)\n\n'
        + "    def network(self, ports, params):\n"
        + f"        {line}\n"
    )
    assert any(message in m for m in _messages(code)), _messages(code)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("import os\n", "cannot import 'os'"),
        ("from subprocess import run\n", "cannot import from 'subprocess'"),
        ("from core.physical.component import compose\n", "has no 'compose'"),
        ("print('hi')\n", "only imports, constants"),
        ("X = len('a')\n", "must be a constant"),
        ("class Plain:\n    pass\n", "needs @physical_component"),
        (
            "@physical_component\nclass P(object):\n    pass\n",
            "must derive from PhysicalComponent",
        ),
        (
            '@physical_component\nclass P(PhysicalComponent):\n    ports = (Port("A", THERMAL),)\n',
            "must implement network",
        ),
        (
            '@physical_component\nclass P(PhysicalComponent):\n    ports = (Port("A", THERMAL),)\n'
            "    def network(self, ports):\n        return []\n",
            "network(self, ports, params)",
        ),
    ],
)
def test_top_level_rules(code: str, message: str) -> None:
    assert any(message in m for m in _messages(code)), _messages(code)


def test_helpers_and_constants_may_be_used() -> None:
    code = """
    SCALE = 2.0

    def doubled(x):
        return SCALE * x

    @physical_component
    class Twice(PhysicalComponent):
        ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL))
        parameters = (Parameter("R", SCALE),)

        def network(self, ports, params):
            return [Resistor("R", ports.p, ports.n, doubled(params["R"]))]
    """
    assert _messages(code) == []
    (cls,) = load_components({"d": textwrap.dedent(code)})
    assert build_network(cls).primitives[0].value == 4.0  # type: ignore[union-attr]


def test_duplicate_component_in_one_document() -> None:
    one = _HEAD + '    ports = (Port("A", THERMAL),)\n    def network(self, ports, params):\n'
    one += "        return []\n"
    assert any("already defined on line" in m for m in _messages(one + one))


# ---- loading


def test_loading_refuses_code_with_errors() -> None:
    with pytest.raises(ComponentLoadError, match="line"):
        load_components({"bad": "import os\n"})


def test_loaded_code_has_only_the_safe_builtins() -> None:
    (cls,) = load_components({"d": TC_RESISTOR})
    namespace_builtins = cls.network.__globals__["__builtins__"]
    assert "open" not in namespace_builtins and "eval" not in namespace_builtins


def test_network_errors_are_diagnostics() -> None:
    (cls,) = load_components({"d": TC_RESISTOR})
    result = build_network(cls, {"R0": 0.0}, "rth")
    (diagnostic,) = result.diagnostics
    assert not result.ok and diagnostic.block == "rth"
    assert "network() failed" in diagnostic.message


# ---- documents (UI process: specs only)


def test_documents_register_specs_without_classes() -> None:
    registry = physical_registry()
    users = UserComponents(registry, taken=lambda name: name == "Gain")
    result = users.add_source(TC_RESISTOR, "doc1")
    assert result.ok
    assert registry.spec("TCThermalResistor") is not None
    assert registry.get("TCThermalResistor") is None  # never run in this process
    assert users.document_of("TCThermalResistor") == "doc1"
    # renaming the class removes the old component
    renamed = TC_RESISTOR.replace("class TCThermalResistor", "class Rth")
    result = users.add_source(renamed, "doc1")
    assert result.ok and result.removed == ["TCThermalResistor"]
    assert "TCThermalResistor" not in registry and "Rth" in registry
    # errors leave the document as it was
    assert not users.add_source("import os\n", "doc1").ok
    assert users.source("doc1") == renamed
    assert users.remove_document("doc1") == ["Rth"] and "Rth" not in registry


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("ThermalResistor", "a built-in component is already named"),
        ("Gain", "a block is already named"),
    ],
)
def test_name_clashes(name: str, message: str) -> None:
    users = UserComponents(physical_registry(), taken=lambda n: n == "Gain")
    code = TC_RESISTOR.replace("class TCThermalResistor", f"class {name}")
    result = users.check_source(code, "doc1")
    assert not result.ok and message in result.diagnostics[0].message


def test_another_document_may_not_take_a_name() -> None:
    users = UserComponents(PhysicalRegistry())
    assert users.add_source(TC_RESISTOR, "doc1").ok
    result = users.check_source(TC_RESISTOR, "doc2")
    assert not result.ok and "another document" in result.diagnostics[0].message


# ---- in a network (runner process)

T_AMB = 298.15
Q = 50.0  # W
R0, ALPHA = 1.0, 0.004


def _heated_diagram() -> dict:
    users = UserComponents(physical_registry())
    assert users.add_source(TC_RESISTOR, "doc1").ok
    d, _ = new_diagram(users.registry)  # the registry with the spec only
    d.add_block("ThermalReference", block_id="tref")
    d.add_block("SolverConfiguration", block_id="solver", settings={"Ts": 1e-3})
    d.add_block("TemperatureSource", block_id="amb", settings={"T": T_AMB})
    d.add_block("TCThermalResistor", block_id="rth", settings={"R0": R0, "alpha": ALPHA})
    d.add_block("HeatFlowSource", block_id="heat", settings={"Q": Q})
    d.add_block("ThermalMass", block_id="mass", settings={"C": 0.5})
    d.add_block("TemperatureSensor", block_id="ts")
    d.add_block("Scope", block_id="scope", settings={"label": "T"})
    wire(d, "solver.port", "tref.R")
    wire(d, "amb.B", "tref.R")
    wire(d, "amb.A", "rth.B")
    wire(d, "rth.A", "mass.A")
    wire(d, "heat.A", "mass.A")
    wire(d, "heat.B", "tref.R")
    wire(d, "ts.A", "mass.A")
    wire(d, "ts.B", "tref.R")
    d.add_wire("ts", "T", "scope", "u")
    return d.to_dict()


def _steady_state() -> float:
    """Solve Q = (T - Ta) / (R0 (1 + alpha ((T + Ta)/2 - Ta))) for T: with
    x = T - Ta, x = Q R0 (1 + alpha x / 2)."""
    x = Q * R0 / (1 - Q * R0 * ALPHA / 2)
    return T_AMB + x


def _run_heated() -> list[float]:
    result = run_diagram(
        _heated_diagram(), {}, t_end=0.2, dt=1e-3, components={"doc1": TC_RESISTOR}
    )
    return list(np.asarray(result.vectors["T"]).real)


@pytest.mark.ngspice
def test_user_thermal_resistor_with_temperature_coefficient_in_a_network() -> None:
    temps = in_fresh_process(_run_heated, timeout=300)
    expected = _steady_state()
    # the run starts from the operating point, where the thermal mass carries no
    # heat: the steady state from the first tick on (initial conditions are M6)
    assert temps[0] == pytest.approx(expected, abs=1e-3)
    assert temps[-1] == pytest.approx(expected, abs=1e-3)
    # a constant R0 would settle 5.6 K lower: the temperature coefficient is in effect
    assert expected - (T_AMB + Q * R0) > 5.0
    assert not math.isnan(temps[0])


def _check_mapping_error_names_the_block() -> str:
    code = TC_RESISTOR.replace('raise ValueError("R0 must be positive")', "pass")
    bad = dict(_heated_diagram())
    for block in bad["blocks"]:
        if block["id"] == "rth":
            block["settings"]["R0"] = "x"
    try:
        run_diagram(bad, {}, t_end=0.01, dt=1e-3, components={"doc1": code})
    except Exception as err:  # noqa: BLE001
        return str(err)
    return ""


@pytest.mark.ngspice
def test_a_failing_network_is_reported_on_the_block() -> None:
    message = in_fresh_process(_check_mapping_error_names_the_block, timeout=300)
    assert "rth" in message and "network() failed" in message


@pytest.mark.ngspice
def test_block_runner_child_loads_the_components() -> None:
    runner = BlockRunner(_heated_diagram(), {}, 0.5, 1e-3, components={"doc1": TC_RESISTOR})
    outcome = runner.run()
    assert outcome.ok, outcome.message
    assert "T" in outcome.result.vectors  # type: ignore[union-attr]
