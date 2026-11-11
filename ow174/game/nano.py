"""104319 Ana 0B9E/0B9F Nano Boost host.

The authored cast owns charge and release. Capsule targeting, outgoing
scaling, and expiry are reconstructed server policies.
"""

import math

from ow174.game import ana
from ow174.game.script import expr

GRAPH = 0x0B9E
HEALING = 250
BONUS = 0.5
INCOMING = 0.5
CONE = 10


def active(fight, vitals):
    return vitals is not None and not vitals.dead and fight.now < vitals.nano_until


def bonus(fight, actor):
    from ow174.game.combat import _source_entity

    return BONUS if active(fight, fight.vitals.get(_source_entity(actor))) else 0.0


def query(fight, state):
    instance = state.instance
    if instance.graph.index != GRAPH or state.index not in (3, 6):
        return NotImplemented
    source = next((p for p in fight.match.players
                   if p.body == instance.component.entity and p.has_body), None)
    if (source is None or source.hero.guid != ana.HERO or fight.is_dead(source)
            or fight.is_stunned(source) or fight.match.game_map.free_for_all):
        return ()
    from ow174.game.combat import aim, ray_world

    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin = fight._eye(source)
    forward = aim(source.mover.yaw, source.mover.pitch)
    threshold = math.cos(math.radians(CONE))
    candidates = []
    for target in fight._targets():
        if target.owner is source or target.team != source.team or fight.vitals_of(target).dead:
            continue
        x, y, z = target.feet
        delta = (x - origin[0], y + target.height / 2 - origin[1], z - origin[2])
        distance = math.hypot(*delta)
        if not 0 < distance <= radius:
            continue
        direction = tuple(d / distance for d in delta)
        alignment = sum(a * b for a, b in zip(direction, forward, strict=True))
        if alignment < threshold:
            continue
        wall = ray_world(fight._collision(), origin, direction, distance)
        if wall is None or wall >= distance:
            candidates.append((-alignment, distance, target.entity))
    return (expr.Entity(min(candidates)[2]),) if candidates else ()


def release(fight, source, state):
    if state.instance.graph.index != GRAPH or state.index != 1:
        return False
    if source.hero.guid != ana.HERO or fight.match.game_map.free_for_all:
        return True
    instance = state.instance
    selected = instance.find_var(expr.INSTANCE, 8070).value()
    duration = expr.to_float(instance.find_var(expr.INSTANCE, 215).value())
    if not math.isfinite(duration) or duration <= 0:
        return True
    selection = selected if isinstance(selected, tuple) else (selected,)
    entities = {expr.to_entity(item) for item in selection}
    for target in fight._targets():
        if target.entity not in entities or target.owner is source or target.team != source.team:
            continue
        vitals = fight.vitals_of(target)
        if not vitals.dead:
            fight._heal(source, target.owner, vitals, HEALING)
            vitals.nano_until = max(vitals.nano_until, fight.now + duration)
    return True
