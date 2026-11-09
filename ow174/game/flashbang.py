"""Scoped McCree Flashbang motion from the parent lane."""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000042


def flashbang_gravity(shooter, state):
    """Resolve01E9/7's unconditional E1A5B579 motion; other paths stay separate."""
    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x01E9 or state.node.pos != 7):
        return None
    motions = state.node.field('m_projectileMotions') or ()
    if (len(motions) != 1 or motions[0].get('$') != 'STU_E1A5B579'
            or motions[0].get('m_D0C4CF49') is not None):
        return None
    cfg = motions[0].get('m_599AB8D7')
    return expr.f32(expr.to_float(state.evaluate(cfg, watch=False))) if cfg is not None else None


def gravity_velocity(velocity, gravity: float, seconds: float):
    """PC104319 D71ACD-D71ADE: unconditional Y velocity change, float32."""
    return (velocity[0], expr.f32(expr.f32(velocity[1]) - expr.f32(
        expr.f32(gravity) * expr.f32(seconds))), velocity[2])


def flashbang_direction(state, forward):
    """Apply the authored0.05 upward launch bias before speed/gravity."""
    cfg = (state.node.field('m_5EC1EB38') or {}).get('m_22CE7F30')
    lift = expr.to_float(state.evaluate(cfg, watch=False)) if cfg is not None else 0.0
    return launch_direction(forward, lift)


def launch_direction(forward, lift: float) -> tuple[float, float, float]:
    """D5ECB4-D5ECEA: add launch22CE7F30 to Y, normalize, or fall back down."""
    x, y, z = (expr.f32(value) for value in forward)
    y = expr.f32(y + expr.f32(lift))
    squared = expr.f32(expr.f32(expr.f32(x*x) + expr.f32(y*y)) + expr.f32(z*z))
    if squared < 1.1754943508222875e-36:
        return -0.0, -1.0, -0.0
    inverse = 1 / math.sqrt(squared)
    return tuple(expr.f32(value * inverse) for value in (x, y, z))
