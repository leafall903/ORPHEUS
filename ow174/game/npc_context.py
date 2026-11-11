"""Declared manual NPC controller contexts; native allocation remains open."""

from ow174.game.script import expr
from ow174.game.script.driver import OWNER_HEALTH

ENFORCER = 0x02E00000000001CE
ASSASSIN = 0x02E00000000001BB
WEAPON = 0x13EB


def manual_components(bot):
    return (75,) if bot.hero in (ENFORCER, ASSASSIN) and bot.spawner.get("manual_spawn") is True else None


def controller_predicate(components):
    """104319 CC9F61 checks component75 present and component92 absent."""
    return components is not None and 75 in components and 92 not in components


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.graph.index != WEAPON):
        return NotImplemented
    target = cfg.get("m_entity")
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    if cfg.get("$") == "STU_B96D3A98":
        return controller_predicate(world.controller_components)
    if cfg.get("$") == "STU_9B7648C3":
        # Host aggregate-health adapter; native pool/getter semantics remain open.
        slot = instance.component.entity_var(OWNER_HEALTH, create=False)
        return expr.f32(slot.value()) if slot is not None else 0.0
    return NotImplemented
