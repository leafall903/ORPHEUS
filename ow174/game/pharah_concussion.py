"""Authored 104319 Concussive Blast with a scoped player velocity adapter."""

import math

from ow174.game import bots, cryo, fortify, phase_defenses
from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.script import expr

HERO, GRAPH, STATE = 0x02E0000000000008, 0x007B, 0


def payload(state):
    modify = state.node.field("m_D2D11CE9.m_modifyHealth") or {}

    def read(record, key):
        value = record.get(key)
        return 0.0 if value is None else expr.to_float(state.evaluate(value, watch=False))

    rings = tuple(sorted(
        (read(row, "m_radius"), read(row, "m_579D7CFF"), read(row, "m_10399AE7"))
        for row in modify.get("m_knockbackRings") or ()
    ))
    vertical = "m_583C208B" if modify.get("m_583C208B") is not None else "m_10399AE7"
    return read(modify, "m_E33EFEE1"), read(modify, vertical), rings


def _scale(distance, rings, column):
    previous = None
    for ring in rings:
        if distance <= ring[0]:
            if previous is None:
                return ring[column]
            fraction = (distance - previous[0]) / (ring[0] - previous[0])
            return previous[column] + fraction * (ring[column] - previous[column])
        previous = ring
    return 0.0


def apply(fight, rocket, point, visible_from):
    """Apply the authored magnitudes as radial horizontal and upward velocity deltas."""
    from ow174.game.combat import EPSILON, capsule_ray, segment_distance

    horizontal, vertical, rings = rocket.concussion
    for target in fight._targets():
        player = target.owner
        mover = getattr(player, "mover", None)
        bot = isinstance(player, bots.Bot)
        own = player is rocket.shooter
        if ((mover is None and not bot) or fight.vitals_of(target).dead or cryo.active(player)
                or phase_defenses.active(player) or fortify.scale(player) is not None):
            continue
        if not own and ((not fight.match.game_map.free_for_all and target.team == rocket.team)
                        or fight.zarya_barriers.contact(player) is not None):
            continue
        a, b = target.segment()
        distance = max(0.0, segment_distance(point, a, b) - target.radius)
        scalar = _scale(distance, rings, 2 if own else 1)
        if scalar <= 0 or not fight.visible_target(visible_from, target):
            continue
        direction, reach = capsule_ray(visible_from, target)
        if reach > EPSILON and fight.trace_barrier(rocket.shooter, visible_from, direction, reach - EPSILON):
            continue
        dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
        length = math.hypot(dx, dz)
        scale = horizontal * scalar / length if length > EPSILON else 0.0
        velocity = dx * scale, vertical * scalar, dz * scale
        if bot:
            category = MOVER
            if not fight.match.game_map.free_for_all and target.team in (0, 1):
                category = (TEAM0, TEAM1)[target.team]
            world = fight.ice_wall.movement_world(fight.match.collision_world)
            player.apply_impulse(velocity, fight.tick, world, category)
        else:
            frame = player.body_script.frame + 1 if player.body_script is not None else fight.tick
            mover.add_impulse(velocity, frame)
