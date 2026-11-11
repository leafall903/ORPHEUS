"""Opt-in manual Enforcer controller gate, not native controller allocation.

The selected controller's75/92 tail is known; assigning a manual context is a
server policy. Related Entity/foreign namespaces remain unsupported.
"""
from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB


def manual_components(bot):
    eligible = bot.hero in (ENFORCER, 0x02E00000000001BB) and bot.spawner.get('manual_spawn') is True
    return (75,) if eligible else None


def controller_predicate(components):
    return components is not None and 75 in components and 92 not in components


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.graph.index != WEAPON or cfg.get('$') != 'STU_B96D3A98'):
        return NotImplemented
    target = cfg.get('m_entity')
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    return controller_predicate(world.controller_components)
