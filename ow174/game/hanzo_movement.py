"""Explicit reconstructed player bridge for Hanzo010C's authored Lunge.

The graph owns input, availability, direction, launch quantities and cooldown.
The airborne variable, yaw basis and one-shot velocity-row interpretation here
are host policy, not a verified native movement/action implementation. Climbing
and arbitrary FF3471E9 variants remain unsupported.
"""

import math

from ow174.game.script import expr

HANZO = 0x02E0000000000005
GRAPH = 0x010C
AIRBORNE = 417
IN_AIR = 0x80000C


def owner(world):
    if world.hero != HANZO or world.match is None:
        return None
    return next((player for player in world.match.players
                 if player.body == world.body and not player.gone), None)


def sync(player):
    """Publish only this body's reconstructed airborne input before its graph runs."""
    if player.hero.guid == HANZO and player.body_script is not None:
        player.body_script.component.set(AIRBORNE, bool(player.mover.flags_word & IN_AIR))


def native(world, cfg, instance):
    cls = cfg.get("$")
    if (world.hero != HANZO or instance.graph.index != GRAPH
            or cls not in ("STU_66F3B89E", "STU_021D01CD")):
        return NotImplemented
    if instance.component.strict:
        instance.component.unsupported(instance, "reconstructed Hanzo yaw basis", cls)
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    player = owner(world)
    if player is None:
        return NotImplemented
    angle = player.mover.yaw * (2 * math.pi / 65536)
    sine, cosine = expr.f32(math.sin(angle)), expr.f32(math.cos(angle))
    # 010C/node83 uses -throttle.x * left + throttle.y * forward.
    return (expr.Vec3(cosine, 0.0, -sine) if cls == "STU_66F3B89E"
            else expr.Vec3(sine, 0.0, cosine))


def velocity_action(world, instance, node):
    if world.hero != HANZO or instance.graph.index != GRAPH or node.pos != 82:
        return NotImplemented
    if instance.component.strict:
        instance.component.unsupported(instance, "reconstructed Hanzo launch velocity", node.cls, node)
    rows = node.field("m_BC5E91CF") or ()
    if (len(rows) != 2 or rows[0].get("$") != "STU_9AF0FA80"
            or rows[0].get("m_A9561CA0") != 0
            or rows[1].get("$") != "STU_4635C5E2"
            or rows[1].get("m_CFEC5F76") != 1
            or any(row.get("m_priority") is not None for row in rows)):
        return NotImplemented
    clear = instance.evaluate(rows[0].get("m_9B7A63EA"))
    launch = instance.evaluate(rows[1].get("m_9B7A63EA"))
    if clear != 0 or type(launch) is not expr.Vec3:
        return NotImplemented
    velocity = (launch.x, launch.y, launch.z)
    if not all(math.isfinite(axis) for axis in velocity):
        return NotImplemented
    player = owner(world)
    if player is None or world.match.combat.is_dead(player):
        return True
    # Interpret the authored clear/add pair as replacing launch velocity. The
    # existing capsule mover applies gravity, collision and air drag afterward.
    player.mover.velocity = velocity
    player.mover.fall = 0.0
    if player.mover.history and player.mover.history[-1].frame == instance.component.frame:
        player.mover.history[-1] = player.mover.snapshot(instance.component.frame)
    return True
