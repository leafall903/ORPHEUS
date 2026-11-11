"""Declared manual combat-controller policy; native controller allocation is open."""

from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB


def manual_components(bot):
    """Reconstruct a manual combat controller with4B present and5C absent."""
    eligible = bot.hero in (ENFORCER, 0x02E00000000001BB) and bot.spawner.get('manual_spawn') is True
    return (0x4B,) if eligible else None


def controller_predicate(components):
    """B96D3A98's CC9F61 tail checks4B present, then5C absent."""
    return components is not None and 0x4B in components and 0x5C not in components


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.graph.index != WEAPON):
        return NotImplemented
    target = cfg.get('m_entity')
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    if cfg.get('$') == 'STU_B96D3A98':
        return controller_predicate(world.controller_components)
    if cfg.get('$') == 'STU_9B7648C3':
        # ReplicatedBody.set_health supplies the authoritative aggregate here.
        from ow174.game.script.driver import OWNER_HEALTH

        slot = instance.component.entity_var(OWNER_HEALTH, create=False)
        return expr.f32(slot.value()) if slot is not None else 0.0
    return NotImplemented
