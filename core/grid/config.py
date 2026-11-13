"""PowerFlowConfig: settings of one snapshot power flow. Qt-free.

Solver fields left as ``None`` keep the value the .dss file set (or the OpenDSS
default): IEEE 123, for example, sets ``MaxControlIter=30`` in its own script.
The voltage and loading limits are never given to the engine; violations are
computed from them in core/grid/analysis.py (rule 19).

Later analyses (QSTS, faults, ...) get their own config class; there is no
analysis-type field here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

CONTROL_STATIC = "static"
CONTROL_OFF = "off"
CONTROL_MODES = (CONTROL_STATIC, CONTROL_OFF)


@dataclass(frozen=True)
class PowerFlowConfig:
    control_mode: str = CONTROL_STATIC
    """"static": regulators and capacitor controls act (OpenDSS ControlMode=STATIC);
    "off": controls are ignored, taps stay where they are."""
    max_iterations: int | None = None
    """Power-flow iterations per solve (OpenDSS MaxIterations); None = keep the file's."""
    max_control_iterations: int | None = None
    """Control iterations (OpenDSS MaxControlIter); None = keep the file's."""
    tolerance: float | None = None
    """Convergence tolerance in pu (OpenDSS Tolerance); None = keep the file's."""
    voltage_min_pu: float = 0.95
    voltage_max_pu: float = 1.05
    loading_limit_pct: float = 100.0

    def problems(self) -> list[str]:
        """What is wrong with these settings ([] = fine). Never raises."""
        out: list[str] = []
        if self.control_mode not in CONTROL_MODES:
            out.append(f"control mode must be one of {', '.join(CONTROL_MODES)}")
        if self.max_iterations is not None and self.max_iterations < 1:
            out.append("max iterations must be at least 1")
        if self.max_control_iterations is not None and self.max_control_iterations < 1:
            out.append("max control iterations must be at least 1")
        if self.tolerance is not None and not self.tolerance > 0:
            out.append("tolerance must be greater than 0")
        if not 0 < self.voltage_min_pu < self.voltage_max_pu:
            out.append("voltage limits must satisfy 0 < min < max")
        if not self.loading_limit_pct > 0:
            out.append("loading limit must be greater than 0 %")
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PowerFlowConfig:
        """Unknown keys are ignored; missing keys take the defaults."""
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in names})
