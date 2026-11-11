"""Scoped Trooper and Heavy Assault melee target and health hosts.

104319 provides button 31, two target volumes and the damage payloads.
Capsule acquisition, line of sight and contact/force ordering are reconstructed.
"""

import math

from ow174.game import defenses
from ow174.game.mover import FELL
from ow174.game.script import expr, nodes

BUTTON = 31
# Body -> (graph, TrackTargets position, ModifyHealth position).
PROFILES = {
    0x0400000000001D40: (0x1422, 5, 4),
    0x0400000000001EBB: (0x140E, 6, 5),
}


def instance_of(bot):
    profile = PROFILES.get(bot.body)
    if profile is None or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == profile[0]), None)


def cancel(bot):
    instance = instance_of(bot)
    if instance is not None:
        for state in tuple(instance.states.values()):
            if isinstance(state, nodes.Ability):
                state.presses.clear()
                state.stop()


def context(world, state, slot):
    if world.match is None:
        return None
    for bot in world.match.bots:
        if bot.entity != state.instance.component.entity:
            continue
        profile = PROFILES.get(bot.body)
        if profile is not None and (state.instance.graph.index, state.node.pos) == (profile[0], profile[slot]):
            return world.match.combat, bot, profile
    return None


def volume(bot, tracker):
    from ow174.game import combat

    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    fx, _, fz = combat.aim(round(bot.yaw), 0)
    x, y, z = bot.position
    centre = (x + fz * offset.x + fx * offset.z, y + offset.y, z - fx * offset.x + fz * offset.z)
    return (x, y + combat.EYE, z), centre, radius


def contacts(fight, bot, origin, centre, radius):
    from ow174.game.combat import capsule_visible, segment_distance

    if (bot.dead or not bot.attack_input or bot.attack_action != "melee"
            or fight.is_stunned(bot) or fight.hooks.controlled(bot.entity)):
        return
    for target in fight._enemies(bot):
        a, b = target.segment()
        if (segment_distance(centre, a, b) <= radius + target.radius
                and capsule_visible(fight._collision(), origin, a, b, target.radius, walls=fight.walls)
                and not defenses.parries_melee(target, origin)):
            yield target


def track(world, state):
    found = context(world, state, 1)
    if found is None:
        return NotImplemented
    fight, bot, _ = found
    return tuple(expr.Entity(target.entity) for target in contacts(fight, bot, *volume(bot, state)))


def apply(world, state, elapsed_ms):
    found = context(world, state, 2)
    if found is None:
        return NotImplemented
    fight, bot, profile = found
    if elapsed_ms or bot.dead or not bot.attack_input or bot.attack_action != "melee":
        return True
    tracker = state.instance.state(state.instance.graph.node(profile[1]).state)
    origin, centre, radius = volume(bot, tracker)
    selected = {entity.id for entity in tracker.targets}
    payload = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(payload["m_amount"], watch=False))
    loadout = state.evaluate(payload.get("m_loadout"), watch=False)
    for target in contacts(fight, bot, origin, centre, radius):
        if target.entity in selected:
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(bot, target, amount, False, expr.guid_of(loadout), fight.tick, world_tick=fight.tick)
            if vitals.total < before and not vitals.dead:
                push(state, payload, bot, target)
    if not fight.is_stunned(bot) and not fight.hooks.controlled(bot.entity):
        fight.damage_barriers_in_volumes(bot, ((centre, centre, radius),), amount)
    return True


def push(state, payload, bot, target):
    mover = getattr(target.owner, "mover", None)
    if mover is None or defenses.control_immune(target.owner):
        return
    force = expr.to_float(state.evaluate(payload.get("m_E33EFEE1"), watch=False))
    lift = expr.to_float(state.evaluate(payload.get("m_583C208B"), watch=False))
    if not force and not lift:
        return
    dx, dz = target.feet[0] - bot.position[0], target.feet[2] - bot.position[2]
    distance = math.hypot(dx, dz)
    scale = force / distance if distance else 0.0
    vx, vy, vz = mover.velocity
    mover.driven = False
    mover.velocity = (vx + dx * scale, vy + lift, vz + dz * scale)
    if mover.velocity[1] > 0:
        mover.flags_word |= FELL
        mover.fall = 0.0
