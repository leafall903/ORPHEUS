"""Host Winston 00FE's active Tesla search and health states."""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000009
GRAPH = 0x00FE


def tesla(fight, caster, command, script):
    from ow174.game.combat import EPSILON, _dot, _sub, aim, hit_def, ray_world

    if command.frame <= fight.tesla_frames.get(caster.body, -1):
        return
    fight.tesla_frames[caster.body] = command.frame
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    if instance is None:
        return
    search, damage = instance.states.get(10), instance.states.get(13)
    if search is None or damage is None or not (search.active and damage.active):
        return
    reach = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    angle = expr.to_float(search.evaluate(search.node.field("m_A30B383E"), watch=False))
    definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
    if definition is None or definition.amount <= 0 or not (reach > 0 and 0 < angle < 90):
        return
    amount = definition.amount * caster.quanta / 1e6
    slope = math.tan(math.radians(angle))
    origin, direction = fight._eye(caster), aim(*command.aim())
    view_tick = command.frame - command.view_delay(caster.quanta) * 1e6 / caster.quanta
    shields = {}
    victims = []
    # Resolve the entire damage slice before breaking any shell in it.
    for target in fight._enemies(caster):
        shown = fight.seen(target, view_tick)
        if shown is None:
            continue
        a, b = shown.segment()
        point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        offset = _sub(point, origin)
        distance = math.hypot(*offset)
        edge = max(0.0, distance - shown.radius)
        forward = _dot(offset, direction)
        lateral = math.sqrt(max(0.0, distance * distance - forward * forward))
        if edge > reach or forward < 0 or lateral > forward * slope + shown.radius:
            continue
        if distance > EPSILON:
            ray = tuple(value / distance for value in offset)
            wall = ray_world(fight._collision(), origin, ray, edge)
            if wall is not None or fight.walls.cast(origin, ray, edge) is not None:
                continue
            contact = fight._barrier_contact(caster, origin, ray, edge)
            if contact is not None:
                shields.setdefault(contact[1].body, contact)
                continue
        victims.append(target)
    for contact in shields.values():
        fight._damage_barrier(contact, amount)
    for target in victims:
        fight._damage(caster, target, amount, False, definition.loadout, command.frame,
                      tags=definition.tags, beam=True)
