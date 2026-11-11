"""Winston's authored Tesla primary with reconstructed cone and visibility."""

import math

from ow174.game.commands import FIRE
from ow174.game.script import expr
from ow174.game.script.combat_pose import active_pose

HERO = 0x02E0000000000009


def modify_health(world, state, elapsed_ms):
    from ow174.game.combat import _dot, _sub, aim, capsule_visible

    instance = state.instance
    if instance.graph.index != 0x00FE or state.node.pos != 37:
        return NotImplemented
    pose = active_pose.get()
    if pose is None or pose[0] is not world:
        return True
    _, player, command = pose
    fight = world.match.combat
    collision = world.match.collision_world
    if (collision is None or fight.is_dead(player) or fight.is_stunned(player)
            or not command.buttons & FIRE or instance.component.frame != command.frame):
        return True

    track = instance.graph.node(25)
    offset = state.evaluate(track.field("m_05BE62DD"), watch=False)
    reach = expr.to_float(state.evaluate(track.field("m_radius"), watch=False))
    angle = expr.to_float(state.evaluate(track.field("m_A30B383E"), watch=False))
    cosine = math.cos(math.radians(angle / 2))
    x, y, z = player.mover.position
    origin = (x+offset.x, y+offset.y, z+offset.z)
    direction = aim(*command.aim())
    targets = []
    for target in fight._enemies(player):
        a, b = target.segment()
        point = (a[0], max(a[1], min(origin[1], b[1])), a[2])
        delta = _sub(point, origin)
        distance = math.sqrt(_dot(delta, delta))
        if (distance <= reach and _dot(direction, delta) >= cosine*distance
                and capsule_visible(collision, origin, a, b, target.radius)):
            targets.append(target)
    instance.write(expr.dynamic(track.field("m_8A2905A0")),
                   tuple(expr.Entity(target.entity) for target in targets))

    settings = state.node.field("m_modifyHealth")
    rate = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    damage = rate * min(elapsed_ms, instance.component.frame_ms) / 1000
    if math.isfinite(damage) and damage > 0:
        loadout = state.evaluate(settings["m_loadout"], watch=False)
        for target in targets:
            fight._damage(player, target, expr.f32(damage), False, loadout.guid, command.frame)
    return True
