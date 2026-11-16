"""Read and write QtSpice project files (.qsp). Qt-free.

A project is one JSON file (format in ``schema.json`` next to this module) plus one
``.npz`` file per saved result, in a ``<stem>_results`` folder next to the .qsp file::

    rc.qsp
    rc_results/netlist.npz
    rc_results/diagram.npz

``save_project`` writes every file through a temporary file and a rename, so a failed
save never leaves a half-written project. The ``results`` list of the .qsp is the
project's record of its own result files (O4): a save deletes only files named there
by the .qsp it replaces, never any other file in the results folder.

``load_project`` never raises on a bad file: it returns a LoadResult whose ``errors``
say what is wrong (then ``project`` is None) and whose ``warnings`` list what was
skipped, such as a missing result file.

The .qsp text is deterministic: saving the same Project twice gives the same bytes.

Format versions (O1): version 3 adds the physical side of v0.3 - physical wires in a
diagram (``physical_wires``), checked Solver Configuration settings, the code of user
physical components (``physical_sources``), the parameter sweep (``sweep``) and the
rpm/degC display-unit setting (``display_units``). Versions 1 and 2 still open and are
saved as version 3. Version 2 files written by v0.3 development builds may already
hold physical wires, user components and a sweep; they open as well.

Format version 4 (G7) adds the optional ``grid`` section of v0.4: the .dss file of the
feeder (relative to the .qsp folder when inside it, ``/`` separators, else absolute),
the PowerFlowConfig solver settings (``power_flow``) and the voltage and loading limits
(``limits``). Grid results are not stored; the feeder is solved again. Versions 1-3
still open and are saved as version 4.

Format version 5 (D9) adds the optional ``grid_designs`` list of v0.5: one entry per
Grid Network Designer design - the .qgrid file (same path rules as the .dss file),
whether it is the active design (exactly one is) and its own PowerFlowConfig solver
settings and limits. The v4 ``grid`` section is unchanged. A project without designs is
still written as version 4 (BASE_VERSION), so v0.4 projects keep their bytes until they
get a design.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.grid.config import CONTROL_MODES, LEGACY_LOADING_LIMIT, PowerFlowConfig
from core.netlist.model import ERROR
from core.physical.mapper import SOLVER_SETTINGS
from core.schematic.model import Schematic
from core.sim.backend import DEFAULT_COMPAT_MODE, valid_compat_mode
from core.sim.result import SimulationResult
from core.waveform.layout import PlotLayout, layouts_from_dict, layouts_to_dict

FORMAT = "qtspice-project"
FORMAT_VERSION = 5
"""The newest version this code reads and writes (a project with grid designs)."""
BASE_VERSION = 4
"""The version a project without grid designs is written as."""
SUFFIX = ".qsp"
DEFAULT_T_END = 1.0
DEFAULT_DT = 0.001

_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_KEYS_V1 = ("format", "version", "netlists", "diagrams", "block_sources", "analysis", "results")
_KEYS = (*_KEYS_V1, "simulation")  # version 2
_OPTIONAL_KEYS_V2 = ("schematic", "symbols", "plots", "plot_defs", "physical_sources", "sweep")
"""Version 2 fields that may be left out (``physical_sources``: M5, ``sweep``: M7, both
written as version 2 by v0.3 development builds before O1)."""
_OPTIONAL_KEYS_V3 = (*_OPTIONAL_KEYS_V2, "display_units")
"""Version 3 fields that may be left out."""
_OPTIONAL_KEYS_V4 = (*_OPTIONAL_KEYS_V3, "grid")
"""Version 4 fields that may be left out."""
_OPTIONAL_KEYS = (*_OPTIONAL_KEYS_V4, "grid_designs")
"""Version 5 fields that may be left out."""
_GRID_KEYS = ("dss_file", "power_flow", "limits")
_POWER_FLOW_KEYS = ("control_mode", "max_iterations", "max_control_iterations", "tolerance")
_LIMIT_KEYS = ("voltage_min_pu", "voltage_max_pu", LEGACY_LOADING_LIMIT)
"""The v4 file keys; the one loading limit is both PowerFlowConfig loading limits."""
_DESIGN_KEYS = ("path", "active", "power_flow", "limits")
_DESIGN_LIMIT_KEYS = (
    "voltage_min_pu",
    "voltage_max_pu",
    "line_loading_limit_pct",
    "transformer_loading_limit_pct",
    "warn_fraction",
)
"""The v5 per-design limits: every PowerFlowConfig field that is not a solver setting."""
DESIGN_SUFFIX = ".qgrid"
SOLVER_SPEC = "SolverConfiguration"
"""The block spec whose settings O1 checks (the B5 Solver Configuration)."""
_PORT_REF = re.compile(r"^[^.\s]+\.[^.\s]+$")  # "block.port"
_SWEEP_FIELDS = ("block", "parameter", "values")


class ProjectError(Exception):
    """A project cannot be saved (bad names, unwritable folder)."""


@dataclass
class NetlistEntry:
    name: str
    text: str


@dataclass
class DiagramEntry:
    """A block diagram in its ``Diagram.to_dict()`` form."""

    name: str
    diagram: dict[str, Any] = field(default_factory=lambda: {"blocks": [], "wires": []})


@dataclass
class BlockSource:
    """The code of one function-editor document."""

    document: str
    code: str


@dataclass
class AnalysisSettings:
    """Last analysis settings: the analysis panel card ("" = the netlist's own card)
    and the block-diagram end time and time step."""

    card: str = ""
    diagram_t_end: float = DEFAULT_T_END
    diagram_dt: float = DEFAULT_DT


@dataclass
class SimulationSettings:
    """ngspice settings. ``compat_mode`` is the ``ngbehavior`` value ("" = off).
    Version 1 files have none and get the defaults."""

    compat_mode: str = DEFAULT_COMPAT_MODE


@dataclass
class GridSettings:
    """The project's feeder (G7): the .dss file as stored in the .qsp (relative to its
    folder with ``/``, or absolute) and the settings and limits of the next run."""

    dss_file: str
    config: PowerFlowConfig = field(default_factory=PowerFlowConfig)

    def to_dict(self) -> dict[str, Any]:
        settings = self.config.to_dict()
        return {
            "dss_file": self.dss_file,
            "power_flow": {k: settings[k] for k in _POWER_FLOW_KEYS},
            # v4 holds one loading limit: the line limit (both are equal unless a
            # caller set them apart; .qsp v5 per-design settings hold both, D9)
            "limits": {
                "voltage_min_pu": settings["voltage_min_pu"],
                "voltage_max_pu": settings["voltage_max_pu"],
                LEGACY_LOADING_LIMIT: settings["line_loading_limit_pct"],
            },
        }


@dataclass
class GridDesignSettings:
    """One Grid Network Designer design of the project (D9): the .qgrid file as stored
    in the .qsp (relative to its folder with ``/``, or absolute), whether it is the
    active design, and the settings and limits of its runs."""

    path: str
    active: bool = False
    config: PowerFlowConfig = field(default_factory=PowerFlowConfig)

    def to_dict(self) -> dict[str, Any]:
        settings = self.config.to_dict()
        return {
            "path": self.path,
            "active": self.active,
            "power_flow": {k: settings[k] for k in _POWER_FLOW_KEYS},
            "limits": {k: settings[k] for k in _DESIGN_LIMIT_KEYS},
        }


@dataclass
class Project:
    netlists: list[NetlistEntry] = field(default_factory=list)
    diagrams: list[DiagramEntry] = field(default_factory=list)
    block_sources: list[BlockSource] = field(default_factory=list)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    simulation: SimulationSettings = field(default_factory=SimulationSettings)
    results: dict[str, SimulationResult] = field(default_factory=dict)
    schematic: Schematic | None = None  # one per project (v2); None = no schematic
    # H3: circuit symbols made for this project: .asy paths, relative to the .qsp folder
    # when inside it (``/`` separators), else absolute. Left out of the file when empty.
    symbols: list[str] = field(default_factory=list)
    # W1: one waveform plot layout per analysis type ("tran", "ac", "dc"). Left out of
    # the file when empty.
    plots: dict[str, PlotLayout] = field(default_factory=dict)
    # W9: the plot definitions text (.func/.param lines). None = the shipped default,
    # left out of the file.
    plot_defs: str | None = None
    # M5: the code of each user physical component document. Left out when empty.
    physical_sources: list[BlockSource] = field(default_factory=list)
    # M7: the diagram's parameter sweep {"block", "parameter", "values"} (values as
    # typed, e.g. "1m 2m 5m"). None = no sweep, left out of the file.
    sweep: dict[str, str] | None = None
    # O1: the waveform window shows rad/s and K as rpm and degC. False (SI) is left out.
    display_units: bool = False
    # G7: the feeder of the grid power flow. None = no feeder, left out of the file.
    grid: GridSettings | None = None
    # D9: the grid designs (.qsp v5). Empty = left out, and the file is version 4.
    grid_designs: list[GridDesignSettings] = field(default_factory=list)

    @property
    def version(self) -> int:
        """The format version this project is written as: 5 only with grid designs."""
        return FORMAT_VERSION if self.grid_designs else BASE_VERSION

    def to_dict(self) -> dict[str, Any]:
        """The JSON content of the .qsp file (results only by file name). ``schematic``
        is written only when there is one, so projects without one keep their bytes."""
        data: dict[str, Any] = {
            "format": FORMAT,
            "version": self.version,
            "netlists": [{"name": n.name, "text": n.text} for n in self.netlists],
            "diagrams": [{"name": d.name, "diagram": d.diagram} for d in self.diagrams],
            "block_sources": [{"document": s.document, "code": s.code} for s in self.block_sources],
            "analysis": {
                "card": self.analysis.card,
                "diagram_t_end": self.analysis.diagram_t_end,
                "diagram_dt": self.analysis.diagram_dt,
            },
            "results": [{"name": name, "file": f"{name}.npz"} for name in self.results],
            "simulation": {"compat_mode": self.simulation.compat_mode},
        }
        if self.schematic is not None:
            data["schematic"] = self.schematic.to_dict()
        if self.symbols:
            data["symbols"] = list(self.symbols)
        if self.plots:
            data["plots"] = layouts_to_dict(self.plots)
        if self.plot_defs is not None:
            data["plot_defs"] = self.plot_defs
        if self.physical_sources:
            data["physical_sources"] = [
                {"document": s.document, "code": s.code} for s in self.physical_sources
            ]
        if self.sweep is not None:
            data["sweep"] = {k: str(self.sweep.get(k, "")) for k in _SWEEP_FIELDS}
        if self.display_units:
            data["display_units"] = True
        if self.grid is not None:
            data["grid"] = self.grid.to_dict()
        if self.grid_designs:
            data["grid_designs"] = [d.to_dict() for d in self.grid_designs]
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


@dataclass
class LoadResult:
    project: Project | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.project is not None and not self.errors


def results_dir(path: str | Path) -> Path:
    """The folder that holds a project's result files."""
    path = Path(path)
    return path.with_name(f"{path.stem}_results")


# ---- saving


def save_project(path: str | Path, project: Project) -> None:
    """Write ``project`` to ``path`` and its results next to it.

    Result files of this project that are no longer in ``project.results`` are
    removed: only files that the .qsp being replaced lists as its results (O4). Other
    files in the results folder are left alone, and the folder is removed only when it
    is empty. Raises ProjectError (or OSError) if anything cannot be written.
    """
    path = Path(path)
    for kind, names in (
        ("netlist", [n.name for n in project.netlists]),
        ("diagram", [d.name for d in project.diagrams]),
        ("result", list(project.results)),
    ):
        for name in names:
            if not _NAME.match(name):
                raise ProjectError(f"{kind} name {name!r} may only use letters, digits, _ and -")
        if len(set(names)) != len(names):
            raise ProjectError(f"two {kind}s have the same name")
    for value in (project.analysis.diagram_t_end, project.analysis.diagram_dt):
        if not (math.isfinite(value) and value > 0):
            raise ProjectError("the diagram end time and time step must be positive numbers")
    if not valid_compat_mode(project.simulation.compat_mode):
        raise ProjectError("the compatibility mode must be lower-case letters, e.g. 'ltpsa'")
    if project.grid is not None:
        if not project.grid.dss_file.strip():
            raise ProjectError("the grid section needs a .dss file")
        problems = project.grid.config.problems()
        if problems:
            raise ProjectError(f"power-flow settings: {problems[0]}")
    problems = _design_list_problems(
        [d.path for d in project.grid_designs], [d.active for d in project.grid_designs]
    )
    for design in project.grid_designs:
        problems += [f"grid design {design.path}: {p}" for p in design.config.problems()]
    if problems:
        raise ProjectError(problems[0])
    text = project.to_json()  # before touching the disk: fails on NaN etc.

    folder = results_dir(path)
    owned = _owned_result_files(path)  # read before the .qsp is replaced
    if project.results:
        folder.mkdir(parents=True, exist_ok=True)
        for name, result in project.results.items():
            tmp = folder / f"{name}.tmp.npz"
            result.save_npz(tmp)
            os.replace(tmp, folder / f"{name}.npz")
    if folder.is_dir():
        keep = {f"{name}.npz" for name in project.results}
        for name in sorted(owned - keep):
            (folder / name).unlink(missing_ok=True)
        if not any(folder.iterdir()):
            folder.rmdir()

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _owned_result_files(path: Path) -> set[str]:
    """The result file names listed by the project file at ``path`` (before a save
    replaces it): the only files a save may delete. A missing, unreadable or foreign
    file owns nothing. Only plain ``<name>.npz`` names count, never a path."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return set()
    entries = data.get("results")
    if not isinstance(entries, list):
        return set()
    owned: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name, file = entry.get("name"), entry.get("file")
        if isinstance(name, str) and _NAME.fullmatch(name) and file == f"{name}.npz":
            owned.add(file)
    return owned


# ---- loading


def load_project(path: str | Path) -> LoadResult:
    """Read a project file. Never raises; problems come back in the LoadResult."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LoadResult(None, [f"{path.name} does not exist"])
    except (OSError, UnicodeDecodeError) as exc:
        return LoadResult(None, [f"cannot read {path.name}: {exc}"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return LoadResult(
            None, [f"{path.name} is not valid JSON (line {exc.lineno}, column {exc.colno})"]
        )
    errors = _check_header(data)
    if errors:
        return LoadResult(None, errors)
    errors = _check_body(data)
    if errors:
        return LoadResult(None, errors)

    project = Project(
        netlists=[NetlistEntry(n["name"], n["text"]) for n in data["netlists"]],
        diagrams=[DiagramEntry(d["name"], d["diagram"]) for d in data["diagrams"]],
        block_sources=[BlockSource(s["document"], s["code"]) for s in data["block_sources"]],
        analysis=AnalysisSettings(
            data["analysis"]["card"],
            float(data["analysis"]["diagram_t_end"]),
            float(data["analysis"]["diagram_dt"]),
        ),
    )
    if "simulation" in data:
        project.simulation = SimulationSettings(data["simulation"]["compat_mode"])
    project.physical_sources = [
        BlockSource(s["document"], s["code"]) for s in data.get("physical_sources", [])
    ]
    if "sweep" in data:
        project.sweep = {k: data["sweep"][k] for k in _SWEEP_FIELDS}
    project.display_units = data.get("display_units", False) is True
    if "grid" in data:
        grid = data["grid"]
        project.grid = GridSettings(grid["dss_file"], _grid_config(grid))
    project.grid_designs = [
        GridDesignSettings(d["path"], d["active"], _grid_config(d))
        for d in data.get("grid_designs", [])
    ]
    warnings: list[str] = []
    if data.get("schematic") is not None:
        schematic, found = Schematic.from_dict(data["schematic"])
        errors = [d.message for d in found if d.severity == ERROR]
        if errors:
            return LoadResult(None, errors)
        warnings += [d.message for d in found]
        project.schematic = schematic
    project.symbols = list(data.get("symbols", []))
    project.plot_defs = data.get("plot_defs")
    if "plots" in data:  # a bad layout never stops the project from opening
        project.plots, found_plots = layouts_from_dict(data["plots"])
        warnings += [d.message for d in found_plots]
    folder = results_dir(path)
    for entry in data["results"]:
        file = folder / entry["file"]
        try:
            project.results[entry["name"]] = SimulationResult.load_npz(file)
        except FileNotFoundError:
            warnings.append(f"result {entry['name']!r} was not loaded: {file.name} is missing")
        except Exception as exc:  # a damaged .npz: numpy and zipfile raise many types
            warnings.append(f"result {entry['name']!r} was not loaded: {file.name}: {exc}")
    return LoadResult(project, [], warnings)


def _check_header(data: object) -> list[str]:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return ["this is not a QtSpice project file"]
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return [f"the project format version must be a whole number from 1, not {version!r}"]
    if version > FORMAT_VERSION:
        return [
            f"this project was saved by a newer QtSpice (format version {version}); "
            f"this version reads format version {FORMAT_VERSION}"
        ]
    return []


def _check_body(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    version = data["version"]
    keys = _KEYS_V1 if version == 1 else _KEYS
    for key in keys:
        if key not in data:
            errors.append(f"missing field {key!r}")
    optional = {
        1: (),
        2: _OPTIONAL_KEYS_V2,
        3: _OPTIONAL_KEYS_V3,
        4: _OPTIONAL_KEYS_V4,
    }.get(version, _OPTIONAL_KEYS)
    allowed = (*keys, *optional)
    for key in data:
        if key not in allowed:
            errors.append(f"unknown field {key!r}")
    if errors:
        return errors

    def items(key: str, fields: dict[str, type | tuple[type, ...]]) -> None:
        value = data.get(key, [])
        if not isinstance(value, list):
            errors.append(f"{key!r} must be a list")
            return
        for i, item in enumerate(value):
            where = f"{key}[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{where} must be an object")
                continue
            for name, kind in fields.items():
                if name not in item:
                    errors.append(f"missing field {where}.{name}")
                elif not isinstance(item[name], kind):
                    errors.append(f"{where}.{name} has the wrong type")
            for name in item:
                if name not in fields:
                    errors.append(f"unknown field {where}.{name}")

    items("netlists", {"name": str, "text": str})
    items("diagrams", {"name": str, "diagram": dict})
    items("block_sources", {"document": str, "code": str})
    items("physical_sources", {"document": str, "code": str})
    items("results", {"name": str, "file": str})
    if "sweep" in data:
        sweep = data["sweep"]
        if (
            not isinstance(sweep, dict)
            or set(sweep) != set(_SWEEP_FIELDS)
            or not all(isinstance(v, str) for v in sweep.values())
        ):
            errors.append("sweep must have exactly the strings block, parameter and values")
    if "display_units" in data and not isinstance(data["display_units"], bool):
        errors.append("'display_units' must be true or false")
    if "grid" in data:
        errors += _check_grid(data["grid"])
    if "grid_designs" in data:
        errors += _check_designs(data["grid_designs"])
    if errors:
        return errors

    for key in ("netlists", "diagrams", "results"):
        names = [item["name"] for item in data[key]]
        for i, name in enumerate(names):
            if not _NAME.match(name):
                errors.append(f"{key}[{i}].name {name!r} may only use letters, digits, _ and -")
        if len(set(names)) != len(names):
            errors.append(f"two entries of {key!r} have the same name")
    for i, item in enumerate(data["results"]):
        if item["file"] != f"{item['name']}.npz":
            errors.append(f"results[{i}].file must be {item['name']}.npz")
    for key in ("block_sources", "physical_sources"):
        documents = [s["document"] for s in data.get(key, [])]
        if any(not d for d in documents) or len(set(documents)) != len(documents):
            errors.append(f"{key} need distinct, non-empty document names")
    for i, entry in enumerate(data["diagrams"]):
        errors += _check_diagram(entry["diagram"], f"diagrams[{i}].diagram", version)

    symbols = data.get("symbols", [])
    if not isinstance(symbols, list) or not all(
        isinstance(s, str) and s.strip().casefold().endswith(".asy") for s in symbols
    ):
        errors.append("'symbols' must be a list of .asy file paths")
    if "plot_defs" in data and not isinstance(data["plot_defs"], str):
        errors.append("'plot_defs' must be a string")

    simulation = data.get("simulation", {"compat_mode": DEFAULT_COMPAT_MODE})
    if not isinstance(simulation, dict) or set(simulation) != {"compat_mode"}:
        errors.append("'simulation' must be an object with exactly the field 'compat_mode'")
    elif not valid_compat_mode(simulation["compat_mode"]):
        errors.append("simulation.compat_mode must be '' or lower-case letters, e.g. 'ltpsa'")

    analysis = data["analysis"]
    if not isinstance(analysis, dict):
        errors.append("'analysis' must be an object")
        return errors
    for name in ("card", "diagram_t_end", "diagram_dt"):
        if name not in analysis:
            errors.append(f"missing field analysis.{name}")
    for name in analysis:
        if name not in ("card", "diagram_t_end", "diagram_dt"):
            errors.append(f"unknown field analysis.{name}")
    if "card" in analysis and not isinstance(analysis["card"], str):
        errors.append("analysis.card must be a string")
    for name in ("diagram_t_end", "diagram_dt"):
        value = analysis.get(name)
        if name in analysis and not (_is_number(value) and value > 0):
            errors.append(f"analysis.{name} must be a positive number")
    return errors


def _check_grid(grid: object) -> list[str]:
    """The v4 grid section: a .dss path, the four solver settings (``null`` = keep the
    file's value) and the three limits, checked with PowerFlowConfig.problems()."""
    if not isinstance(grid, dict) or set(grid) != set(_GRID_KEYS):
        return ["'grid' must have exactly the fields dss_file, power_flow and limits"]
    errors: list[str] = []
    if not isinstance(grid["dss_file"], str) or not grid["dss_file"].strip():
        errors.append("grid.dss_file must be a non-empty path")
    errors += _check_power_flow(grid["power_flow"], "grid.power_flow")
    errors += _check_limits(grid["limits"], "grid.limits", _LIMIT_KEYS)
    if errors:
        return errors
    return [f"grid: {problem}" for problem in _grid_config(grid).problems()]


def _check_power_flow(power_flow: object, where: str) -> list[str]:
    """The four solver settings (``null`` = keep the file's or the OpenDSS value)."""
    if not isinstance(power_flow, dict) or set(power_flow) != set(_POWER_FLOW_KEYS):
        return [f"{where} must have exactly the fields {', '.join(_POWER_FLOW_KEYS)}"]
    errors: list[str] = []
    if power_flow["control_mode"] not in CONTROL_MODES:
        errors.append(f"{where}.control_mode must be one of {', '.join(CONTROL_MODES)}")
    for name in ("max_iterations", "max_control_iterations"):
        value = power_flow[name]
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{where}.{name} must be a whole number or null")
    if power_flow["tolerance"] is not None and not _is_number(power_flow["tolerance"]):
        errors.append(f"{where}.tolerance must be a number or null")
    return errors


def _check_limits(limits: object, where: str, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(limits, dict) or set(limits) != set(keys):
        return [f"{where} must have exactly the fields {', '.join(keys)}"]
    if not all(_is_number(limits[k]) for k in keys):
        return [f"{where} must be numbers"]
    return []


def _check_designs(designs: object) -> list[str]:
    """The v5 grid_designs list: entry shapes, then .qgrid paths, distinct files, exactly
    one active design and each design's PowerFlowConfig.problems()."""
    if not isinstance(designs, list):
        return ["'grid_designs' must be a list"]
    errors: list[str] = []
    for i, entry in enumerate(designs):
        where = f"grid_designs[{i}]"
        if not isinstance(entry, dict) or set(entry) != set(_DESIGN_KEYS):
            errors.append(f"{where} must have exactly the fields {', '.join(_DESIGN_KEYS)}")
            continue
        if not isinstance(entry["path"], str) or not entry["path"].strip():
            errors.append(f"{where}.path must be a .qgrid file path")
        if not isinstance(entry["active"], bool):
            errors.append(f"{where}.active must be true or false")
        errors += _check_power_flow(entry["power_flow"], f"{where}.power_flow")
        errors += _check_limits(entry["limits"], f"{where}.limits", _DESIGN_LIMIT_KEYS)
    if errors:
        return errors
    errors = _design_list_problems([d["path"] for d in designs], [d["active"] for d in designs])
    for i, entry in enumerate(designs):
        errors += [f"grid_designs[{i}]: {p}" for p in _grid_config(entry).problems()]
    return errors


def _design_list_problems(paths: list[str], active: list[bool]) -> list[str]:
    """What is wrong with the design list as a whole ([] = fine; an empty list is)."""
    if not paths:
        return []
    errors = [f"grid design {p!r} is not a .qgrid file" for p in paths if not _is_qgrid(p)]
    keys = [p.strip().replace("\\", "/").casefold() for p in paths]
    if len(set(keys)) != len(keys):
        errors.append("two grid designs have the same file")
    count = sum(1 for a in active if a)
    if count != 1:
        errors.append(f"exactly one grid design must be active, not {count}")
    return errors


def _is_qgrid(path: str) -> bool:
    name = path.strip()
    return len(name) > len(DESIGN_SUFFIX) and name.casefold().endswith(DESIGN_SUFFIX)


def _grid_config(grid: dict[str, Any]) -> PowerFlowConfig:
    """The PowerFlowConfig of a checked grid section or grid_designs entry (numbers as
    float)."""
    power_flow = dict(grid["power_flow"])
    if power_flow["tolerance"] is not None:
        power_flow["tolerance"] = float(power_flow["tolerance"])
    limits = {k: float(v) for k, v in grid["limits"].items()}
    return PowerFlowConfig.from_dict(power_flow | limits)


def _check_diagram(diagram: dict[str, Any], where: str, version: int) -> list[str]:
    """Shape checks only; the block rules are checked when the diagram is rebuilt.
    From version 2 a diagram may also hold ``physical_wires`` (O1)."""
    errors: list[str] = []
    if version == 1:
        if set(diagram) != {"blocks", "wires"}:
            return [f"{where} must have exactly the fields 'blocks' and 'wires'"]
    elif not {"blocks", "wires"} <= set(diagram) <= {"blocks", "wires", "physical_wires"}:
        return [f"{where} must have the fields 'blocks' and 'wires' and may have 'physical_wires'"]
    physical = diagram.get("physical_wires", [])
    if not all(isinstance(v, list) for v in (diagram["blocks"], diagram["wires"], physical)):
        return [f"{where}.blocks, {where}.wires and {where}.physical_wires must be lists"]
    for i, block in enumerate(diagram["blocks"]):
        ok = (
            isinstance(block, dict)
            and set(block) == {"id", "spec", "x", "y", "settings"}
            and isinstance(block["id"], str)
            and isinstance(block["spec"], str)
            and _is_number(block["x"])
            and _is_number(block["y"])
            and isinstance(block["settings"], dict)
        )
        if not ok:
            errors.append(f"{where}.blocks[{i}] needs id, spec (strings), x, y and settings")
        elif block["spec"] == SOLVER_SPEC:
            errors += _check_solver(block["settings"], f"{where}.blocks[{i}].settings")
    for i, wire in enumerate(diagram["wires"]):
        ok = (
            isinstance(wire, dict)
            and set(wire) == {"src", "dst"}
            and isinstance(wire["src"], str)
            and isinstance(wire["dst"], str)
        )
        if not ok:
            errors.append(f"{where}.wires[{i}] needs src and dst strings")
    for i, wire in enumerate(physical):
        ok = (
            isinstance(wire, dict)
            and set(wire) == {"a", "b"}
            and all(isinstance(wire[k], str) and _PORT_REF.match(wire[k]) for k in "ab")
        )
        if not ok:
            errors.append(f"{where}.physical_wires[{i}] needs a and b strings 'block.port'")
    return errors


def _check_solver(settings: dict[str, Any], where: str) -> list[str]:
    """The stored Solver Configuration settings (missing ones keep the default): Ts,
    reltol and abstol positive numbers, max_step a number from 0, steady_state true or
    false."""
    errors: list[str] = []
    for name, value in settings.items():
        if name not in SOLVER_SETTINGS:
            errors.append(f"{where}: unknown Solver Configuration setting {name!r}")
        elif name == "steady_state":
            if not isinstance(value, bool):
                errors.append(f"{where}.steady_state must be true or false")
        elif not _is_number(value) or value < 0 or (value == 0 and name != "max_step"):
            low = "0 or more" if name == "max_step" else "positive"
            errors.append(f"{where}.{name} must be a {low} number")
    return errors


def _is_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
