"""Explicit reconstructed player bridge for Hanzo010C's movement graphs.

The graph owns input, availability, direction, launch quantities and cooldown.
The airborne variable, yaw basis and one-shot velocity-row interpretation here
are host policy, not a verified native movement/action implementation. The
scoped wall-climb host below also reconstructs contact geometry and time-budget
meanings. The type-2 ledge probe and path use a static reconstruction.
"""

import math
from dataclasses import dataclass

from ow174.game.collision import NOT_GROUND
from ow174.game.commands import JUMP
from ow174.game.mover import JUMP_LATCH, STEP, Mod, modified_throttles, throttle_byte
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
    mantle: tuple[float, tuple[tuple[float, float, float], ...]] | None = None


def _flux_controls_target_pose(component):
    """Preserve the delivery's 1DBC/node12 movement ownership predicate.

    The combined server has not mounted the separate Sigma Flux owner yet.
    Inspecting its authored state preserves arbitration without importing an
    obsolete second Sigma manager.
    """
    return any(
        (state := instance.node_state(instance.graph.node(12))) is not None and state.active
        for instance in component.instances.values() if instance.graph.index == 0x1DBC
    )


def _mantle_candidate(player, instance, command):
    """Resolve the authored type-2 probe values against static collision."""
    node = instance.graph.node(19)
    if node is None or node.cls != "STU_92ED01D0":
        return None
    state = instance.node_state(node)
    if state is None or not state.active:
        return None
    fields = ("m_3D8D7099", "m_BC8CFAF7", "m_0495F9E1", "m_94554C04",
              "m_70E47D79", "m_E7146E0B", "m_4E3BB2CE")
    facing, reach, wall_angle, lower, upper, forward, speed = (
        expr.to_float(state.evaluate(node.field(field), watch=False)) for field in fields
    )
    if (not all(math.isfinite(value) for value in (facing, reach, wall_angle, lower, upper, forward, speed))
            or not 0 < facing < 90 or not 0 < wall_angle < 90
            or min(reach, forward, speed) <= 0 or upper <= lower):
        return None
    mover = player.mover
    mods = sorted((mod for mod in mover.mods.values() if mod.acts(command.frame)), key=Mod.order)
    forward_input, _ = throttle_byte(command.forward)
    if modified_throttles(mods, 0.0, forward_input)[1] <= 0:
        return None
    radius = mover.values.crouch_radius if mover.crouched else mover.values.stand_radius
    height = mover.values.crouch_height if mover.crouched else mover.values.stand_height
    radius = max(0.1, radius)
    angle = command.yaw * math.tau / 65536
    dx, dz = math.sin(angle), math.cos(angle)
    x, y, z = mover.position
    world = mover.world
    category = mover.category
    direction = (dx, 0.0, dz)
    wall = world.cast_ray((x, y + height + lower, z), direction, radius + reach, category)
    if (wall is None or abs(wall.normal[1]) >= math.cos(math.radians(wall_angle))
            or -dx * wall.normal[0] - dz * wall.normal[2] < math.cos(math.radians(facing))):
        return None
    top = y + height + upper
    depth = radius + forward
    if (world.cast_ray((x, y + height, z), (0.0, 1.0, 0.0), height + upper, category)
            or world.cast_ray((x, top, z), direction, depth, category)):
        return None
    tx, tz = x + dx * depth, z + dz * depth
    floor = world.cast_ray((tx, top, tz), (0.0, -1.0, 0.0), upper - lower, category)
    if floor is None or floor.surface & NOT_GROUND or floor.normal[1] <= mover.slope_cos:
        return None
    if instance.component.strict:
        instance.component.unsupported(instance, "reconstructed Hanzo mantle", node.cls, node)
    # Sweep up before crossing the lip. Normal movement lands on the support.
    level = top - floor.distance + 2 * STEP
    return max(0.1, speed), ((x, level, z), (tx, level, tz))


def climb_request(player, command, seconds, *, superseded=False):
    """Resolve the active010C contact candidate into a capsule-swept upward move.

    Interpret node21's0.8/45 and row0's up/4.5/1.5 as probe reach, facing
    angle, direction, speed and airborne time budget. These meanings, the
    chest ray and type-2 arbitration are reconstructed, not native phase hooks.
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
    if superseded or script.component.owner_dead or _flux_controls_target_pose(script.component):
        progress.mantle = None
        return None
    if not command.buttons & JUMP:
        progress.blocked = False
        progress.mantle = None
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    if instance is None or progress.blocked:
        return None
    if progress.mantle is None and player.mover.airborne and progress.elapsed == 0 and command.forward > 0:
        progress.mantle = _mantle_candidate(player, instance, command)
    if progress.mantle is not None:
        speed, route = progress.mantle
        return route[0], speed
    node = instance.graph.node(21)
    if node is None or node.cls != "STU_798B41F4":
        return None
    state = instance.node_state(node)
    if state is None or not state.active:
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
    # During a hosted climb, let an eligible type-2 route take over before
    # the chest probe loses the face or the airborne climb budget runs out.
    if was_active and player.mover.airborne and command.forward > 0:
        progress.mantle = _mantle_candidate(player, instance, command)
        if progress.mantle is not None:
            mantle_speed, route = progress.mantle
            return route[0], mantle_speed
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


def climbed(player, start_y, outcome, frame):
    progress = player.body_script.world.hanzo_climb
    if progress.mantle is not None:
        # World-target movement suppresses jump and may clear the mover latch
        # when it finds the ledge. Keep the original held press consumed.
        mover = player.mover
        if not mover.flags_word & JUMP_LATCH:
            mover.flags_word |= JUMP_LATCH
            mover.history[-1] = mover.snapshot(frame)
        if outcome is not None:
            speed, route = progress.mantle
            progress.mantle = (speed, route[1:]) if outcome and len(route) > 1 else None
            progress.blocked = progress.mantle is None
    elif progress.active and player.mover.position[1] <= start_y + 1e-9:
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
