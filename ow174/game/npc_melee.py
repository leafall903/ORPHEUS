"""Selected 104319 Trooper and Heavy Assault melee host."""

import math

from ow174.game.script import expr

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


def contacts(fight, actor, tracker):
    from ow174.game.combat import EYE, aim, capsule_visible, segment_distance

    if actor.dead or not actor.firing or actor.attack_mode != "melee" or fight.is_stunned(actor):
        return
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3):
        return
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    direction = aim(round(actor.yaw), 0)
    x, y, z = actor.position
    origin = (x, y + EYE, z)
    centre = (x + direction[0] * offset.z, y + offset.y, z + direction[2] * offset.z)
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
    return tuple(t.entity for t in contacts(fight, actor, state))


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
    for target in contacts(fight, actor, tracker):
        if target.entity not in tracker.targets:
            continue
        if not fight._damage(actor, target, amount, False, None, fight.tick):
            continue
        if fight.vitals_of(target).dead or not (horizontal or vertical):
            continue
        dx, dz = target.feet[0] - actor.position[0], target.feet[2] - actor.position[2]
        distance = math.hypot(dx, dz)
        x, z = (dx / distance, dz / distance) if distance else (0.0, 0.0)
        impulse = (x * horizontal, vertical, z * horizontal)
        if getattr(target.owner, "mover", None) is not None:
            target.owner.mover.apply_impulse(impulse)
        else:
            target.owner.apply_impulse(impulse, fight.tick, fight._collision())
    return True
