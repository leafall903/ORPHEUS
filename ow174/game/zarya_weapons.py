"""Particle Cannon's authored beam state with a scoped static ray host."""

import math

from ow174.game.script import expr, runtime

GRAPH = 0x0241
TRACKERS = (43, 81)
SECONDARY = 0x04000000000003DB


def contact(fight, caster, state):
    from ow174.game.combat import _along, aim, ray_capsule, ray_world

    reach = expr.to_float(state.evaluate(state.node.field("m_F5732403.m_6A79BD98"), watch=False))
    radius = expr.to_float(state.evaluate(state.node.field("m_57DAE1B5"), watch=False))
    if not math.isfinite(reach) or reach <= 0 or not math.isfinite(radius) or radius < 0:
        return None, None, None
    origin, direction = fight._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
    limit = ray_world(fight._collision(), origin, direction, reach)
    closest = reach if limit is None else limit
    wall = fight.walls.cast(origin, direction, closest, radius=radius)
    if wall is not None:
        closest = wall[0]
    body = None
    for target in fight._enemies(caster):
        distance = ray_capsule(origin, direction, *target.segment(), target.radius + radius)
        if distance is not None and distance < closest:
            closest, body = distance, target
    shield = fight._barrier_contact(caster, origin, direction, closest, radius)
    if shield is not None and shield[0] < closest:
        return _along(origin, direction, shield[0]), shield[1].body, shield
    hit = body is not None or wall is not None or limit is not None
    return (_along(origin, direction, closest) if hit else None,
            body.entity if body is not None else None, None)


def ray(fight, body, state):
    target = fight._target_entity(body)
    if target is None or fight.match.collision_world is None or fight.is_stunned(target.owner):
        return runtime.RaycastResult(False, expr.Entity(0), None, None)
    point, entity, _ = contact(fight, target.owner, state)
    return runtime.RaycastResult(point is not None, expr.Entity(entity or 0),
                                 expr.Vec3(*point) if point is not None else None, None)


def beam(fight, caster, command, script):
    from ow174.game.combat import damage_tags

    if command.frame <= fight.zarya_beam_frames.get(caster.body, -1):
        return
    fight.zarya_beam_frames[caster.body] = command.frame
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    state = instance.states.get(10) if instance is not None else None
    if state is None or not state.active:
        return
    selected = instance.vars.get(536)
    if selected is None or not expr.to_entity(selected.value()):
        return
    tracker = next((instance.states[index] for index in TRACKERS
                    if index in instance.states and instance.states[index].active), None)
    if tracker is None:
        return
    _, entity, shield = contact(fight, caster, tracker)
    if entity != expr.to_entity(selected.value()):
        return
    modify = state.node.field("m_modifyHealth")
    rate = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
    if not math.isfinite(rate) or rate <= 0:
        return
    amount = rate * script.component.frame_ms / 1000
    if shield is not None:
        fight._damage_barrier(shield, amount)
        return
    target = fight._target_entity(entity)
    if target is not None:
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        fight._damage(caster, target, amount, False, loadout, command.frame, tags=damage_tags(state, modify))
