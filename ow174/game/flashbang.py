"""Authored Flashbang launch and unconditional gravity arithmetic."""

import math

from ow174.game.script import expr


def launch(shooter, state, forward):
    if (shooter.hero.guid != 0x02E0000000000042 or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x01E9 or state.node.pos != 7):
        return None
    motions = state.node.field("m_projectileMotions") or ()
    if (len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579"
            or motions[0].get("m_D0C4CF49") is not None):
        return None
    gravity = motions[0].get("m_599AB8D7")
    if gravity is None:
        return None
    gravity = expr.f32(expr.to_float(state.evaluate(gravity, watch=False)))
    cfg = (state.node.field("m_5EC1EB38") or {}).get("m_22CE7F30")
    lift = expr.to_float(state.evaluate(cfg, watch=False)) if cfg is not None else 0.0
    return gravity, biased(forward, lift)


def biased(forward, lift):
    x, y, z = (expr.f32(value) for value in forward)
    y = expr.f32(y + expr.f32(lift))
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    if squared < 1.1754943508222875e-36:
        return (-0.0, -1.0, -0.0)
    inverse = 1 / math.sqrt(squared)
    return tuple(expr.f32(value * inverse) for value in (x, y, z))


def accelerate(velocity, gravity, seconds):
    """Apply the D71ACD unconditional vertical velocity update."""
    delta = expr.f32(expr.f32(gravity) * expr.f32(seconds))
    return velocity[0], expr.f32(expr.f32(velocity[1]) - delta), velocity[2]
