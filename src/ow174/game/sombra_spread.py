"""Original pistol cone math with a declared server seed/mode mapping.

Native handle identities, recoil/muzzle transforms and sampling-mode binding
remain unaccepted. No mutable RNG, hidden clocks or copied asset tables.
"""

import math

from ow174.game import weapon_spread
from ow174.game.script import expr

HERO = 0x02E000000000012E


def direction(shooter, state, forward, number, time, activation):
    if (getattr(getattr(shooter, 'hero', None), 'guid', None) != HERO
            or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x0B1B or state.spread_ramp is None):
        return forward
    launch = state.node.field('m_5EC1EB38')
    if not isinstance(launch, dict) or launch.get('$') != 'STU_239B571E':
        return forward
    low, high = (expr.to_float(state.evaluate(launch.get(name), watch=False))
                 for name in ('m_7F0FED02', 'm_429A6C85'))
    normalized = state.spread_at(time - state.start - state.offset)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + expr.f32(low))
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    # Reconstructed caller identities, matching the reviewed server Fan policy.
    # Native seed arithmetic is retained; uniform radius is an explicit policy.
    base = weapon_spread.seed_base(shooter.body, state.instance.id, state.node.pos, activation[0])
    return weapon_spread.direction(forward, cone, base, number)
