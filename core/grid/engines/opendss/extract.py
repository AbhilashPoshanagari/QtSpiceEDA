"""Build a GridModel from a compiled OpenDSS circuit, through the API only. Qt-free.

OpenDSS is the .dss parser (rule 16): nothing here reads DSS text. Values come from
the typed interfaces; the few that have none (spike 4) come from
``ActiveDSSElement.Properties("<name>").Val``, a single number or name.

- Line impedances are read from the line itself (``Lines.Rmatrix`` etc., given per
  LINE unit and already converted from its linecode) and converted with the line's
  own Units code (units.py, rule 17). A line without a linecode, or whose impedances
  or ratings differ from its linecode's, gets a generated LineCode "_<line>".
- Only linecodes used by a line are in the model; a LineCode is not a circuit element.
- Lines with Switch=yes become Switch.
- Taps are read as they are now: call this after the solve so regulator taps are the
  solved ones. An unsolved circuit gives a warning.
- Every circuit element of a class we do not model gives a warning Diagnostic
  ("unsupported element ...") naming the element; nothing is dropped silently.

Never raises on odd circuits: problems are Diagnostics (rule 6). Errors from the
engine itself are left to the caller (OpenDSSEngine turns them into Diagnostics).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

from core.grid.engines.opendss import units
from core.grid.model import (
    DELTA,
    LOAD_CVR,
    LOAD_I,
    LOAD_PQ,
    LOAD_Z,
    WYE,
    Bus,
    Capacitor,
    GridModel,
    Line,
    LineCode,
    Load,
    PVSystem,
    Regulator,
    Source,
    Switch,
    Terminal,
    Transformer,
    Winding,
)
from core.netlist.model import WARNING
from core.physical.component import NetworkDiagnostic

LOAD_MODEL_BY_NUMBER = {1: LOAD_PQ, 2: LOAD_Z, 4: LOAD_CVR, 5: LOAD_I}
"""OpenDSS load model number -> ours. Others (3, 6, 7, 8) are unsupported."""

SUPPORTED_CLASSES = ("vsource", "line", "transformer", "regcontrol", "load", "capacitor",
                     "pvsystem")  # fmt: skip
"""OpenDSS classes (of AllElementNames) the extractor reads."""

_MATRIX_RTOL = 1e-9


def _warn(location: str, message: str) -> NetworkDiagnostic:
    return NetworkDiagnostic(location, WARNING, message)


def _each(iface: Any) -> Iterator[str]:
    """Names of every element of one typed interface (Lines, Loads, ...)."""
    names: list[str] = []
    idx = iface.First
    while idx > 0:
        names.append(str(iface.Name).lower())
        idx = iface.Next
    yield from names


def _same(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return len(a) == len(b) and all(
        math.isclose(x, y, rel_tol=_MATRIX_RTOL, abs_tol=1e-12) for x, y in zip(a, b, strict=True)
    )


class _Extractor:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.ckt = ctx.ActiveCircuit
        self.diagnostics: list[NetworkDiagnostic] = []

    # ---- helpers

    def _prop(self, name: str) -> str:
        """A property of the active DSS element, as its string value."""
        return str(self.ckt.ActiveDSSElement.Properties(name).Val)

    def _prop_float(self, name: str) -> float:
        return float(self._prop(name))

    def _activate(self, element_id: str) -> Any:
        self.ckt.SetActiveElement(element_id)
        return self.ckt.ActiveCktElement

    def _terminals(self) -> list[Terminal]:
        return [Terminal.parse(str(b)) for b in self.ckt.ActiveCktElement.BusNames]

    def _check_base_freq(self, element_id: str, model_freq: float) -> None:
        freq = self._prop_float("BaseFreq")
        if not math.isclose(freq, model_freq):
            self.diagnostics.append(
                _warn(
                    element_id,
                    f"base frequency {freq:g} Hz differs from the circuit's "
                    f"{model_freq:g} Hz; impedances are kept as given",
                )
            )

    # ---- whole circuit

    def run(self) -> GridModel:
        ckt = self.ckt
        base_frequency = float(self._command("get DefaultBaseFrequency"))
        model = GridModel(
            name=str(ckt.Name).lower(),
            base_frequency=base_frequency,
            voltage_bases=[float(v) for v in ckt.Settings.VoltageBases],
        )
        # After compile without a solve OpenDSS reports Converged=True, 0 iterations.
        if int(ckt.Solution.Iterations) == 0 or not bool(ckt.Solution.Converged):
            self.diagnostics.append(
                _warn(
                    "circuit",
                    "circuit is not solved: transformer taps are as defined, not as "
                    "the regulators set them",
                )
            )
        self._buses(model)
        self._sources(model)
        self._lines(model)
        self._transformers(model)
        self._regulators(model)
        self._loads(model)
        self._capacitors(model)
        self._pvsystems(model)
        self._unsupported()
        return model

    def _command(self, text: str) -> str:
        self.ctx.Text.Command = text
        return str(self.ctx.Text.Result)

    def _unsupported(self) -> None:
        for name in self.ckt.AllElementNames:
            element = str(name).lower()
            cls = element.split(".", 1)[0]
            if cls not in SUPPORTED_CLASSES:
                self.diagnostics.append(
                    _warn(element, f"unsupported element {element}: not in the grid model")
                )

    # ---- element classes

    def _buses(self, model: GridModel) -> None:
        ckt = self.ckt
        for i in range(int(ckt.NumBuses)):
            ckt.SetActiveBusi(i)
            b = ckt.ActiveBus
            name = str(b.Name).lower()
            has_xy = bool(b.Coorddefined)
            model.buses[name] = Bus(
                name=name,
                kv_base_ln=float(b.kVBase),
                x=float(b.x) if has_xy else None,
                y=float(b.y) if has_xy else None,
            )

    def _sources(self, model: GridModel) -> None:
        vs = self.ckt.Vsources
        for name in list(_each(vs)):
            vs.Name = name
            el = self._activate(f"vsource.{name}")
            terms = self._terminals()
            element_id = f"vsource.{name}"
            self._check_base_freq(element_id, model.base_frequency)
            model.sources[name] = Source(
                name=name,
                bus1=terms[0],
                bus2=terms[1] if len(terms) > 1 else Terminal(terms[0].bus, (0,) * 3),
                phases=int(vs.Phases),
                kv=float(vs.BasekV),
                pu=float(vs.pu),
                angle_deg=float(vs.AngleDeg),
                r1_ohm=self._prop_float("R1"),
                x1_ohm=self._prop_float("X1"),
                r0_ohm=self._prop_float("R0"),
                x0_ohm=self._prop_float("X0"),
                enabled=bool(el.Enabled),
            )

    def _lines(self, model: GridModel) -> None:
        ln = self.ckt.Lines
        for name in list(_each(ln)):
            ln.Name = name
            element_id = f"line.{name}"
            el = self._activate(element_id)
            terms = self._terminals()
            phases = int(ln.Phases)
            enabled = bool(el.Enabled)
            if bool(ln.IsSwitch):
                self._switch(model, name, terms, phases, enabled)
                continue
            if any(bool(el.IsOpen(t, 0)) for t in (1, 2)):
                self.diagnostics.append(
                    _warn(element_id, "line is open but not a switch: taken as closed")
                )
            self._check_base_freq(element_id, model.base_frequency)
            code = int(ln.Units)
            if units.metres_per_unit(code) is None:
                self.diagnostics.append(
                    _warn(
                        element_id,
                        f"line has no length unit (Units {code}); length and impedance "
                        f"are read as {units.UNIT_NAMES[units.NONE_AS]}",
                    )
                )
                code = units.NONE_AS
            length_m = units.length_to_m(float(ln.Length), code)
            r = units.matrix_to_per_km(ln.Rmatrix, code)
            x = units.matrix_to_per_km(ln.Xmatrix, code)
            c = units.matrix_to_per_km(ln.Cmatrix, code)
            assert length_m is not None and r is not None and x is not None and c is not None
            own = LineCode(
                name=str(ln.LineCode).lower(),
                phases=phases,
                r_matrix=r,
                x_matrix=x,
                c_matrix=c,
                normal_amps=float(ln.NormAmps),
                emergency_amps=float(ln.EmergAmps),
            )
            code_name = self._linecode_for(model, name, own)
            model.lines[name] = Line(
                name=name,
                bus1=terms[0],
                bus2=terms[1],
                phases=phases,
                length_m=length_m,
                linecode=code_name,
                enabled=enabled,
            )

    def _linecode_for(self, model: GridModel, line: str, own: LineCode) -> str:
        """The name of a LineCode in ``model`` equal to the line's own values: its
        named code, or a generated "_<line>" when it has none or differs from it."""
        if own.name:
            known = model.linecodes.get(own.name)
            if known is None:
                model.linecodes[own.name] = own
                return own.name
            if (
                known.phases == own.phases
                and _same(known.r_matrix, own.r_matrix)
                and _same(known.x_matrix, own.x_matrix)
                and _same(known.c_matrix, own.c_matrix)
                and math.isclose(known.normal_amps, own.normal_amps)
                and math.isclose(known.emergency_amps, own.emergency_amps)
            ):
                return own.name
        generated = f"_{line}"
        model.linecodes[generated] = LineCode(
            name=generated,
            phases=own.phases,
            r_matrix=own.r_matrix,
            x_matrix=own.x_matrix,
            c_matrix=own.c_matrix,
            normal_amps=own.normal_amps,
            emergency_amps=own.emergency_amps,
            generated=True,
        )
        return generated

    def _switch(
        self, model: GridModel, name: str, terms: list[Terminal], phases: int, enabled: bool
    ) -> None:
        ln = self.ckt.Lines
        el = self.ckt.ActiveCktElement
        element_id = f"line.{name}"
        length = float(ln.Length)  # same unit as R1/X1, whatever it is
        r1, x1, r0, x0 = float(ln.R1), float(ln.X1), float(ln.R0), float(ln.X0)
        if not (math.isclose(r1, r0) and math.isclose(x1, x0)):
            self.diagnostics.append(
                _warn(
                    element_id,
                    "switch has different positive and zero sequence impedance: "
                    "the positive sequence is kept",
                )
            )
        if float(ln.C1) != 0.0 or float(ln.C0) != 0.0:
            self.diagnostics.append(
                _warn(element_id, "switch has shunt capacitance: it is not kept")
            )
        closed = not any(bool(el.IsOpen(t, 0)) for t in (1, 2))
        model.switches[name] = Switch(
            name=name,
            bus1=terms[0],
            bus2=terms[1],
            phases=phases,
            closed=closed,
            r_ohm=r1 * length,
            x_ohm=x1 * length,
            enabled=enabled,
        )

    def _transformers(self, model: GridModel) -> None:
        xf = self.ckt.Transformers
        for name in list(_each(xf)):
            xf.Name = name
            element_id = f"transformer.{name}"
            el = self._activate(element_id)
            terms = self._terminals()
            self._check_base_freq(element_id, model.base_frequency)
            windings: list[Winding] = []
            for w in range(1, int(xf.NumWindings) + 1):
                xf.Wdg = w
                windings.append(
                    Winding(
                        terminal=terms[w - 1],
                        conn=DELTA if bool(xf.IsDelta) else WYE,
                        kv=float(xf.kV),
                        kva=float(xf.kVA),
                        pct_r=float(xf.R),
                        tap=float(xf.Tap),
                        min_tap=float(xf.MinTap),
                        max_tap=float(xf.MaxTap),
                        num_taps=int(xf.NumTaps),
                        rneut_ohm=float(xf.Rneut),
                        xneut_ohm=float(xf.Xneut),
                    )
                )
            model.transformers[name] = Transformer(
                name=name,
                phases=int(el.NumPhases),
                windings=windings,
                xhl=float(xf.Xhl),
                xht=float(xf.Xht),
                xlt=float(xf.Xlt),
                pct_noload_loss=self._prop_float("%NoLoadLoss"),
                pct_imag=self._prop_float("%IMag"),
                ppm_antifloat=self._prop_float("ppm_antifloat"),
                enabled=bool(el.Enabled),
            )

    def _regulators(self, model: GridModel) -> None:
        rc = self.ckt.RegControls
        for name in list(_each(rc)):
            rc.Name = name
            el = self._activate(f"regcontrol.{name}")
            model.regulators[name] = Regulator(
                name=name,
                transformer=str(rc.Transformer).lower(),
                winding=int(rc.Winding),
                vreg=float(rc.ForwardVreg),
                band=float(rc.ForwardBand),
                pt_ratio=float(rc.PTratio),
                ct_primary=float(rc.CTPrimary),
                r=float(rc.ForwardR),
                x=float(rc.ForwardX),
                delay_s=float(rc.Delay),
                tap_delay_s=float(rc.TapDelay),
                max_tap_change=int(rc.MaxTapChange),
                pt_phase=self._prop("PTPhase").lower(),
                monitored_bus=str(rc.MonitoredBus).lower(),
                reversible=bool(rc.IsReversible),
                enabled=bool(el.Enabled),
            )

    def _loads(self, model: GridModel) -> None:
        ld = self.ckt.Loads
        for name in list(_each(ld)):
            ld.Name = name
            element_id = f"load.{name}"
            el = self._activate(element_id)
            number = int(ld.Model)
            kind = LOAD_MODEL_BY_NUMBER.get(number)
            if kind is None:
                self.diagnostics.append(
                    _warn(
                        element_id,
                        f"unsupported load model {number}: taken as constant power (pq)",
                    )
                )
                kind = LOAD_PQ
            model.loads[name] = Load(
                name=name,
                terminal=self._terminals()[0],
                phases=int(ld.Phases),
                conn=DELTA if bool(ld.IsDelta) else WYE,
                kv=float(ld.kV),
                kw=float(ld.kW),
                kvar=float(ld.kvar),
                model=kind,
                vminpu=float(ld.Vminpu),
                vmaxpu=float(ld.Vmaxpu),
                cvr_watts=float(ld.CVRwatts),
                cvr_vars=float(ld.CVRvars),
                enabled=bool(el.Enabled),
            )

    def _capacitors(self, model: GridModel) -> None:
        cap = self.ckt.Capacitors
        for name in list(_each(cap)):
            cap.Name = name
            element_id = f"capacitor.{name}"
            el = self._activate(element_id)
            terms = self._terminals()
            if len(terms) > 1 and any(n != 0 for n in terms[1].nodes):
                self.diagnostics.append(
                    _warn(
                        element_id,
                        f"capacitor bus2 {terms[1]} is not ground: taken as grounded",
                    )
                )
            model.capacitors[name] = Capacitor(
                name=name,
                terminal=terms[0],
                phases=int(el.NumPhases),
                conn=DELTA if bool(cap.IsDelta) else WYE,
                kv=float(cap.kV),
                kvar=float(cap.kvar),
                states=tuple(int(s) for s in cap.States),
                enabled=bool(el.Enabled),
            )

    def _pvsystems(self, model: GridModel) -> None:
        pv = self.ckt.PVSystems
        for name in list(_each(pv)):
            pv.Name = name
            el = self._activate(f"pvsystem.{name}")
            conn = self._prop("Conn").strip().lower()
            model.pvsystems[name] = PVSystem(
                name=name,
                terminal=self._terminals()[0],
                phases=int(el.NumPhases),
                conn=DELTA if conn.startswith(("d", "ll")) else WYE,
                kv=self._prop_float("kV"),
                kva=float(pv.kVArated),
                pmpp_kw=float(pv.Pmpp),
                irradiance=float(pv.Irradiance),
                pf=float(pv.PF),
                enabled=bool(el.Enabled),
            )


def extract(ctx: Any) -> tuple[GridModel, list[NetworkDiagnostic]]:
    """Read the active circuit of ``ctx`` back as a GridModel."""
    ex = _Extractor(ctx)
    model = ex.run()
    return model, ex.diagnostics
