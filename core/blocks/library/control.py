"""M4: discrete PI and PID controllers (parallel form, forward Euler).

Input ``e`` is the control error (setpoint - measurement; use a Sum block). The
controller samples ``e`` every ``ts`` seconds (``ts`` = 0: every diagram step; else a
whole multiple of the step) and holds its output between samples.

At a sample with error e (Ts = the sample time):

- P = kp * e;  D (PID only) = N * (kd * e - x), x += Ts * D (a derivative filtered
  with time constant 1/N; x starts at kd * e on the first sample, so there is no
  derivative kick at the start);
- v = P + I + D;  u = v limited to [u_min, u_max] (None: no limit);
- the integrator I (starting at ``initial``) is updated by ``anti_windup``:

  - ``"clamping"`` (default): I += ki * Ts * e, except while u is limited and e
    would drive v further past the limit (conditional integration);
  - ``"back-calculation"``: I += Ts * (ki * e + kb * (u - v));
  - ``"none"``: I += ki * Ts * e always (the integrator winds up).

Like every stateful block, ``output()`` is the value computed at the previous step,
so the controller adds one diagram step of delay (the engine's algebraic-loop rule
is unchanged). Its first output, before any sample, is ``initial`` limited.
"""

from __future__ import annotations

import math

from core.blocks.decorator import block

CLAMPING = "clamping"
BACK_CALCULATION = "back-calculation"
NO_ANTI_WINDUP = "none"
ANTI_WINDUP = (CLAMPING, BACK_CALCULATION, NO_ANTI_WINDUP)

_RATIO_TOL = 1e-6


def _number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, not {value!r}")
    return float(value)


def _limit(name: str, value: object) -> float | None:
    return None if value is None else _number(name, value)


class _Discrete:
    """What the PI and the PID share: sampling, limits and anti-windup."""

    def __init__(
        self,
        kp: object,
        ki: object,
        ts: object,
        u_min: object,
        u_max: object,
        anti_windup: object,
        kb: object,
        initial: object,
    ) -> None:
        self.kp = _number("kp", kp)
        self.ki = _number("ki", ki)
        self.ts = _number("ts", ts)
        if self.ts < 0:
            raise ValueError(f"ts must not be negative, not {ts!r}")
        self.u_min = _limit("u_min", u_min)
        self.u_max = _limit("u_max", u_max)
        if self.u_min is not None and self.u_max is not None and self.u_min > self.u_max:
            raise ValueError(f"u_min ({u_min!r}) is above u_max ({u_max!r})")
        mode = anti_windup.strip().lower() if isinstance(anti_windup, str) else anti_windup
        if mode not in ANTI_WINDUP:
            raise ValueError(f"anti_windup must be one of {', '.join(ANTI_WINDUP)}, not {mode!r}")
        self.mode = mode
        self.kb = _number("kb", kb)
        self.i = _number("initial", initial)
        self.u = self._clamp(self.i)
        self.every = 0  # diagram steps per sample, known at the first step
        self.count = 0

    def _clamp(self, v: float) -> float:
        if self.u_max is not None and v > self.u_max:
            return self.u_max
        if self.u_min is not None and v < self.u_min:
            return self.u_min
        return v

    def output(self) -> float:
        return self.u

    def _sample_time(self, dt: float) -> float | None:
        """The sample time if this step is a sample, else None."""
        if not self.every:
            if self.ts == 0:
                self.every = 1
            else:
                ratio = self.ts / dt
                self.every = round(ratio)
                if self.every < 1 or abs(ratio - self.every) > _RATIO_TOL * ratio:
                    raise ValueError(
                        f"ts = {self.ts:g} s must be a whole multiple of the time step {dt:g} s"
                    )
        sample = self.count % self.every == 0
        self.count += 1
        return self.every * dt if sample else None

    def _advance(self, e: float, rest: float, ts: float) -> None:
        """Set the output from v = kp*e + I + ``rest`` and update the integrator."""
        v = self.kp * e + self.i + rest
        u = self._clamp(v)
        if self.mode == BACK_CALCULATION:
            self.i += ts * (self.ki * e + self.kb * (u - v))
        elif self.mode == CLAMPING and u != v and (v - u) * (self.ki * e) > 0:
            pass  # limited, and integrating would push v further past the limit
        else:
            self.i += self.ki * ts * e
        self.u = u


@block(stateful=True)
class DiscretePI(_Discrete):
    """Discrete PI controller on error e: u = kp*e + ki*integral(e), sampled every ts
    (0 = every step), limited to [u_min, u_max] (None = no limit), with anti-windup
    "clamping", "back-calculation" (gain kb) or "none"."""

    def __init__(
        self,
        kp=1.0,  # noqa: ANN001
        ki=1.0,  # noqa: ANN001
        ts=0.0,  # noqa: ANN001
        u_min=None,  # noqa: ANN001
        u_max=None,  # noqa: ANN001
        anti_windup="clamping",  # noqa: ANN001
        kb=1.0,  # noqa: ANN001
        initial=0.0,  # noqa: ANN001
    ) -> None:
        super().__init__(kp, ki, ts, u_min, u_max, anti_windup, kb, initial)

    def output(self):  # noqa: ANN201
        return self.u

    def step(self, e, dt):  # noqa: ANN001, ANN201
        ts = self._sample_time(dt)
        if ts is not None:
            self._advance(_number("e", e), 0.0, ts)


@block(stateful=True)
class DiscretePID(_Discrete):
    """Discrete PID controller on error e: PI plus a derivative kd*de/dt filtered with
    time constant 1/n; sampled every ts (0 = every step), limited to [u_min, u_max]
    (None = no limit), with anti-windup "clamping", "back-calculation" or "none"."""

    def __init__(
        self,
        kp=1.0,  # noqa: ANN001
        ki=1.0,  # noqa: ANN001
        kd=0.0,  # noqa: ANN001
        n=100.0,  # noqa: ANN001
        ts=0.0,  # noqa: ANN001
        u_min=None,  # noqa: ANN001
        u_max=None,  # noqa: ANN001
        anti_windup="clamping",  # noqa: ANN001
        kb=1.0,  # noqa: ANN001
        initial=0.0,  # noqa: ANN001
    ) -> None:
        super().__init__(kp, ki, ts, u_min, u_max, anti_windup, kb, initial)
        self.kd = _number("kd", kd)
        self.n = _number("n", n)
        if not self.n > 0:
            raise ValueError(f"n must be positive, not {n!r}")
        self.x: float | None = None  # derivative filter state

    def output(self):  # noqa: ANN201
        return self.u

    def step(self, e, dt):  # noqa: ANN001, ANN201
        ts = self._sample_time(dt)
        if ts is None:
            return
        if self.n * ts >= 2.0:
            raise ValueError(
                f"n * ts = {self.n * ts:g} must be below 2 for a stable derivative filter"
            )
        err = _number("e", e)
        if self.x is None:
            self.x = self.kd * err
        d = self.n * (self.kd * err - self.x)
        self.x += ts * d
        self._advance(err, d, ts)
