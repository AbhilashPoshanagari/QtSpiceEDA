"""SimulationResult: the one result type shared by ngspice runs and block-diagram runs."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_META_KEY = "__qtspice_meta__"


@dataclass
class SimulationResult:
    """Vectors from one analysis.

    ``axis`` names the independent vector (``"time"``, ``"frequency"``, a DC sweep
    source, ...) and is also a key in ``vectors``. An operating-point result has an
    empty ``axis`` and one-element vectors. AC vectors are complex arrays.

    ``runs`` (M7): a parameter sweep's label per run, e.g. ``["J=0.001", "J=0.002"]``;
    vector ``name@k`` then belongs to run k (1-based, ``core.blocks.sweep``). Empty
    for a single run.
    """

    axis: str
    vectors: dict[str, np.ndarray] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    analysis: str = ""
    runs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.axis and self.axis not in self.vectors:
            raise ValueError(f"axis vector {self.axis!r} is not in vectors")

    @property
    def axis_values(self) -> np.ndarray | None:
        return self.vectors.get(self.axis) if self.axis else None

    def trace_names(self) -> list[str]:
        """Names of all vectors except the axis, in insertion order."""
        return [name for name in self.vectors if name != self.axis]

    def to_csv(self, path: str | Path | None = None) -> str:
        """Write the result as CSV (axis first). Complex vectors get .re / .im columns.

        Returns the CSV text; also writes it to ``path`` when one is given.
        """
        names = ([self.axis] if self.axis else []) + self.trace_names()
        header: list[str] = []
        columns: list[np.ndarray] = []
        for name in names:
            data = np.asarray(self.vectors[name])
            unit = self.units.get(name, "")
            suffix = f" [{unit}]" if unit else ""
            if np.iscomplexobj(data):
                header += [f"{name}.re{suffix}", f"{name}.im{suffix}"]
                columns += [data.real, data.imag]
            else:
                header.append(f"{name}{suffix}")
                columns.append(data)

        rows = max((len(c) for c in columns), default=0)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(header)
        for i in range(rows):
            writer.writerow([repr(float(c[i])) if i < len(c) else "" for c in columns])
        text = buf.getvalue()
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    def save_npz(self, path: str | Path) -> None:
        meta = {
            "axis": self.axis,
            "analysis": self.analysis,
            "units": self.units,
            "order": list(self.vectors),
        }
        if self.runs:
            meta["runs"] = list(self.runs)
        arrays = {f"v{i}": np.asarray(v) for i, v in enumerate(self.vectors.values())}
        np.savez_compressed(Path(path), **arrays, **{_META_KEY: np.array(json.dumps(meta))})

    @classmethod
    def load_npz(cls, path: str | Path) -> SimulationResult:
        with np.load(Path(path), allow_pickle=False) as data:
            if _META_KEY not in data:
                raise ValueError(f"{path} is not a QtSpice result file")
            meta = json.loads(str(data[_META_KEY]))
            vectors = {name: data[f"v{i}"].copy() for i, name in enumerate(meta["order"])}
        return cls(
            axis=meta["axis"],
            vectors=vectors,
            units=dict(meta["units"]),
            analysis=meta["analysis"],
            runs=[str(r) for r in meta.get("runs", [])],
        )
