"""Delivered Tracer Blink and input guards on the combined movement host."""

import math

from ow174.game import contacts
from ow174.game.script import expr

HERO = 0x02E0000000000003
BLINK_GRAPH = 0x0024
BLINK_ACTION = "STU_A807207A"
MOBILITY_BUTTONS = (29, 35, 36, 110, 112, 114)


def _player(world, component=None):
    if world.hero != HERO or world.match is None:
        return None
    player = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (player is None or player.body_script is None
            or (component is not None and player.body_script.component is not component)):
        return None
    return player


def recalling(owner):
    script = getattr(owner, "body_script", None)
    world = getattr(script, "world", None)
    match = getattr(world, "match", None)
    return match is not None and match.combat.recalls.locked(owner)


def filter_edges(world, edges):
    player = _player(world)
    if player is not None and player.body in world.match.combat.zarya_gravitons.pulled:
        # Reject new Blink and Recall presses while the world has captured this body.
        # Preserve releases so a rejected press cannot start when the pull expires.
        return [(button, down) for button, down in edges if not (down and button in MOBILITY_BUTTONS)]
    if player is not None and recalling(player):
        # Blink's logical-button nodes do not consult the ordinary ability
        # stack. Reconstruct its Recall exclusion before a press enters them.
        return [(button, down) for button, down in edges if not (down and button in (29, 35))]
    return edges


def blink(world, instance, node):
    """Handle only a currently posed, live Tracer's actual Blink action."""
    match = world.match
    if world.hero != HERO or instance.graph.index != BLINK_GRAPH or node.pos != 3:
        return NotImplemented
    if match is None or match.collision_world is None:
        return NotImplemented
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    component = instance.component
    if (player is None or player.body_script is None
            or player.body_script.component is not component or component.owner_dead
            or recalling(player)):
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
    match.combat.walls.move_mover(player.mover, before)
    # The action runs after the normal pose. Replace its same-frame correction
    # snapshot instead of leaving the owning client at the pre-Blink position.
    mover.history[-1] = mover.snapshot(component.frame)
    return True
