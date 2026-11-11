"""Selected 104319 Trooper and Heavy Assault melee host."""

import math

from ow174.game.script import expr, support

SPECS = {0x1422: (0x0400000000001D40, 5, 4), 0x140E: (0x0400000000001EBB, 6, 5)}
BODIES = frozenset(spec[0] for spec in SPECS.values())
BUTTON = 31


def context(world, state, index):
    instance = state.instance
    spec = SPECS.get(instance.graph.index)
    if world.match is None or spec is None or state.node.pos != spec[index]:
        return None
    actor = next((b for b in world.match.bots if b.entity == instance.component.entity and b.body == spec[0]),
                 None)
    return (world.match.combat, actor, spec) if actor is not None else None


def volume(fight, actor, tracker):
    from ow174.game.combat import EYE, aim

    if actor.dead or fight.is_stunned(actor):
        return
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3):
        return
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    direction = aim(round(actor.yaw), 0)
    x, y, z = actor.position
    origin = (x, y + EYE, z)
    centre = (x + direction[0] * offset.z, y + offset.y, z + direction[2] * offset.z)
    return origin, centre, radius


def contacts(fight, actor, sphere):
    from ow174.game.combat import capsule_visible, segment_distance

    if sphere is None:
        return
    origin, centre, radius = sphere
    for target in fight._enemies(actor):
        a, b = target.segment()
        if (segment_distance(centre, a, b) <= radius + target.radius
                and capsule_visible(fight._collision(), origin, a, b, target.radius)):
            yield target


def targets(world, state):
    found = context(world, state, 1)
    if found is None:
        return NotImplemented
    fight, actor, _ = found
    return tuple(t.entity for t in contacts(fight, actor, volume(fight, actor, state)))


def apply(world, state, elapsed_ms):
    found = context(world, state, 2)
    if found is None:
        return NotImplemented
    if elapsed_ms:
        return True
    fight, actor, spec = found
    tracker = state.instance.node_state(state.instance.graph.node(spec[1]))
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    horizontal = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1"), watch=False))
    vertical = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_583C208B"), watch=False))
    boost = fight._damage_boost(actor)
    scale = boost[1] if boost is not None else 1.0
    tags = support.modify_health_tags(state, state.node.field("m_modifyHealth"))
    sphere = volume(fight, actor, tracker)
    if sphere is not None:
        fight.melee_barriers(actor, *sphere, amount * scale)
    for target in contacts(fight, actor, sphere):
        if target.entity not in tracker.targets or fight.damage_immune(target):
            continue
        absorbed = fight._damage(
            actor, target, amount * scale, False, None, fight.tick, boost_credit=boost, melee_tags=tags,
        )
        if not absorbed and amount > 0:
            dx, dz = target.feet[0] - actor.position[0], target.feet[2] - actor.position[2]
            distance = math.hypot(dx, dz)
            x, z = (dx / distance, dz / distance) if distance else (0.0, 0.0)
            fight.queue_impulse(target, (x * horizontal, vertical, z * horizontal))
    return True
