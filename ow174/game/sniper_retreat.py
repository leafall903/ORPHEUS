"""Bounded live host for the opt-in Sniper 142C reposition branch."""

import math

from ow174.game.collision import NOT_GROUND
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr

HERO = 0x02E00000000001B8
GRAPH = 0x142C
CLOSE_RANGE = 3.0
RETRY_MS = 6000  # Static host re-admission policy.


def _root(bot):
    if bot.hero != HERO or bot.body_script is None or not bot.spawner.get("acquire_specials"):
        return None
    component = bot.body_script.component
    if component.strict or component.fault is not None:
        return None
    return next((item for item in component.instances.values() if item.graph.index == GRAPH), None)


def _active(root, pos):
    state = root.states.get(root.graph.node(pos).state)
    return state if state is not None and state.active and not state.ending else None


def _target(fight, bot, root):
    entity = root.component.world.sniper_target
    if entity is None:
        return None
    return next((target for target in fight._enemies(bot) if target.entity == entity), None)


def acquire(bot, selected):
    """Feed the visible close threat to the actual 142C graph gate."""
    root = _root(bot)
    if root is None:
        return False
    world = root.component.world
    if root.context().var(expr.INSTANCE, 59) == 1:
        return True
    target = (selected[1] if selected is not None and selected[0] <= CLOSE_RANGE
              and root.component.now >= world.sniper_retry_at else None)
    if target != world.sniper_target:
        world.sniper_target = target
        gate = _active(root, 56)
        if gate is not None:
            gate.dependency_changed()
    return False


def native(world, cfg, instance):
    if (world.hero != HERO or world.match is None or instance is None
            or instance.graph.index != GRAPH or instance.component.strict):
        return NotImplemented
    kind = cfg.get("$")
    if kind in ("STU_F7262B36", "STU_A37A910D"):
        controller, target = instance.graph.node(56).field("m_condition")["m_configVars"]
        # These two operands stand in for the unavailable encounter controller.
        if cfg is controller:
            return expr.Map(((13650, world.sniper_target is not None),))
        if cfg is target:
            return expr.Entity(world.sniper_target or 0)
    owner = instance.component.entity
    if kind == "STU_71A87E92" and expr.to_entity(instance.evaluate(cfg.get("m_entity"))) == owner:
        return world.match.script_body_position(owner, owner)
    return NotImplemented


def _destination(state, bot, target, collision, walls):
    origin = state.evaluate(state.node.field("m_6A443DF5"), watch=False)
    if not isinstance(origin, expr.Vec3):
        return None
    limit = expr.to_float(state.evaluate(state.node.field("m_EB691F64"), watch=False))
    spread = expr.to_float(state.evaluate(state.node.field("m_3D5CAF44"), watch=False))
    spacing = expr.to_float(state.evaluate(state.node.field("m_7B36ED50"), watch=False))
    speed = expr.to_float(state.evaluate(state.instance.graph.node(16).field("m_speed"), watch=False))
    if not (limit > 0 and spacing > 0 and speed > 0):
        return None
    heading = math.atan2(origin.x - target.feet[0], origin.z - target.feet[2])
    for step in range(1, int(limit / spacing) + 1):
        distance = step * spacing
        for offset in (0, -1, 1, -2, 2, -3, 3):
            angle = heading + math.radians(offset * spread / 3)
            x, z = origin.x + distance * math.sin(angle), origin.z + distance * math.cos(angle)
            floor = collision.ground(x, z, origin.y + limit, bot.position[1] - spacing)
            if floor is None or floor.surface & NOT_GROUND or floor.normal[1] < 0.7:
                continue
            point = (x, floor.y, z)
            vector = (x - origin.x, floor.y + 1 - origin.y, z - origin.z)
            reach = math.hypot(*vector)
            if reach > limit:
                continue
            direction = tuple(axis / reach for axis in vector)
            if collision.cast_ray(tuple(origin), direction, reach) is not None:
                continue
            mover = Mover(bot.position, round(bot.yaw), collision, mover_data(bot.body))
            seconds = math.dist(bot.position, point) / speed + state.instance.component.frame_ms / 1000
            outcome = mover.step_pose(Command(0, yaw=mover.yaw), seconds, 0,
                                      move_to=(point, speed), walls=walls)
            if outcome is True:
                return expr.Vec3(*point)
    return None


def interrupt(bot):
    root = _root(bot)
    if root is None or root.component.world.sniper_target is None:
        return
    component = root.component
    component.world.sniper_target = None
    with component.operation():
        motion = _active(root, 16)
        if motion is not None:
            motion.end(False)
        elif root.context().var(expr.INSTANCE, 59) == 1:
            root.follow(root.graph.node(64), "m_outPlug")
        search = _active(root, 39)
        if search is not None:
            search.end(False)
        gate = _active(root, 56)
        if gate is not None:
            gate.dependency_changed()
    bot.velocity = (0.0, 0.0, 0.0)


def advance(fight, bot):
    from ow174.game.combat import TICK

    root = _root(bot)
    if root is None:
        return
    motion = _active(root, 16)
    search = _active(root, 39)
    if motion is None and search is None and root.context().var(expr.INSTANCE, 59) != 1:
        return
    target = _target(fight, bot, root)
    collision = fight.match.collision_world
    if (target is None or collision is None or fight.is_asleep(bot) or fight.is_stunned(bot)
            or bot.entity in fight.sombra_controls.locks
            or fight.steel_traps.rooted(bot.entity) or fight.hooks.controlled(bot.entity)):
        interrupt(bot)
        return
    component = root.component
    walls = fight.ice_wall if fight.ice_wall.pieces else None
    with component.operation():
        if search is not None:
            destination = _destination(search, bot, target, collision, walls)
            component.world.sniper_retry_at = component.now + RETRY_MS
            if destination is None:
                interrupt(bot)
                return
            for output in ("m_942D2129", "m_2FFBD269"):
                root.write(expr.lvalue(search.node.field(output)), destination)
            search.end(True)
            return
        if motion is None:
            return
        movement = motion.move_request()
        if movement is None:
            interrupt(bot)
            return
        destination, speed = movement
        start = bot.position
        mover = Mover(start, round(bot.yaw), collision, mover_data(bot.body))
        outcome = mover.step_pose(Command(fight.tick, yaw=mover.yaw), TICK, fight.tick,
                                  move_to=(tuple(destination), speed), walls=walls)
        bot.position = tuple(mover.position)
        bot.velocity = tuple((axis - old) / TICK for axis, old in zip(bot.position, start, strict=True))
        bot.moved_tick = fight.tick
        if outcome is not None:
            motion.end(outcome)
            bot.velocity = (0.0, 0.0, 0.0)
