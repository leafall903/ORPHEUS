"""Explicit manual Enforcer controller policy; native allocation remains open."""

from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB


def manual_components(bot):
    """A declared sandbox context, not an inferred/native controller entity."""
    eligible = bot.hero in (ENFORCER, 0x02E00000000001BB) and bot.spawner.get('manual_spawn') is True
    return (0x4B,) if eligible else None


def controller_predicate(components):
    """The recovered B96D3A98 predicate tail:4B present and5C absent."""
    return components is not None and 0x4B in components and 0x5C not in components


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.component.strict or instance.graph.index != WEAPON
            or cfg.get('$') != 'STU_B96D3A98'):
        return NotImplemented
    target = cfg.get('m_entity')
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    return controller_predicate(world.controller_components)
