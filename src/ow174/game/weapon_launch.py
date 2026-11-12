"""Scoped authoritative launch policies from104319 resources.

Cone mathematics/table generation are reconstructed native functions. The stored
server seed identities and uniform mode binding are declared policies. Enforcer
uses the authored1+8+16 counts with fixed concentric-ring orientation; its exact
native ring orientation, muzzle transform and sampling semantics are unproved.
"""

import math

from ow174.game import weapon_spread
from ow174.game.script import expr

WIDOWMAKER, MCCREE, ENFORCER = 0x02E000000000000A, 0x02E0000000000042, 0x02E00000000001CE
OWNED = {WIDOWMAKER: (0x001B, (9, 19)), MCCREE: (0x01D2, (37,))}


def _cross(a, b):
    return a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]


def directions(shooter, state, forward, number, time, activation):
    """One scoped cone ray or the declared Enforcer pellet pattern; other launches pass through."""
    hero = getattr(shooter.hero, 'guid', shooter.hero)
    instance = state.instance
    launch = state.node.field('m_5EC1EB38')
    script = getattr(shooter, 'body_script', None)
    if not isinstance(launch, dict) or script is None or instance.component is not script.component:
        return (forward,)
    if hero == ENFORCER and instance.graph.index == 0x13EB and state.node.pos == 24:
        return _enforcer(state, launch, forward)
    spec = OWNED.get(hero)
    if (spec is None or getattr(instance.component.world, 'body', None) != instance.component.entity
            or instance.graph.index != spec[0] or state.node.pos not in spec[1]
            or launch.get('$') != 'STU_239B571E' or state.spread_ramp is None):
        return (forward,)
    low, high = (expr.to_float(state.evaluate(launch.get(name), watch=False))
                 for name in ('m_7F0FED02', 'm_429A6C85'))
    # Current recovered AE71E0 caller samples the emitted index before increment.
    normalized = state.spread_for_index(number)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + expr.f32(low))
    if degrees <= 0:
        return (forward,)  # a scoped zero-angle launch preserves the exact command ray
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    base = weapon_spread.seed_base(shooter.body, instance.id, state.node.pos, activation[0])
    return (weapon_spread.direction(forward, cone, base, number),)


def _enforcer(state, launch, forward):
    if launch.get('$') != 'STU_613BDDB0':
        raise ValueError('unsupported Enforcer launch pattern')
    spread = expr.to_float(state.evaluate(launch.get('m_spread'), watch=False))
    count = expr.to_int(state.evaluate(state.node.field('m_numProjectilesPerShot'), watch=False))
    rows = launch.get('m_0D30E40C') or []
    rings = [(expr.to_float(state.evaluate(row.get('m_6425FFB9'), watch=False)),
              expr.to_int(state.evaluate(row.get('m_B19BD35D'), watch=False))) for row in rows]
    if (count != 25 or sum(n for _, n in rings) != count
            or not math.isfinite(spread) or not 0 <= spread < 90
            or any(not math.isfinite(r) or not 0 <= r <= 1 or n <= 0 for r, n in rings)):
        raise ValueError('unsupported Enforcer pellet configuration')
    right = _cross(forward, (0, 1, 0))
    length = math.sqrt(sum(x*x for x in right))
    right = tuple(x / length for x in right) if length else (1, 0, 0)
    up = _cross(right, forward)
    result = []
    for radius, pellets in rings:
        width = math.tan(math.radians(spread * radius))
        for index in range(pellets):
            angle = math.tau * index / pellets
            vector = tuple(forward[k] + width * (right[k]*math.cos(angle) + up[k]*math.sin(angle))
                           for k in range(3))
            length = math.sqrt(sum(x*x for x in vector))
            result.append(tuple(x / length for x in vector))
    return tuple(result)
