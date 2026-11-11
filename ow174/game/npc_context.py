"""Explicit manual combat-controller policy; native allocation remains unverified."""

from ow174.game import assassin
from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB


def manual_components(bot):
    """Supply the declared manual controller profile, not an inferred native allocation."""
    if bot.hero in (ENFORCER, assassin.HERO) and bot.spawner.get("manual_spawn") is True:
        return (75,)
    return None


def controller_predicate(components):
    """Actual B96D3A98 tail CC9F61 requires component75 and excludes92."""
    return components is not None and 75 in components and 92 not in components


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.graph.index != WEAPON or cfg.get("$") != "STU_B96D3A98"):
        return NotImplemented
    target = cfg.get("m_entity")
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    return controller_predicate(world.controller_components)
