"""Selected McCree Fan and Flashbang bridge for Moira cross-hero acceptance.

Fan01D2/node37 and Flashbang01E9/node7 are scoped here. Fan seed and mode binding
remain declared server reconstructions. Full McCree lives in the parent lane.
"""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000042


def fan_direction(shooter, state, forward, number: int, time: int, activation):
    """Scoped Fan cone; native math with a declared server seed-identity mapping."""
    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x01D2 or state.node.pos != 37):
        return forward
    launch = state.node.field('m_5EC1EB38')
    if (not isinstance(launch, dict) or launch.get('$') != 'STU_239B571E'
            or state.spread_ramp is None):
        return forward
    from ow174.game import weapon_spread

    low, high = (expr.to_float(state.evaluate(launch.get(name), watch=False))
                 for name in ('m_7F0FED02', 'm_429A6C85'))
    normalized = state.spread_at(time - state.start - state.offset)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + expr.f32(low))
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    # Native handle98, script/state word and six-bit counter identities are
    # not fully mapped. Use stored server identities, with no mutable RNG or
    # wall clock. This mapping is a reconstruction, not native seed parity.
    base = weapon_spread.seed_base(shooter.body, state.instance.id, state.node.pos, activation[0])
    # The inspected D74C0E and D79BB9 callers use uniform radius (modefalse).
    # Binding that mode to this scoped Fan remains a declared reconstruction.
    return weapon_spread.direction(forward, cone, base, number)


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
