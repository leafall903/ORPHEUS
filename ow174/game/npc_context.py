"""Scoped manual Enforcer controller queries for the authored weapon graph."""

from ow174.game.assassin import HERO as ASSASSIN
from ow174.game.script import expr
from ow174.game.script.driver import OWNER_HEALTH

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB


def manual_components(bot):
    """Declare the sandbox controller with component 4B present and 5C absent."""
    return (0x4B,) if bot.hero in (ENFORCER, ASSASSIN) and bot.spawner.get("manual_spawn") is True else None


def native(world, cfg, instance):
    components = world.controller_components
    query = cfg.get("$")
    if (world.hero != ENFORCER or components is None or instance is None
            or instance.graph.index != WEAPON or query not in {"STU_B96D3A98", "STU_9B7648C3"}):
        return NotImplemented
    target = cfg.get("m_entity")
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    if query == "STU_B96D3A98":
        return 0x4B in components and 0x5C not in components
    slot = instance.component.entity_var(OWNER_HEALTH, create=False)
    return expr.f32(slot.value()) if slot is not None else 0.0
