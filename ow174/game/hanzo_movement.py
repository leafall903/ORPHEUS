"""Explicit reconstructed player bridge for Hanzo010C's movement graphs.

The graph owns input, availability, direction, launch quantities and cooldown.
The airborne variable, yaw basis and one-shot velocity-row interpretation here
are host policy, not a verified native movement/action implementation. The
scoped wall-climb host below also reconstructs contact geometry and time-budget
meanings. Mantling and arbitrary FF3471E9 variants remain unsupported.
"""

import math
from dataclasses import dataclass

from ow174.game.commands import JUMP
from ow174.game.script import expr

HANZO = 0x02E0000000000005
GRAPH = 0x010C
AIRBORNE = 417
IN_AIR = 0x80000C


@dataclass
class Climb:
    """Reconstructed per-body climb budget, retained until a grounded pose."""

    elapsed: float = 0.0
    active: bool = False
    blocked: bool = False
    normal: tuple | None = None


def climb_request(player, command, seconds, *, superseded=False):
    """Resolve the active010C contact candidate into a capsule-swept upward move.

    Interpret node21's0.8/45 and row0's up/4.5/1.5 as probe reach, facing
    angle, direction, speed and airborne time budget. These meanings, the
    chest ray and no-mantle exit are reconstructed, not native phase hooks.
    """
    script = player.body_script
    if player.hero.guid != HANZO or script is None:
        return None
    progress = script.world.hanzo_climb
    if progress is None:
        progress = script.world.hanzo_climb = Climb()
    was_active = progress.active
    progress.active = False
    if not player.mover.airborne and not was_active:
        progress.elapsed = 0.0
        progress.blocked = False
        progress.normal = None
    if superseded or script.component.owner_dead:
        return None
    if not command.buttons & JUMP:
        progress.blocked = False
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    node = instance.graph.node(21) if instance is not None else None
    if node is None or node.cls != "STU_798B41F4":
        return None
    state = instance.node_state(node)
    if state is None or not state.active or progress.blocked:
        return None
    if script.component.strict:
        script.component.unsupported(instance, "reconstructed Hanzo wall climb", state.node.cls, state.node)
    rows = state.node.field("m_82B8DE3B") or ()
    if len(rows) != 1 or rows[0].get("$") != "STU_D906F79D":
        return None
    row = rows[0]
    up = state.evaluate(row.get("m_408295AF"), watch=False)
    if up != expr.Vec3(0.0, 1.0, 0.0):
        return None
    output = expr.lvalue(state.node.field("m_7F8E47ED"))
    if output != (expr.INSTANCE, 1207):
        return None
    reach = expr.to_float(state.evaluate(state.node.field("m_BC8CFAF7"), watch=False))
    angle = expr.to_float(state.evaluate(state.node.field("m_0495F9E1"), watch=False))
    speed = expr.to_float(state.evaluate(row.get("m_82BF9656"), watch=False))
    budget = expr.to_float(state.evaluate(row.get("m_21411EEE"), watch=False))
    if (not all(math.isfinite(v) for v in (reach, angle, speed, budget))
            or min(reach, speed, budget) <= 0 or not 0 < angle < 90):
        return None
    remaining = budget - progress.elapsed
    if remaining <= 1e-9:
        return None
    yaw = command.yaw * (2 * math.pi / 65536)
    direction = (math.sin(yaw), 0.0, math.cos(yaw))
    x, y, z = player.mover.position
    hit = player.mover.world.cast_ray((x, y + up.y, z), direction, reach, player.mover.category)
    if hit is None:
        return None
    facing = -sum(a * b for a, b in zip(direction, hit.normal, strict=True))
    if (facing + 1e-6 < math.cos(math.radians(angle))
            or abs(hit.normal[1]) > math.sin(math.radians(angle))):
        return None
    progress.normal = hit.normal
    progress.active = True
    progress.elapsed += min(seconds, remaining)
    instance.write(output, expr.Vec3(*hit.normal))
    # Existing fixed-target motion handles contact and ceiling sweeps. A far
    # endpoint preserves speed until the final, distance-clipped budget step.
    return (x, y + speed * remaining, z), speed


def climbed(player, start_y):
    progress = player.body_script.world.hanzo_climb
    if progress is not None and progress.active and player.mover.position[1] <= start_y + 1e-9:
        progress.active = False
        progress.blocked = True


def owner(world):
    if world.hero != HANZO or world.match is None:
        return None
    return next((player for player in world.match.players
                 if player.body == world.body and not player.gone), None)


def sync(player):
    """Publish only this body's reconstructed airborne input before its graph runs."""
    if player.hero.guid == HANZO and player.body_script is not None:
        component = player.body_script.component
        airborne = bool(player.mover.flags_word & IN_AIR)
        progress = player.body_script.world.hanzo_climb
        component.set(AIRBORNE, airborne)
        component.set(545, not airborne)  # reconstructed grounded reset for010C/node24
        component.set(1204, bool(progress is not None and progress.active))


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
