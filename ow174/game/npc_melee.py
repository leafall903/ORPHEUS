"""Scoped manual Talon melee contacts at the authored query entry."""

import math

from ow174.game.script import expr

GRAPHS = {0x0400000000001EBB: 0x140E, 0x0400000000001D40: 0x1422}
QUERY_AND_DAMAGE = {0x140E: (6, 5), 0x1422: (5, 4)}
WINDUP = {0x140E: (9, (29,)), 0x1422: (8, (14, 37))}


def cancel_windup(component):
    for instance in component.instances.values():
        phase = WINDUP.get(instance.graph.index)
        if phase is None:
            continue
        ability_pos, windup_positions = phase
        ability = instance.node_state(instance.graph.node(ability_pos))
        if ability is not None:
            ability.presses.clear()
            ability.stop()
        for position in (*windup_positions, QUERY_AND_DAMAGE[instance.graph.index][0]):
            state = instance.node_state(instance.graph.node(position))
            if state is not None:
                state.end(False)
                # Trooper's wait can queue TrackTargets even when it aborts.
                instance.drop_begins([state])


def track(world, state):
    instance = state.instance
    query = QUERY_AND_DAMAGE.get(instance.graph.index)
    if query is None or state.node.pos != query[0] or world.match is None:
        return
    fight = world.match.combat
    actor = next((b for b in world.match.bots if b.entity == instance.component.entity), None)
    if (actor is None or actor.dead or not actor.attack_input or actor.attack_action != "melee"
            or actor.body_script is None or actor.body_script.component is not instance.component
            or GRAPHS.get(actor.body) != instance.graph.index or fight.is_stunned(actor)):
        return

    from ow174.game import combat

    offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    if not isinstance(offset, expr.Vec3) or radius <= 0:
        return
    x, y, z = actor.position
    forward = combat.aim(round(actor.yaw), 0)
    right = (forward[2], 0, -forward[0])
    centre = (x + right[0] * offset.x + forward[0] * offset.z, y + offset.y,
              z + right[2] * offset.x + forward[2] * offset.z)
    origin = (x, y + combat.EYE, z)
    damage = instance.graph.node(query[1]).field("m_modifyHealth")
    amount = expr.to_float(instance.evaluate(damage["m_amount"], state))
    horizontal = expr.to_float(instance.evaluate(damage.get("m_E33EFEE1"), state))
    vertical = expr.to_float(instance.evaluate(damage.get("m_583C208B"), state))
    fight._melee_barriers(actor, ((centre, centre, radius),), amount)
    for target in fight._enemies(actor):
        a, b = target.segment()
        if (combat.segment_distance(centre, a, b) > radius + target.radius
                or fight.walls.blocked(origin, a)
                or not combat.capsule_visible(fight._collision(), origin, a, b, target.radius)):
            continue
        incoming = (target.feet[0] - x, 0, target.feet[2] - z)
        distance = math.hypot(*incoming)
        direction = tuple(axis / distance for axis in incoming) if distance else forward
        if combat.deflection.frontal(target, direction) is not None:
            continue
        fight._damage(actor, target, amount, False, None, fight.tick, tags=combat.damage_tags(state, damage))
        fight.knockback(target, (horizontal * direction[0], vertical, horizontal * direction[2]), fight.tick)
