from __future__ import annotations

import pickle

from core.grid.config import CONTROL_OFF, CONTROL_STATIC, PowerFlowConfig


def test_defaults() -> None:
    config = PowerFlowConfig()
    assert config.control_mode == CONTROL_STATIC
    # None = keep what the .dss file set
    assert config.max_iterations is None
    assert config.max_control_iterations is None
    assert config.tolerance is None
    assert (config.voltage_min_pu, config.voltage_max_pu) == (0.95, 1.05)
    assert config.problems() == []


def test_no_analysis_type_field() -> None:
    assert "analysis" not in " ".join(PowerFlowConfig.__dataclass_fields__)


def test_problems_are_reported_not_raised() -> None:
    bad = PowerFlowConfig(
        control_mode="time",
        max_iterations=0,
        max_control_iterations=0,
        tolerance=0.0,
        voltage_min_pu=1.1,
        voltage_max_pu=1.0,
    )
    assert len(bad.problems()) == 5
    assert PowerFlowConfig(control_mode=CONTROL_OFF).problems() == []


def test_dict_round_trip_and_pickle() -> None:
    config = PowerFlowConfig(control_mode=CONTROL_OFF, max_control_iterations=30, tolerance=1e-6)
    assert PowerFlowConfig.from_dict(config.to_dict()) == config
    assert PowerFlowConfig.from_dict({"tolerance": 1e-5, "unknown": 1}).tolerance == 1e-5
    assert pickle.loads(pickle.dumps(config)) == config


def test_feasibility_limits() -> None:
    config = PowerFlowConfig()
    assert config.line_loading_limit_pct == 100.0
    assert config.transformer_loading_limit_pct == 100.0
    assert config.warn_fraction == 0.9
    bad = PowerFlowConfig(
        line_loading_limit_pct=0.0, transformer_loading_limit_pct=-1.0, warn_fraction=1.5
    )
    assert len(bad.problems()) == 3
    assert PowerFlowConfig(warn_fraction=0.0).problems() != []
    assert PowerFlowConfig(warn_fraction=1.0).problems() == []
    custom = PowerFlowConfig(line_loading_limit_pct=80.0, warn_fraction=0.75)
    assert PowerFlowConfig.from_dict(custom.to_dict()) == custom


def test_v04_loading_limit_maps_onto_both_limits() -> None:
    old = PowerFlowConfig.from_dict({"voltage_min_pu": 0.9, "loading_limit_pct": 80.0})
    assert (old.line_loading_limit_pct, old.transformer_loading_limit_pct) == (80.0, 80.0)
    assert "loading_limit_pct" not in PowerFlowConfig.__dataclass_fields__
    both = PowerFlowConfig.from_dict(
        {"loading_limit_pct": 80.0, "transformer_loading_limit_pct": 120.0}
    )
    assert (both.line_loading_limit_pct, both.transformer_loading_limit_pct) == (80.0, 120.0)
