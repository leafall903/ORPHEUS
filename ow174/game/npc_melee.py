"""Selected 104319 Trooper and Heavy Assault melee host."""

import math

from ow174.game import bots, defenses, zarya_barrier
from ow174.game.script import expr

TROOPER_GRAPH = 0x1422
SPECS = {TROOPER_GRAPH: (0x0400000000001D40, 5, 4), 0x140E: (0x0400000000001EBB, 6, 5)}
BODIES = frozenset(spec[0] for spec in SPECS.values())
BUTTON = 31


def context(world, state, index):
    instance = state.instance
    spec = SPECS.get(instance.graph.index)
    if spec is None or state.node.pos != spec[index] or getattr(world, "match", None) is None:
        return None
    actor = next((b for b in world.match.bots if b.entity == instance.component.entity and b.body == spec[0]),
                 None)
    return (world.match.combat, actor, spec) if actor is not None else None


def volume(fight, actor, tracker):
    from ow174.game.combat import EYE, aim

    if actor.dead or fight.is_incapacitated(actor):
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
                and capsule_visible(fight._collision(), origin, a, b, target.radius, walls=fight.walls)):
            yield target


def trooper_ability(fight, actor, player):
    instance = next((i for i in actor.body_script.component.instances.values()
                     if i.graph.index == TROOPER_GRAPH), None)
    if instance is None:
        return None
    ability = instance.states.get(instance.graph.node(8).state)
    if ability is None or not ability.active:
        return None
    tracker = instance.node_state(instance.graph.node(SPECS[TROOPER_GRAPH][1]))
    if any(target.owner is player for target in contacts(fight, actor, volume(fight, actor, tracker))):
        return ability
    return None


def targets(world, state):
    found = context(world, state, 1)
    if found is None:
        return NotImplemented
    fight, actor, _ = found
    return tuple(expr.Entity(t.entity) for t in contacts(fight, actor, volume(fight, actor, state)))


def apply(world, state, elapsed_ms):
    found = context(world, state, 2)
    if found is None:
        return NotImplemented
    if elapsed_ms:
        return True
    fight, actor, spec = found
    tracker = state.instance.node_state(state.instance.graph.node(spec[1]))
    modify = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
    horizontal = expr.to_float(state.evaluate(modify.get("m_E33EFEE1"), watch=False))
    vertical = expr.to_float(state.evaluate(modify.get("m_583C208B"), watch=False))
    selected = {value.id for value in tracker.targets}
    bubbles = {bubble.body: bubble for bubble in zarya_barrier.active(fight)}
    for target in contacts(fight, actor, volume(fight, actor, tracker)):
        if target.entity not in selected or defenses.phase(target.owner)[0]:
            continue
        if (bubble := bubbles.get(target.entity)) is not None:
            bubble.damage(amount, actor.entity)
            continue
        fight._damage(actor, target, amount, False, None, fight.tick, world_tick=fight.tick)
        if (isinstance(target.owner, bots.Bot) or fight.vitals_of(target).dead
                or defenses.fortified(target.owner) or not (horizontal or vertical)):
            continue
        dx, dz = target.feet[0] - actor.position[0], target.feet[2] - actor.position[2]
        length = math.hypot(dx, dz)
        scale = horizontal / length if length else 0.0
        target.owner.mover.add_impulse((dx * scale, vertical, dz * scale), fight.tick)
    return True
