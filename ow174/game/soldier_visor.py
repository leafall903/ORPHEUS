"""Selected 104319 Tactical Visor target search and rifle aim."""

import math

from ow174.game.content import SOLDIER
from ow174.game.script import expr

GRAPH = 0x0259
SEARCH = 7
RIFLE = 0x0254
VISOR_BULLET = 0x0400000000001C85
VISOR_HELIX = 0x0400000000001C86


def targets(world, state):
    from ow174.game.combat import _dot, aim, capsule_visible, segment_distance

    if world.hero != SOLDIER or state.index != SEARCH:
        return NotImplemented
    if world.match is None or world.match.collision_world is None:
        return ()
    fight = world.match.combat
    shooter = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if shooter is None or fight.is_dead(shooter) or fight.is_stunned(shooter):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    angle = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    if radius <= 0 or not 0 < angle <= 360:
        return ()
    threshold = math.cos(math.radians(angle / 2))
    origin, forward = fight._eye(shooter), aim(shooter.mover.yaw, shooter.mover.pitch)
    found = []
    for target in fight._enemies(shooter):
        a, b = target.segment()
        if segment_distance(origin, a, b) - target.radius > radius:
            continue
        offset = _offset(origin, a, b)
        distance = math.hypot(*offset)
        alignment = _dot(offset, forward) / distance if distance else 1.0
        if alignment >= threshold and capsule_visible(
            world.match.collision_world, origin, a, b, target.radius, walls=fight.walls,
        ):
            found.append((-alignment, distance, target.entity))
    return tuple(entity for _, _, entity in sorted(found))


def _offset(origin, a, b):
    return tuple((start + end) / 2 - axis for axis, start, end in zip(origin, a, b, strict=True))


def rifle_direction(fight, shooter, command, state, origin, direction):
    """Use the active authored search and the rifle's existing rewind pose."""
    component = state.instance.component
    visor = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    search = visor.states.get(SEARCH) if visor is not None else None
    if search is None or not search.active or not search.targets:
        return direction
    enemies = {target.entity: target for target in fight._enemies(shooter)}
    view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
    for entity in search.targets:
        target = enemies.get(entity)
        shown = fight.seen(target, view_tick) if target is not None else None
        if shown is not None:
            offset = _offset(origin, *shown.segment())
            distance = math.hypot(*offset)
            if distance:
                return tuple(axis / distance for axis in offset)
    return direction
