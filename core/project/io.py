"""Read and write QtSpice project files (.qsp). Qt-free.

A project is one JSON file (format in ``schema.json`` next to this module) plus one
``.npz`` file per saved result, in a ``<stem>_results`` folder next to the .qsp file::

    rc.qsp
    rc_results/netlist.npz
    rc_results/diagram.npz

``save_project`` writes every file through a temporary file and a rename, so a failed
save never leaves a half-written project. ``load_project`` never raises on a bad file:
it returns a LoadResult whose ``errors`` say what is wrong (then ``project`` is None)
and whose ``warnings`` list what was skipped, such as a missing result file.

The .qsp text is deterministic: saving the same Project twice gives the same bytes.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.sim.backend import DEFAULT_COMPAT_MODE, valid_compat_mode
from core.sim.result import SimulationResult

FORMAT = "qtspice-project"
FORMAT_VERSION = 2
SUFFIX = ".qsp"
DEFAULT_T_END = 1.0
DEFAULT_DT = 0.001

_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_KEYS_V1 = ("format", "version", "netlists", "diagrams", "block_sources", "analysis", "results")
_KEYS = (*_KEYS_V1, "simulation")  # version 2


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
class Project:
    netlists: list[NetlistEntry] = field(default_factory=list)
    diagrams: list[DiagramEntry] = field(default_factory=list)
    block_sources: list[BlockSource] = field(default_factory=list)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    simulation: SimulationSettings = field(default_factory=SimulationSettings)
    results: dict[str, SimulationResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The JSON content of the .qsp file (results only by file name)."""
        return {
            "format": FORMAT,
            "version": FORMAT_VERSION,
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
    removed. Raises ProjectError (or OSError) if anything cannot be written.
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
    text = project.to_json()  # before touching the disk: fails on NaN etc.

    folder = results_dir(path)
    if project.results:
        folder.mkdir(parents=True, exist_ok=True)
        for name, result in project.results.items():
            tmp = folder / f"{name}.tmp.npz"
            result.save_npz(tmp)
            os.replace(tmp, folder / f"{name}.npz")
    if folder.is_dir():
        keep = {f"{name}.npz" for name in project.results}
        for old in folder.glob("*.npz"):
            if old.name not in keep:
                old.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


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
    warnings: list[str] = []
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
    keys = _KEYS_V1 if data["version"] == 1 else _KEYS
    for key in keys:
        if key not in data:
            errors.append(f"missing field {key!r}")
    for key in data:
        if key not in keys:
            errors.append(f"unknown field {key!r}")
    if errors:
        return errors

    def items(key: str, fields: dict[str, type | tuple[type, ...]]) -> None:
        value = data[key]
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
    items("results", {"name": str, "file": str})
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
    documents = [s["document"] for s in data["block_sources"]]
    if any(not d for d in documents) or len(set(documents)) != len(documents):
        errors.append("block_sources need distinct, non-empty document names")
    for i, entry in enumerate(data["diagrams"]):
        errors += _check_diagram(entry["diagram"], f"diagrams[{i}].diagram")

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


def _check_diagram(diagram: dict[str, Any], where: str) -> list[str]:
    """Shape checks only; the block rules are checked when the diagram is rebuilt."""
    errors: list[str] = []
    if set(diagram) != {"blocks", "wires"}:
        return [f"{where} must have exactly the fields 'blocks' and 'wires'"]
    if not isinstance(diagram["blocks"], list) or not isinstance(diagram["wires"], list):
        return [f"{where}.blocks and {where}.wires must be lists"]
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
    for i, wire in enumerate(diagram["wires"]):
        ok = (
            isinstance(wire, dict)
            and set(wire) == {"src", "dst"}
            and isinstance(wire["src"], str)
            and isinstance(wire["dst"], str)
        )
        if not ok:
            errors.append(f"{where}.wires[{i}] needs src and dst strings")
    return errors


def _is_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
