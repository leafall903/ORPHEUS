"""Declared controller/health inputs for manually spawned Talon combat bodies.

The75-without92 composition and aggregate health query are server reconstruction
policies. No native controller allocation,namespace entity or general health
query is inferred. The generic B96 evaluator still performs its usual checks.
"""

from ow174.game.script import controller_query, expr

ENFORCER, ASSASSIN = 0x02E00000000001CE, 0x02E00000000001BB
ENFORCER_WEAPON = 0x13EB


def manual_components(bot):
    return (75,) if (bot.hero in (ENFORCER, ASSASSIN)
                      and bot.spawner.get("manual_spawn") is True) else None


def context(world, owner, components):
    if components is None:
        return None
    if (type(components) is not tuple or any(type(value) is not int or value < 0
                                           for value in components)):
        raise ValueError("manual controller components require explicit indices")
    world.manual_controller_target = controller_query.Target(owner, True)
    # Enforcer needs only a data-only descriptor0. Assassin has a world-local
    # typed ID for its target variable;neither is allocated or sent on the wire.
    # Lifetime belongs to this BotWorld.
    descriptor = (0xD0000000 | (owner & 0x0FFFFFFF)) if world.hero == ASSASSIN else 0
    world.manual_controller_descriptor = controller_query.Controller(descriptor,
                                                                   75 in components, 92 in components)
    return controller_query.Context(owner, True, lookup=world.lookup_manual_controller,
        resolve_controller=world.resolve_manual_controller, watch_component43=world.watch_manual_controller)


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.manual_controller_context is None
            or instance is None or instance.graph.index != ENFORCER_WEAPON
            or cfg.get("$") != "STU_9B7648C3"):
        return NotImplemented
    if expr.to_entity(instance.evaluate(cfg.get("m_entity"))) != instance.component.entity:
        return NotImplemented
    from ow174.game.script.driver import OWNER_HEALTH

    slot = instance.component.entity_var(OWNER_HEALTH, create=False)
    return expr.f32(slot.value()) if slot is not None else 0.0


def projectile_directions(shooter, state, direction):
    if shooter.hero != ENFORCER or state.instance.graph.index != ENFORCER_WEAPON:
        return (direction,)
    from ow174.game.shot_patterns import ring_directions

    return ring_directions(state, direction)
