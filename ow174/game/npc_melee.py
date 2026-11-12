"""Manual input and contact hosting for the authored Heavy Assault and Trooper melee."""

import math

from ow174.game import combat, immunity
from ow174.game.script import expr, nodes

BUTTON = 31
TROOPER = 0x02E00000000001AC
# Hero to graph, ability position, target query position and health action position.
LAYOUT = {
    0x02E00000000001BA: (0x140E, 9, 6, 5),
    TROOPER: (0x1422, 8, 5, 4),
}


def ability(component):
    layout = LAYOUT.get(component.world.hero)
    if layout is None or component.strict:
        return None
    graph, position, _, _ = layout
    for instance in component.instances.values():
        if instance.graph.index == graph:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if isinstance(state, nodes.Ability):
                return state
    return None


def contact_target(bot, state, query):
    match = state.instance.component.world.match
    if (match is None or match.ended or not bot.firing or bot.dead
            or match.combat.is_stunned(bot)):
        return None
    target = next((candidate for candidate in match.combat._enemies(bot)
                   if getattr(candidate.owner, "client", None) is not None
                   and not candidate.owner.gone and candidate.owner.entity == bot.attack_target), None)
    return target if target is not None and in_contact(bot, state, query, target) else None


def in_contact(bot, state, query, target):
    """Test the authored contact volume against a live capsule and static terrain."""
    node = state.instance.graph.node(query)
    if node is None:
        return False
    radius = expr.to_float(state.evaluate(node.field("m_radius"), watch=False))
    offset = state.evaluate(node.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3) or not math.isfinite(radius) or radius <= 0:
        return False
    x, y, z = bot.position
    dx, _, dz = combat.aim(round(bot.yaw), 0)
    eye = (x, y + offset.y, z)
    center = (x + dz * offset.x + dx * offset.z, eye[1], z - dx * offset.x + dz * offset.z)
    a, b = target.segment()
    collision = state.instance.component.world.match.collision_world
    return (combat.segment_distance(center, a, b) <= radius + target.radius
            and combat.capsule_visible(collision, eye, a, b, target.radius))


def _source(world, state, position):
    if state.node.pos != position:
        return None
    return manual_source(world, state.instance.component)


def manual_source(world, component):
    if component.strict or world.match is None:
        return None
    return next((bot for bot in world.match.bots if bot.entity == component.entity
                 and bot.spawner.get("manual_spawn") is True and bot.body_script is not None
                 and bot.body_script.component is component), None)


def native(world, cfg, instance):
    # Trooper's position 38 branch compares the first tracked body to its owner.
    if (world.hero != TROOPER or instance.graph.index != LAYOUT[TROOPER][0]
            or cfg.get("$") != "STU_71A87E92"):
        return NotImplemented
    bot = manual_source(world, instance.component)
    if bot is None:
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if entity == bot.entity:
        return expr.Vec3(*bot.position)
    return world.match.script_body_position(entity, bot.entity)


def track_targets(world, state):
    layout = LAYOUT.get(world.hero)
    if layout is None or state.instance.graph.index != layout[0]:
        return NotImplemented
    query = layout[2]
    bot = _source(world, state, query)
    if bot is None:
        return NotImplemented
    target = contact_target(bot, state, query)
    return (expr.Entity(target.entity),) if target is not None else ()


def modify_health(world, state, elapsed_ms):
    layout = LAYOUT.get(world.hero)
    if layout is None or state.instance.graph.index != layout[0]:
        return NotImplemented
    bot = _source(world, state, layout[3])
    if bot is None:
        return NotImplemented
    if elapsed_ms:
        return False
    target = contact_target(bot, state, layout[2])
    chosen = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if target is None or not isinstance(chosen, tuple) or expr.Entity(target.entity) not in chosen:
        return False
    settings = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    if not math.isfinite(amount) or amount <= 0:
        return False
    fight = world.match.combat
    barrier = fight.barrier_phase(target)
    if barrier is not None:
        fight.damage_personal_barrier(target.entity, barrier, bot, amount)
        return False
    vitals = fight.vitals_of(target)
    before = vitals.total
    fight._damage(bot, target, amount, False, None, fight.tick)
    if vitals.total < before and not vitals.dead and not immunity.fortify_active(target.owner, target.entity):
        _push(fight, bot, target, state, settings)
    return False


def _push(fight, bot, target, state, settings):
    # The graph provides force magnitudes. Their application as velocity is reconstructed.
    horizontal = expr.to_float(state.evaluate(settings["m_E33EFEE1"], watch=False))
    upward = expr.to_float(state.evaluate(settings["m_10399AE7"], watch=False))
    if not all(math.isfinite(value) and value >= 0 for value in (horizontal, upward)):
        return
    x, _, z = bot.position
    dx, dz = target.feet[0] - x, target.feet[2] - z
    distance = math.hypot(dx, dz)
    if distance:
        dx, dz = dx / distance, dz / distance
    else:
        dx, _, dz = combat.aim(round(bot.yaw), 0)
    fight.impulses.queue(target.owner, (horizontal * dx, upward, horizontal * dz))


def update(fight, bot, component, enabled):
    """Prefer melee while its authored contact volume reaches the manual target."""
    state = ability(component)
    if state is None:
        return False
    if BUTTON in component.held:
        component.edges([(BUTTON, False)])
    if state.flags & nodes.Ability.RUNNING:
        if not enabled:
            state.presses.clear()
            state.stop()
        return True
    if not enabled:
        state.presses.clear()
        return False
    if component.world.match is None:
        component.world.match = fight.match
    query = LAYOUT[component.world.hero][2]
    if contact_target(bot, state, query) is None:
        return False
    if state.remaining() <= 0 and BUTTON not in component.held:
        component.edges([(BUTTON, True)])
    return True


def stop(component):
    state = ability(component)
    if state is not None:
        state.presses.clear()
        state.stop()
        component.edges([(BUTTON, False)])
