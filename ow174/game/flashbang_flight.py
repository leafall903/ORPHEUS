"""Build104319 Flashbang launch and gravity from 01E9 node7."""

import math

from ow174.game.script import expr

MCCREE = 0x02E0000000000042


def launch(shooter, state, forward):
    script = shooter.body_script
    if (shooter.hero.guid != MCCREE or script is None or state.instance.component is not script.component
            or state.instance.graph.index != 0x01E9 or state.node.pos != 7):
        return forward, None
    motions = state.node.field("m_projectileMotions") or ()
    if (len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579"
            or motions[0].get("m_D0C4CF49") is not None):
        return forward, None
    cfg = motions[0].get("m_599AB8D7")
    if cfg is None:
        return forward, None
    gravity = expr.f32(expr.to_float(state.evaluate(cfg, watch=False)))
    lift_cfg = (state.node.field("m_5EC1EB38") or {}).get("m_22CE7F30")
    lift = expr.to_float(state.evaluate(lift_cfg, watch=False)) if lift_cfg is not None else 0.0
    # D5ECB4-D5ECEA normalizes after adding the authored vertical bias.
    f32 = expr.f32
    x, y, z = (f32(value) for value in forward)
    y = f32(y + f32(lift))
    squared = f32(f32(f32(x * x) + f32(y * y)) + f32(z * z))
    if squared < 1.1754943508222875e-36:
        return (-0.0, -1.0, -0.0), gravity
    inverse = 1 / math.sqrt(squared)
    return tuple(f32(value * inverse) for value in (x, y, z)), gravity


def gravity_velocity(velocity, gravity, duration):
    """D71ACD-D71ADE updates the vertical velocity at float32 precision."""
    f32 = expr.f32
    x, y, z = velocity
    return x, f32(f32(y) - f32(f32(gravity) * f32(duration))), z
