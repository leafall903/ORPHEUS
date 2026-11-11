"""Selected Blink action with an authored distance and server capsule sweep."""

import math

from ow174.game import contacts
from ow174.game.script import expr

HERO = 0x02E0000000000003


def blink(instance, node, caller):
    """Handle only a currently posed, live Tracer's actual Blink action."""
    component = instance.component
    world = component.world
    if component.strict or world.hero != HERO or instance.graph.index != 0x0024 or node.pos != 3:
        return NotImplemented
    match = getattr(world, "match", None)
    if match is None or match.collision_world is None:
        return NotImplemented
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if (player is None or player.body_script is None
            or player.body_script.component is not component or component.owner_dead
            or match.combat.recalls.protected(player, frame=component.frame)
            or match.combat.is_stunned(player)
            or match.combat.sombra_hacks.silenced(player.body, match.tick)
            or match.combat.captured(player.body)):
        return False
    mover = player.mover
    if not mover.history or mover.history[-1].frame != component.frame:
        return False  # no authoritative pose for a recovered/unposed script frame
    distance = expr.to_float(instance.evaluate(node.field("m_6A79BD98")))
    if not math.isfinite(distance) or distance <= 0:
        return False
    forward, right = world.throttles
    if not (forward or right):
        forward = 127
    length = math.hypot(forward, right)
    angle = mover.yaw * 2 * math.pi / 65536
    dx = (math.sin(angle) * forward - math.cos(angle) * right) / length * distance
    dz = (math.cos(angle) * forward + math.sin(angle) * right) / length * distance
    body = mover._engine_body()
    start = body.position
    end = (start[0] + dx, start[1], start[2] + dz)
    hit = contacts.scene_sweep(mover.world.engine, start, end, body.local_points(),
                               body.radius, mover.category)
    share = max(0.0, hit[0] - 0.001 / distance) if hit is not None else 1.0
    final = (start[0] + dx * share, start[1], start[2] + dz * share)
    before = tuple(mover.position)
    mover._place(body, final, hit is not None)
    match.combat.walls.move_player(player, before)
    # The action runs after the normal pose. Replace its same-frame correction
    # snapshot instead of leaving the owning client at the pre-Blink position.
    mover.history[-1] = mover.snapshot(component.frame)
    match.combat.record_discontinuity(player)
    instance.follow(node, "m_outPlug", caller)
    return True
