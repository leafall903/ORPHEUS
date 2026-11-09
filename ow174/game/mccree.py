"""Reviewed parent McCree damage defaults/Fan rays only; other bridges are not copied."""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000042


def critical_default(state):
    """Declared McCree primary implicit2x policy; not a recovered global default."""
    world = state.instance.component.world
    if (getattr(world, 'hero', None) == HERO
            and getattr(world, 'body', None) == state.instance.component.entity
            and state.instance.graph.index == 0x01D2 and state.node.pos == 31):
        return 2.0
    return 1.0


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
