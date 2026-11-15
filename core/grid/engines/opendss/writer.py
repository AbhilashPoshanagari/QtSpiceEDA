"""GridModel -> OpenDSS script text. Qt-free; does not import ``dss``.

Writing DSS text is allowed (rule 16); reading it is OpenDSS's job. The script is
self-contained: no redirects, bus coordinates as ``SetBusXY`` commands.

- Order: Clear, base frequency, ``New Circuit`` from the first Source (R1/X1/R0/X0,
  never MVAsc), other sources, linecodes, transformers, regcontrols, lines,
  switches, loads, capacitors, PV systems, voltage bases, CalcVoltageBases, opening
  of open switches, bus coordinates.
- Linecodes are written in ohm/km and nF/km with ``units=km`` after the matrices,
  lines with ``length=<m> units=m``. OpenDSS resets Units when impedances are set
  after it (spike S4), so Units always comes last.
- Ratings: a linecode's ``normamps``/``emergamps`` with its matrices; a line's own
  rating after ``linecode=`` (which resets the line's rating to the code's, S4). A
  rating that is None is not written.
- Voltage bases: the model's ``voltage_bases`` (from its .dss file), or for a design
  (``derive_voltage_bases``) topology.derived_voltage_bases(), then CalcVoltageBases.
- A Switch is ``Line ... switch=yes`` followed by its impedance (switch=yes resets
  r1/x1/c1/length), written as the total impedance over ``length=1 units=none``.
- Open switches are opened after CalcVoltageBases, so bus voltage bases are those
  of the closed network, as in a file that opens a switch after it is solved.
- Numbers use ``repr`` (shortest exact round trip, never locale-dependent); names
  are written in lower case.

``write(model)`` returns (script, diagnostics). It never raises (rule 6): an
incomplete (draft) model gives (None, one error per missing field, as
``model.missing_fields``); any other value that cannot be written becomes a Diagnostic
and the element is left out of the script.

Development CLI: ``python -m core.grid.engines.opendss.writer in.dss out.dss``.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Iterable, Sequence

from core.grid.engines.opendss.extract import LOAD_MODEL_BY_NUMBER
from core.grid.model import (
    DELTA,
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
    missing_fields,
)
from core.grid.topology import derived_voltage_bases
from core.netlist.model import ERROR, WARNING
from core.physical.component import NetworkDiagnostic

_LOAD_NUMBER = {kind: number for number, kind in LOAD_MODEL_BY_NUMBER.items()}

_SYMMETRY_RTOL = 1e-9


class _BadValue(ValueError):
    """A number that cannot be written (NaN, infinite); becomes a Diagnostic."""


def num(value: float | int | None) -> str:
    """A number as OpenDSS text: ints as ints, floats with ``repr`` (full precision,
    "." as decimal mark whatever the locale)."""
    if value is None:
        raise _BadValue("value not set")
    if isinstance(value, bool):
        raise _BadValue(f"boolean {value} given where a number is expected")
    if isinstance(value, int):
        return str(value)
    v = float(value)
    if not math.isfinite(v):
        raise _BadValue(f"{v} cannot be written")
    if v == 0.0:
        return "0"
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def _array(values: Iterable[float | int]) -> str:
    return "[" + " ".join(num(v) for v in values) + "]"


def _lower_triangle(values: Sequence[float], n: int) -> str:
    """A symmetric row-major n x n matrix as OpenDSS lower triangle "[a | b c | ...]"."""
    rows = [" ".join(num(values[i * n + j]) for j in range(i + 1)) for i in range(n)]
    return "[" + " | ".join(rows) + "]"


def _symmetric(values: Sequence[float], n: int) -> bool:
    return all(
        math.isclose(values[i * n + j], values[j * n + i], rel_tol=_SYMMETRY_RTOL, abs_tol=1e-15)
        for i in range(n)
        for j in range(i)
    )


_BAD_NAME_CHARS = frozenset(" \t\r\n\"'=[](){}|,!")


def _name(text: str) -> str:
    """A name as written: lower case; one that would break the script (spaces,
    quotes, "=", brackets, ...) is a _BadValue."""
    name = text.strip().lower()
    if not name or any(ch in _BAD_NAME_CHARS for ch in name):
        raise _BadValue(f"name {text!r} cannot be written to a DSS script")
    return name


def _term(terminal: Terminal) -> str:
    return str(Terminal(_name(terminal.bus), terminal.nodes))


def _ratings(norm_amps: float | None, emerg_amps: float | None) -> list[str]:
    """normamps=/emergamps= for the ratings that are set."""
    out = []
    if norm_amps is not None:
        out.append(f"normamps={num(norm_amps)}")
    if emerg_amps is not None:
        out.append(f"emergamps={num(emerg_amps)}")
    return out


def _enabled(enabled: bool) -> list[str]:
    return [] if enabled else ["enabled=false"]


class _Writer:
    def __init__(self, model: GridModel) -> None:
        self.model = model
        self.lines: list[str] = []
        self.diagnostics: list[NetworkDiagnostic] = []

    def _error(self, location: str, message: str) -> None:
        self.diagnostics.append(NetworkDiagnostic(location, ERROR, message))

    def _warn(self, location: str, message: str) -> None:
        self.diagnostics.append(NetworkDiagnostic(location, WARNING, message))

    def _emit(self, element_id: str, build: object) -> None:
        """Run ``build`` (returns the element's script lines); a bad number skips the
        element with an error Diagnostic instead of raising."""
        try:
            out = build()  # type: ignore[operator]
        except _BadValue as err:
            self._error(element_id, f"not written: {err}")
            return
        self.lines.extend(out)

    def _section(self, title: str) -> None:
        self.lines.extend(["", f"! {title}"])

    # ---- whole script

    def run(self) -> str:
        m = self.model
        self.lines = ["! GridModel written by QtSpice (writer.py)", "Clear"]
        try:
            self.lines.append(f"Set DefaultBaseFrequency={num(m.base_frequency)}")
        except _BadValue as err:
            self._error("circuit", f"base frequency: {err}")
        sources = list(m.sources.values())
        if not sources:
            self._error("circuit", "the model has no source: no circuit can be written")
            return "\n".join(self.lines) + "\n"
        self._section("Source")
        self._emit(sources[0].id, lambda: self._source(sources[0], circuit=_name(m.name)))
        if not any(line.startswith("New Circuit.") for line in self.lines):
            return "\n".join(self.lines) + "\n"  # the circuit itself could not be written
        for src in sources[1:]:
            self._emit(src.id, lambda src=src: self._source(src, circuit=None))
        self._group("Linecodes (ohm/km, nF/km)", m.linecodes.values(), self._linecode)
        self._group("Transformers", m.transformers.values(), self._transformer)
        self._group("Regulators", m.regulators.values(), self._regulator)
        self._group("Lines (length in m)", m.lines.values(), self._line)
        self._group("Switches", m.switches.values(), self._switch)
        self._group("Loads", m.loads.values(), self._load)
        self._group("Capacitors", m.capacitors.values(), self._capacitor)
        self._group("PV systems", m.pvsystems.values(), self._pvsystem)
        bases = derived_voltage_bases(m) if m.derive_voltage_bases else m.voltage_bases
        self._section("Voltage bases (kV LL, derived)" if m.derive_voltage_bases
                      else "Voltage bases (kV LL)")  # fmt: skip
        if bases:
            try:
                self.lines.append(f"Set VoltageBases={_array(bases)}")
            except _BadValue as err:
                self._error("circuit", f"voltage bases: {err}")
        else:
            self._warn("circuit", "the model has no voltage bases: no pu voltages")
        self.lines.append("CalcVoltageBases")
        opened = [s for s in m.switches.values() if not s.closed]
        if opened:
            self._section("Open switches")
            self.lines.extend(f"Open line.{_name(s.name)} 1" for s in opened)
        coords = [b for b in m.buses.values() if b.x is not None and b.y is not None]
        if coords:
            self._section("Bus coordinates")
            for bus in coords:
                assert bus.x is not None and bus.y is not None
                self._emit(
                    bus.id,
                    lambda bus=bus: [
                        f"SetBusXY bus={_name(bus.name)} x={num(bus.x)} y={num(bus.y)}"
                    ],
                )
        return "\n".join(self.lines) + "\n"

    def _group(self, title: str, items: Iterable[object], write: object) -> None:
        items = list(items)
        if not items:
            return
        self._section(title)
        for item in items:
            self._emit(item.id, lambda item=item: write(item))  # type: ignore[attr-defined,operator]

    # ---- element classes

    def _source(self, s: Source, circuit: str | None) -> list[str]:
        head = f"New Circuit.{circuit}" if circuit is not None else f"New Vsource.{_name(s.name)}"
        if circuit is not None and _name(s.name) != "source":
            self._warn(s.id, "the first source is written as the circuit's own, named 'source'")
        parts = [head, f"bus1={_term(s.bus1)}"]
        if any(n != 0 for n in s.bus2.nodes) or _name(s.bus2.bus) != _name(s.bus1.bus):
            parts.append(f"bus2={_term(s.bus2)}")
        parts += [
            f"phases={num(s.phases)}",
            f"basekv={num(s.kv)}",
            f"pu={num(s.pu)}",
            f"angle={num(s.angle_deg)}",
            f"basefreq={num(self.model.base_frequency)}",
            f"r1={num(s.r1_ohm)}",
            f"x1={num(s.x1_ohm)}",
            f"r0={num(s.r0_ohm)}",
            f"x0={num(s.x0_ohm)}",
            *_enabled(s.enabled),
        ]
        return [" ".join(parts)]

    def _linecode(self, c: LineCode) -> list[str]:
        n = c.phases
        matrices: list[Sequence[float]] = []
        for label, matrix in (("r", c.r_matrix), ("x", c.x_matrix), ("c", c.c_matrix)):
            if matrix is None:
                raise _BadValue(f"{label} matrix not set")
            if len(matrix) != n * n:
                raise _BadValue(f"{label} matrix has {len(matrix)} values, not {n * n}")
            if not _symmetric(matrix, n):
                raise _BadValue(f"{label} matrix is not symmetric")
            matrices.append(matrix)
        r, x, cap = matrices
        return [
            f"New Linecode.{_name(c.name)} nphases={num(n)}",
            f"~ rmatrix={_lower_triangle(r, n)}",
            f"~ xmatrix={_lower_triangle(x, n)}",
            f"~ cmatrix={_lower_triangle(cap, n)}",
            " ".join(["~", *_ratings(c.norm_amps, c.emerg_amps), "units=km"]),
        ]

    def _transformer(self, t: Transformer) -> list[str]:
        nw = len(t.windings)
        head = [f"New Transformer.{_name(t.name)}", f"phases={num(t.phases)}", f"windings={nw}"]
        head.append(f"xhl={num(t.xhl)}")
        if nw >= 3:
            head += [f"xht={num(t.xht)}", f"xlt={num(t.xlt)}"]
        head += [
            f"%noloadloss={num(t.pct_noload_loss)}",
            f"%imag={num(t.pct_imag)}",
            f"ppm_antifloat={num(t.ppm_antifloat)}",
        ]
        head += _enabled(t.enabled)
        out = [" ".join(head)]
        for i, w in enumerate(t.windings, start=1):
            out.append(
                " ".join(
                    [
                        f"~ wdg={i}",
                        f"bus={_term(w.terminal)}",
                        f"conn={'delta' if w.conn == DELTA else 'wye'}",
                        f"kv={num(w.kv)}",
                        f"kva={num(w.kva)}",
                        f"%r={num(w.pct_r)}",
                        f"tap={num(w.tap)}",
                        f"mintap={num(w.min_tap)}",
                        f"maxtap={num(w.max_tap)}",
                        f"numtaps={num(w.num_taps)}",
                        f"rneut={num(w.rneut_ohm)}",
                        f"xneut={num(w.xneut_ohm)}",
                    ]
                )
            )
        return out

    def _regulator(self, r: Regulator) -> list[str]:
        if _name(r.transformer) not in self.model.transformers:
            raise _BadValue(f"transformer {r.transformer!r} is not in the model")
        parts = [
            f"New RegControl.{_name(r.name)}",
            f"transformer={_name(r.transformer)}",
            f"winding={num(r.winding)}",
            f"vreg={num(r.vreg)}",
            f"band={num(r.band)}",
            f"ptratio={num(r.pt_ratio)}",
            f"ctprim={num(r.ct_primary)}",
            f"r={num(r.r)}",
            f"x={num(r.x)}",
            f"delay={num(r.delay_s)}",
            f"tapdelay={num(r.tap_delay_s)}",
            f"maxtapchange={num(r.max_tap_change)}",
            f"ptphase={_name(r.pt_phase)}",
        ]
        if r.monitored_bus:
            parts.append(f"bus={_name(r.monitored_bus)}")
        parts.append(f"reversible={'yes' if r.reversible else 'no'}")
        parts += _enabled(r.enabled)
        return [" ".join(parts)]

    def _line(self, ln: Line) -> list[str]:
        if _name(ln.linecode) not in self.model.linecodes:
            raise _BadValue(f"linecode {ln.linecode!r} is not in the model")
        parts = [
            f"New Line.{_name(ln.name)}",
            f"phases={num(ln.phases)}",
            f"bus1={_term(ln.bus1)}",
            f"bus2={_term(ln.bus2)}",
            f"linecode={_name(ln.linecode)}",
            *_ratings(ln.norm_amps, ln.emerg_amps),  # after linecode= (S4)
            f"length={num(ln.length_m)}",
            "units=m",
            *_enabled(ln.enabled),
        ]
        return [" ".join(parts)]

    def _switch(self, s: Switch) -> list[str]:
        # switch=yes resets r1, x1, r0, x0, c1, c0 and length: everything after it.
        parts = [
            f"New Line.{_name(s.name)}",
            f"phases={num(s.phases)}",
            f"bus1={_term(s.bus1)}",
            f"bus2={_term(s.bus2)}",
            "switch=yes",
            f"r1={num(s.r_ohm)}",
            f"x1={num(s.x_ohm)}",
            f"r0={num(s.r_ohm)}",
            f"x0={num(s.x_ohm)}",
            "c1=0",
            "c0=0",
            "length=1",
            "units=none",
            *_enabled(s.enabled),
        ]
        return [" ".join(parts)]

    def _load(self, ld: Load) -> list[str]:
        number = _LOAD_NUMBER.get(ld.model)
        if number is None:
            raise _BadValue(f"unknown load model {ld.model!r}")
        parts = [
            f"New Load.{_name(ld.name)}",
            f"phases={num(ld.phases)}",
            f"bus1={_term(ld.terminal)}",
            f"conn={'delta' if ld.conn == DELTA else 'wye'}",
            f"model={number}",
            f"kv={num(ld.kv)}",
            f"kw={num(ld.kw)}",
            f"kvar={num(ld.kvar)}",
            f"vminpu={num(ld.vminpu)}",
            f"vmaxpu={num(ld.vmaxpu)}",
            f"cvrwatts={num(ld.cvr_watts)}",
            f"cvrvars={num(ld.cvr_vars)}",
            *_enabled(ld.enabled),
        ]
        return [" ".join(parts)]

    def _capacitor(self, c: Capacitor) -> list[str]:
        steps = max(len(c.states), 1)
        kvar = num(c.kvar) if steps == 1 else _array([c.kvar / steps] * steps)
        parts = [
            f"New Capacitor.{_name(c.name)}",
            f"phases={num(c.phases)}",
            f"bus1={_term(c.terminal)}",
            f"conn={'delta' if c.conn == DELTA else 'wye'}",
            f"kv={num(c.kv)}",
            f"kvar={kvar}",
            f"states={_array(c.states or (1,))}",
            *_enabled(c.enabled),
        ]
        return [" ".join(parts)]

    def _pvsystem(self, pv: PVSystem) -> list[str]:
        parts = [
            f"New PVSystem.{_name(pv.name)}",
            f"phases={num(pv.phases)}",
            f"bus1={_term(pv.terminal)}",
            f"conn={'delta' if pv.conn == DELTA else 'wye'}",
            f"kv={num(pv.kv)}",
            f"kva={num(pv.kva)}",
            f"pmpp={num(pv.pmpp_kw)}",
            f"irradiance={num(pv.irradiance)}",
            f"pf={num(pv.pf)}",
            *_enabled(pv.enabled),
        ]
        return [" ".join(parts)]


def write(model: GridModel) -> tuple[str | None, list[NetworkDiagnostic]]:
    """The OpenDSS script of ``model`` plus what could not be written.

    An incomplete (draft) model gives no script: (None, one error Diagnostic per missing
    field, "load.l3: kW not set"). Never raises (rule 6).
    """
    try:
        missing = [NetworkDiagnostic(f.element_id, ERROR, f.message) for f in missing_fields(model)]
    except Exception as err:  # a malformed model; never raise
        return None, [NetworkDiagnostic("circuit", ERROR, f"model cannot be written: {err!r}")]
    if missing:
        return None, missing
    writer = _Writer(model)
    try:
        text = writer.run()
    except Exception as err:
        return None, [
            *writer.diagnostics,
            NetworkDiagnostic("circuit", ERROR, f"model cannot be written: {err!r}"),
        ]
    return text, writer.diagnostics


def element_of(command: str, current: str) -> str:
    """The element id a script command defines ("New Load.l1 ..." -> "load.l1"), or
    ``current`` for a "~" continuation and other commands."""
    words = command.split()
    if len(words) >= 2 and words[0].lower() == "new":
        target = words[1].lower()
        return "vsource.source" if target.startswith("circuit.") else target
    return current


def script_commands(text: str) -> list[tuple[int, str]]:
    """(line number, command) of every non-empty, non-comment script line."""
    out: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith(("!", "//")):
            out.append((number, stripped))
    return out


# ---- development CLI


def main(argv: Sequence[str] | None = None) -> int:
    from core.grid.config import PowerFlowConfig
    from core.grid.engines.opendss.engine import OpenDSSEngine

    parser = argparse.ArgumentParser(
        prog="python -m core.grid.engines.opendss.writer",
        description="Compile a .dss file in OpenDSS, read it back as a GridModel and "
        "write that model as a DSS script.",
    )
    parser.add_argument("input", help=".dss file to compile")
    parser.add_argument("output", help="script to write")
    parser.add_argument(
        "--no-solve",
        action="store_true",
        help="do not solve before reading back (taps stay as defined)",
    )
    args = parser.parse_args(argv)

    engine = OpenDSSEngine()
    if not engine.available():
        print("error: dss-python is not installed", file=sys.stderr)
        return 2
    diagnostics = list(engine.load_native(args.input))
    if engine.loaded and not args.no_solve:
        diagnostics += engine.run(PowerFlowConfig()).diagnostics
    model, more = engine.extract_model()
    diagnostics += more
    text: str | None = None
    if model is not None:
        text, more = write(model)
        diagnostics += more
    engine.close()
    for d in diagnostics:
        print(f"{d.severity}: {d.block}: {d.message}", file=sys.stderr)
    if model is None or text is None:
        return 1
    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {args.output}", file=sys.stderr)
    return 1 if any(d.severity == ERROR for d in diagnostics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
