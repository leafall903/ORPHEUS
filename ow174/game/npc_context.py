"""Scoped manual Talon context, following the parent native-tail evidence.

B96D3A98 native CC9F61 tests controller component4B present and5C absent.
Creating that manual controller and mapping9B7648C3 to aggregate body health
are declared server reconstructions. Native allocation and general queries stay open.
"""

from ow174.game import assassin
from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB
PROJECTILE = 0x04000000000023F0
WEAPON_REPLAY_TICKS = 512


def retains_weapon(bot):
    """Keep the supported manual Talon attacks on the combat clock."""
    from ow174.game.npc_navigation import HEAVY, SNIPER, TROOPER

    talon = (TROOPER, ENFORCER, SNIPER, assassin.HERO, HEAVY)
    return (bot.hero in talon and bot.spawner.get("manual_spawn") is True
            and not bot.dead and bot.body_script is not None and (bot.firing or bot.autonomous))


def manual_components(bot):
    eligible = bot.hero in (ENFORCER, assassin.HERO) and bot.spawner.get("manual_spawn") is True
    return (0x4B,) if eligible else None


def controller_predicate(components):
    return components is not None and 0x4B in components and 0x5C not in components


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
        from ow174.game.script.driver import OWNER_HEALTH

        slot = instance.component.entity_var(OWNER_HEALTH, create=False)
        return expr.f32(slot.value()) if slot is not None else 0.0
    return NotImplemented


def projectile_directions(shooter, state, direction):
    if shooter.hero != ENFORCER or state.instance.graph.index != WEAPON:
        return (direction,)
    from ow174.game.shot_patterns import ring_directions

    return ring_directions(state, direction)
