"""Drive the Sniper's authored 142C MoveTo in manual engagements."""

import math

from ow174.game import bots, combat, npc_motion
from ow174.game.mover import mover_data
from ow174.game.script import expr, nodes, runtime

SNIPER = 0x02E00000000001B8
GRAPH, MOVE, DESTINATION = 0x142C, 16, 1220
COOLDOWN_MS = 6000  # Sandbox policy for the unresolved native destination service.


def movement_state(bot, *, create=False):
    if bot.hero != SNIPER or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(MOVE)
            if node is not None and node.cls == 'STUStatescriptStateMoveTo':
                return instance.node_state(node) if create else instance.states.get(node.state)
    return None


def stop(bot):
    state = movement_state(bot)
    if state is None:
        return
    state.instance.drop_begins([state])
    if state.active:
        state.end(False)
        _stop_motion(bot)


def _stop_motion(bot):
    if not bot.airborne:
        bot.velocity = (0.0, 0.0, 0.0)
        bot.held = (0, 0)


def _visible(world, start, end):
    delta = tuple(b - a for a, b in zip(start, end, strict=True))
    distance = math.hypot(*delta)
    if distance <= combat.EPSILON:
        return True
    direction = tuple(axis / distance for axis in delta)
    return combat.ray_world(world, start, direction, distance) is None


def _destination(fight, world, bot, target, data):
    x, y, z = bot.position
    tx, ty, tz = target.mover.position
    dx, dz = x - tx, z - tz
    distance = math.hypot(dx, dz)
    if not 0.1 <= distance <= 3 or abs(y - ty) > 0.75:
        return None
    eye = fight._eye(target)
    for angle in (0, math.pi / 4, -math.pi / 4):
        cos, sin = math.cos(angle), math.sin(angle)
        nx = x + (dx * cos - dz * sin) * 4 / distance
        nz = z + (dz * cos + dx * sin) * 4 / distance
        floor = npc_motion.supported_lane(world, data, bot.position, nx, nz)
        if floor is None or abs(floor - y) > 0.5:
            continue
        destination = (nx, floor, nz)
        candidate_eye = (nx, floor + combat.EYE, nz)
        if (_visible(world, (x, y + combat.EYE, z), candidate_eye)
                and _visible(world, candidate_eye, eye)
                and npc_motion.teammate_clear(fight.match, bot, destination, data)):
            return destination
    return None


def _request(state, tick, operation):
    component = state.instance.component
    state.request(operation, max(0, tick * component.frame_ms - component.now))


def update(fight, bot, target, enabled):
    if bot.hero != SNIPER:
        return False
    world = fight._collision()
    if not enabled or bot.airborne or world is None:
        stop(bot)
        return False
    state = movement_state(bot, create=True)
    if not isinstance(state, nodes.MoveTo):
        return False
    data = mover_data(bot.body)
    if not state.active:
        if state.ended_at and state.now - state.ended_at < COOLDOWN_MS:
            return False
        destination = _destination(fight, world, bot, target, data)
        if destination is None:
            return False
        state.instance.write((expr.INSTANCE, DESTINATION), expr.Vec3(*destination))
        _request(state, fight.tick, runtime.BEGIN)
        return True

    movement = state.move_request()
    if movement is None:
        stop(bot)
        return False
    destination, speed = movement
    start = bot.position
    distance = math.dist(start, destination)
    fraction = min(1.0, speed * combat.TICK / distance) if distance > combat.EPSILON else 1.0
    step = tuple(a + (b - a) * fraction for a, b in zip(start, destination, strict=True))
    if (npc_motion.floor_at(world, data, step[0], start[1], step[2]) is None
            or not npc_motion.teammate_clear(fight.match, bot, step, data)):
        stop(bot)
        return True
    yaw = bots.heading(destination.x - start[0], destination.z - start[2])
    outcome = npc_motion.drive(fight, world, bot, data, tuple(destination), speed, yaw)
    # A fixed retreat ends when the capsule is deflected or slowed by a wall.
    # Reissuing the same request from partial contact can creep through it.
    if outcome is None and math.hypot(bot.position[0] - step[0], bot.position[2] - step[2]) > 0.025:
        outcome = False
    if outcome is None:
        bot.held = bot.last_held = (0, 127)
        bot.input_tick = fight.tick
    else:
        _request(state, fight.tick, runtime.FINISH if outcome else runtime.ABORT)
        _stop_motion(bot)
    return True
