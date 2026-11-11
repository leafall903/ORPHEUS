"""Scoped 01E9 Flashbang launch and gravity helpers selected from the parent."""

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
    from ow174.game import weapon_spread

    cfg = (state.node.field('m_5EC1EB38') or {}).get('m_22CE7F30')
    lift = expr.to_float(state.evaluate(cfg, watch=False)) if cfg is not None else 0.0
    return weapon_spread.launch_direction(forward, lift)
