"""The C3 speed-control example built with DiagramController slots (U3, U4 tests).

Inertia J and damper b on a shaft, driven by a signal-controlled torque, with a P
controller of v0.1 blocks: tau = kp * (w_set - w). Steady state (P control):
w = kp / (kp + b) * w_set.
"""

from __future__ import annotations

from bridge.diagram_controller import DiagramController

J, B, KP, W_SET = 0.01, 0.001, 1.0, 100.0


def steady_speed(kp: float = KP, b: float = B, w_set: float = W_SET) -> float:
    return kp / (kp + b) * w_set


def build_speed_loop(ctl: DiagramController, reference: bool = True) -> dict[str, str]:
    """Place and wire the example; returns the block ids by role."""
    ids = {
        "solver": ctl.addBlock("SolverConfiguration", 40, 40),
        "inertia": ctl.addBlock("Inertia", 300, 180),
        "damper": ctl.addBlock("RotationalDamper", 300, 300),
        "motor": ctl.addBlock("ControlledTorqueSource", 520, 180),
        "sensor": ctl.addBlock("AngularVelocitySensor", 520, 300),
        "setpoint": ctl.addBlock("Constant", 40, 460),
        "error": ctl.addBlock("Sum", 220, 460),
        "kp": ctl.addBlock("Gain", 380, 460),
        "scope": ctl.addBlock("Scope", 560, 460),
    }
    if reference:
        ids["ref"] = ctl.addBlock("MechanicalRotationalReference", 300, 40)
    ctl.setSetting(ids["inertia"], "J", str(J))
    ctl.setSetting(ids["damper"], "b", "1m")
    ctl.setSetting(ids["setpoint"], "value", str(W_SET))
    ctl.setSetting(ids["error"], "signs", "+-")
    ctl.setSetting(ids["kp"], "k", str(KP))
    ctl.setSetting(ids["scope"], "label", "tau")
    i, d, m, s = ids["inertia"], ids["damper"], ids["motor"], ids["sensor"]
    wires = [(f"{i}.A", f"{d}.A"), (f"{m}.A", f"{i}.A"), (f"{s}.A", f"{i}.A")]
    if reference:
        r = ids["ref"]
        wires += [
            (f"{ids['solver']}.port", f"{r}.R"),
            (f"{d}.B", f"{r}.R"),
            (f"{m}.B", f"{r}.R"),
            (f"{s}.B", f"{r}.R"),
        ]
    else:
        wires += [(f"{ids['solver']}.port", f"{i}.A"), (f"{m}.B", f"{d}.B"), (f"{s}.B", f"{d}.B")]
    for a, b in wires:
        assert ctl.connectPhysical(a, b), ctl.property("hint")
    for src, dst in [
        (f"{ids['setpoint']}.y", f"{ids['error']}.a"),
        (f"{s}.w", f"{ids['error']}.b"),
        (f"{ids['error']}.y", f"{ids['kp']}.u"),
        (f"{ids['kp']}.y", f"{m}.tau"),
        (f"{ids['kp']}.y", f"{ids['scope']}.u"),
    ]:
        assert ctl.connectPorts(src, dst), ctl.property("hint")
    return ids
