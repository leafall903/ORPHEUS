"""Declared manual combat-controller policy; native allocation remains open."""

import math

from ow174.game import weapon_spread
from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB


def projectile_directions(bot, state, forward):
    """Read Enforcer's 613BDDB0 rings. Native phase and muzzle remain unresolved."""
    if bot.hero != ENFORCER or state.instance.graph.index != WEAPON or state.node.pos != 24:
        return (forward,)
    launch = state.node.field('m_5EC1EB38')
    if not isinstance(launch, dict) or launch.get('$') != 'STU_613BDDB0':
        return (forward,)
    evaluate = state.evaluate
    count = expr.to_int(evaluate(state.node.field('m_numProjectilesPerShot'), watch=False))
    rings = [(expr.to_float(evaluate(row.get('m_6425FFB9'), watch=False)),
              expr.to_int(evaluate(row.get('m_B19BD35D'), watch=False)))
             for row in launch.get('m_0D30E40C') or ()]
    if not 0 < count <= 256 or sum(size for _, size in rings) != count or any(
        not 0 <= radius <= 1 or size <= 0 for radius, size in rings
    ):
        return (forward,)
    cone = math.radians(expr.to_float(evaluate(launch.get('m_spread'), watch=False)))
    return weapon_spread.ring_directions(forward, cone, rings)


def manual_components(bot):
    """Reconstruct a manual combat controller with4B present and5C absent."""
    eligible = bot.hero in (ENFORCER, 0x02E00000000001BB) and bot.spawner.get('manual_spawn') is True
    return (0x4B,) if eligible else None


def controller_predicate(components):
    """Original CC9F61 tail checks4B present, then5C absent."""
    return components is not None and 0x4B in components and 0x5C not in components


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.graph.index != WEAPON or cfg.get('$') != 'STU_B96D3A98'):
        return NotImplemented
    target = cfg.get('m_entity')
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    return controller_predicate(world.controller_components)
