"""Small helpers shared by the built-in library modules. Qt-free.

Only formatting and parameter reading: element values still come from the Domain
registry inside each component (rule 12).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from core.physical.component import Parameter, Pulse, Sine


def num(value: object) -> str:
    """A number as it goes into a behavioural expression."""
    return repr(float(value))  # type: ignore[arg-type]


def real(params: Mapping[str, object], name: str) -> float:
    """Parameter ``name`` as a float. Raises ValueError for a non-number (the caller,
    ``build_network``, reports it as a diagnostic)."""
    value = params[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number, not {value!r}")
    return float(value)


def positive(params: Mapping[str, object], name: str) -> float:
    value = real(params, name)
    if not value > 0.0:
        raise ValueError(f"{name} must be positive, not {value!r}")
    return value


def sine_parameters(unit: str) -> tuple[Parameter, ...]:
    return (
        Parameter("offset", 0.0, unit, "offset"),
        Parameter("amplitude", 1.0, unit, "amplitude"),
        Parameter("freq", 50.0, "Hz", "frequency"),
        Parameter("delay", 0.0, "s", "delay"),
        Parameter("theta", 0.0, "1/s", "damping factor"),
        Parameter("phase", 0.0, "deg", "phase"),
    )


def pulse_parameters(unit: str) -> tuple[Parameter, ...]:
    return (
        Parameter("v1", 0.0, unit, "initial value"),
        Parameter("v2", 1.0, unit, "pulsed value"),
        Parameter("delay", 0.0, "s", "delay"),
        Parameter("rise", 1e-6, "s", "rise time"),
        Parameter("fall", 1e-6, "s", "fall time"),
        Parameter("width", 0.5e-3, "s", "pulse width"),
        Parameter("period", 1e-3, "s", "period (0 = one pulse)"),
        Parameter("ncycles", 0, "", "number of cycles (0 = no limit)"),
    )


def sine(params: Mapping[str, object], to: Callable[[float], float]) -> Sine:
    """A Sine from ``sine_parameters``; ``to`` converts levels (Domain.to_voltage ...)."""
    return Sine(
        offset=to(real(params, "offset")),
        amplitude=to(real(params, "amplitude")),
        freq=real(params, "freq"),
        delay=real(params, "delay"),
        theta=real(params, "theta"),
        phase=real(params, "phase"),
    )


def pulse(params: Mapping[str, object], to: Callable[[float], float]) -> Pulse:
    """A Pulse from ``pulse_parameters``; ``to`` converts levels."""
    return Pulse(
        v1=to(real(params, "v1")),
        v2=to(real(params, "v2")),
        delay=real(params, "delay"),
        rise=real(params, "rise"),
        fall=real(params, "fall"),
        width=real(params, "width"),
        period=real(params, "period"),
        ncycles=int(real(params, "ncycles")),
    )
