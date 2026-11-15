"""GridModel: an engine-independent distribution grid. Qt-free, engine-free, picklable.

Canonical units (CLAUDE.md "Grid model"; conversion happens only in
core/grid/engines/opendss/units.py, rule 17):

- length in m;
- series impedance in ohm/km and shunt capacitance in nF/km, as full phase matrices
  stored row-major (``phases * phases`` values);
- voltage in kV: line-to-line for 3-phase and delta elements, as given for 1-phase
  wye elements; bus bases are line-to-neutral;
- power in kW, kvar, kVA; transformer R and X in % on the winding / element base.

Draft elements (v0.5 designs, CLAUDE.md "Grid model changes for designs"): the fields a
user must type are ``X | None``; None means "not set yet". ``missing_fields`` lists
them; validate.py reports each as an error and writer.py refuses the model. A model
extracted from a .dss file is always complete.

Names are lower case. Element ids are "<class>.<name>" with the OpenDSS class, so they
match GridResult and the Diagnostics: a Switch is "line.<name>", a Regulator
"regcontrol.<name>", a Source "vsource.<name>".
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

WYE = "wye"
DELTA = "delta"
CONNECTIONS = (WYE, DELTA)

LOAD_PQ = "pq"
LOAD_Z = "z"
LOAD_I = "i"
LOAD_CVR = "cvr"
LOAD_MODELS = (LOAD_PQ, LOAD_Z, LOAD_I, LOAD_CVR)


@dataclass(frozen=True)
class Terminal:
    """A bus plus explicit node numbers: "671.1.2.3" -> Terminal("671", (1, 2, 3)).

    ``nodes`` is empty when the bus was given without nodes ("675"); 0 is ground.
    """

    bus: str
    nodes: tuple[int, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Terminal:
        """Parse "<bus>[.<node>...]". Node parts that are not integers stay part of
        the bus name, so this never raises on odd names."""
        parts = text.strip().lower().split(".")
        nodes: list[int] = []
        while len(parts) > 1 and parts[-1].isdigit():
            nodes.insert(0, int(parts.pop()))
        return cls(".".join(parts), tuple(nodes))

    def __str__(self) -> str:
        return ".".join([self.bus, *(str(n) for n in self.nodes)])


@dataclass
class Bus:
    name: str
    kv_base_ln: float
    """Line-to-neutral voltage base in kV; 0 when the bus has none."""
    x: float | None = None
    y: float | None = None
    """Coordinates for the single-line view; None when the file gives none."""

    cls: ClassVar[str] = "bus"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class Source:
    """An OpenDSS Vsource: an ideal voltage behind R1/X1 and R0/X0."""

    name: str
    bus1: Terminal
    bus2: Terminal
    phases: int
    kv: float
    """Base voltage in kV LL."""
    pu: float
    angle_deg: float
    r1_ohm: float
    x1_ohm: float
    r0_ohm: float
    x0_ohm: float
    enabled: bool = True

    cls: ClassVar[str] = "vsource"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class LineCode:
    name: str
    phases: int
    r_matrix: tuple[float, ...] | None
    """Series resistance in ohm/km, row-major phases x phases; None in a draft."""
    x_matrix: tuple[float, ...] | None
    """Series reactance in ohm/km, row-major; None in a draft."""
    c_matrix: tuple[float, ...] | None
    """Shunt capacitance in nF/km, row-major; None in a draft."""
    norm_amps: float | None = None
    """Normal rating in A; None = no rating (never a guessed value)."""
    emerg_amps: float | None = None
    """Emergency rating in A; None = no rating."""
    generated: bool = False
    """True for a code made from one line's own impedances ("_<line>")."""

    cls: ClassVar[str] = "linecode"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class Line:
    name: str
    bus1: Terminal
    bus2: Terminal
    phases: int
    length_m: float
    linecode: str
    """Name of a LineCode in the same GridModel."""
    enabled: bool = True
    norm_amps: float | None = None
    """Normal rating in A of this line; None = its linecode's rating."""
    emerg_amps: float | None = None
    """Emergency rating in A of this line; None = its linecode's rating."""

    cls: ClassVar[str] = "line"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class Switch:
    """A switch (OpenDSS Line with Switch=yes). Its impedance is kept: IEEE 13 uses
    r1 = 1e-4. Positive and zero sequence are equal, without shunt capacitance."""

    name: str
    bus1: Terminal
    bus2: Terminal
    phases: int
    closed: bool
    r_ohm: float
    """Total series resistance per phase in ohm."""
    x_ohm: float = 0.0
    """Total series reactance per phase in ohm."""
    enabled: bool = True

    cls: ClassVar[str] = "line"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class Winding:
    terminal: Terminal
    conn: str
    kv: float | None
    """Rated voltage in kV (LL for 3-phase and delta, as given for 1-phase wye);
    None in a draft."""
    kva: float | None
    pct_r: float | None
    tap: float = 1.0
    """Tap in pu; for a regulated winding, the tap after the solve."""
    min_tap: float = 0.9
    max_tap: float = 1.1
    num_taps: int = 32
    rneut_ohm: float = -1.0
    """Neutral resistance in ohm; negative = solidly grounded (OpenDSS convention)."""
    xneut_ohm: float = 0.0


@dataclass
class Transformer:
    name: str
    phases: int
    windings: list[Winding]
    xhl: float | None
    """Reactance winding 1-2 in %."""
    xht: float | None
    """Reactance winding 1-3 in % (3-winding transformers; not needed for 2)."""
    xlt: float | None
    """Reactance winding 2-3 in % (3-winding transformers; not needed for 2)."""
    pct_noload_loss: float = 0.0
    pct_imag: float = 0.0
    ppm_antifloat: float = 1.0
    """Shunt capacitance to ground on each winding, in ppm of the winding's base
    admittance (OpenDSS ppm_antifloat). IEEE 123 sets 0 on its regulators."""
    enabled: bool = True

    cls: ClassVar[str] = "transformer"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class Regulator:
    """A tap control (OpenDSS RegControl) on one winding of a Transformer; never a
    branch of its own. The tap it reached is the winding's ``tap``."""

    name: str
    transformer: str
    winding: int
    vreg: float
    """Regulated voltage in V on the PT secondary."""
    band: float
    """Bandwidth in V on the PT secondary."""
    pt_ratio: float
    ct_primary: float
    """CT primary rating in A."""
    r: float
    """Line drop compensator R in V."""
    x: float
    """Line drop compensator X in V."""
    delay_s: float = 15.0
    tap_delay_s: float = 2.0
    max_tap_change: int = 16
    pt_phase: str = "1"
    """Phase the PT is on: "1".."n", "max" or "min"."""
    monitored_bus: str = ""
    reversible: bool = False
    enabled: bool = True

    cls: ClassVar[str] = "regcontrol"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class Load:
    name: str
    terminal: Terminal
    phases: int
    conn: str
    kv: float
    kw: float | None
    kvar: float | None
    model: str
    """One of LOAD_MODELS: constant power, impedance, current, or CVR."""
    vminpu: float = 0.95
    vmaxpu: float = 1.05
    cvr_watts: float = 1.0
    """CVR model only: % change of P per % change of voltage."""
    cvr_vars: float = 2.0
    enabled: bool = True

    cls: ClassVar[str] = "load"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class Capacitor:
    name: str
    terminal: Terminal
    phases: int
    conn: str
    kv: float
    kvar: float | None
    """Total rated kvar of the bank (all steps)."""
    states: tuple[int, ...] = (1,)
    """Step states, 1 = in service, one per step."""
    enabled: bool = True

    cls: ClassVar[str] = "capacitor"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class PVSystem:
    name: str
    terminal: Terminal
    phases: int
    conn: str
    kv: float
    kva: float | None
    """Inverter rating in kVA."""
    pmpp_kw: float | None
    """Panel power at 1 kW/m2 in kW."""
    irradiance: float
    """Irradiance in kW/m2 (pu of 1 kW/m2)."""
    pf: float
    enabled: bool = True

    cls: ClassVar[str] = "pvsystem"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class GridModel:
    name: str
    base_frequency: float
    """In Hz, always from the file; there is no default."""
    voltage_bases: list[float] = field(default_factory=list)
    """kV LL, as in the file's Set VoltageBases (unused when derive_voltage_bases)."""
    derive_voltage_bases: bool = False
    """False: the bases are ``voltage_bases``, from the .dss file (also for a design
    opened from a .dss file). True: a design made in the designer; the writer derives
    the bases from topology.derived_voltage_bases() and ``voltage_bases`` is ignored."""
    buses: dict[str, Bus] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
    linecodes: dict[str, LineCode] = field(default_factory=dict)
    lines: dict[str, Line] = field(default_factory=dict)
    switches: dict[str, Switch] = field(default_factory=dict)
    transformers: dict[str, Transformer] = field(default_factory=dict)
    regulators: dict[str, Regulator] = field(default_factory=dict)
    loads: dict[str, Load] = field(default_factory=dict)
    capacitors: dict[str, Capacitor] = field(default_factory=dict)
    pvsystems: dict[str, PVSystem] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        """Number of elements per class dict, e.g. {"lines": 11, ...}."""
        return {
            "buses": len(self.buses),
            "sources": len(self.sources),
            "linecodes": len(self.linecodes),
            "lines": len(self.lines),
            "switches": len(self.switches),
            "transformers": len(self.transformers),
            "regulators": len(self.regulators),
            "loads": len(self.loads),
            "capacitors": len(self.capacitors),
            "pvsystems": len(self.pvsystems),
        }


@dataclass(frozen=True)
class MissingField:
    """A user-entered field of a draft element that is not set (None)."""

    element_id: str
    label: str
    """What is missing as the user knows it ("kW", "winding 2 kVA", "R matrix")."""

    @property
    def message(self) -> str:
        return f"{self.label} not set"


def missing_fields(model: GridModel) -> Iterator[MissingField]:
    """Every user-entered field that is None, in model order (CLAUDE.md "Grid model
    changes for designs": linecode impedances, load kW/kvar, transformer kVA/kV/%R/X,
    capacitor kvar, PV Pmpp/kVA)."""
    for c in model.linecodes.values():
        for label, value in (("R matrix", c.r_matrix), ("X matrix", c.x_matrix),
                             ("C matrix", c.c_matrix)):  # fmt: skip
            if value is None:
                yield MissingField(c.id, label)
    for t in model.transformers.values():
        for i, w in enumerate(t.windings, start=1):
            for label, value in (("kV", w.kv), ("kVA", w.kva), ("%R", w.pct_r)):
                if value is None:
                    yield MissingField(t.id, f"winding {i} {label}")
        reactances = [("Xhl", t.xhl)]
        if len(t.windings) >= 3:
            reactances += [("Xht", t.xht), ("Xlt", t.xlt)]
        for label, value in reactances:
            if value is None:
                yield MissingField(t.id, f"{label} (%)")
    for ld in model.loads.values():
        for label, value in (("kW", ld.kw), ("kvar", ld.kvar)):
            if value is None:
                yield MissingField(ld.id, label)
    for cap in model.capacitors.values():
        if cap.kvar is None:
            yield MissingField(cap.id, "kvar")
    for pv in model.pvsystems.values():
        for label, value in (("Pmpp", pv.pmpp_kw), ("kVA", pv.kva)):
            if value is None:
                yield MissingField(pv.id, label)
