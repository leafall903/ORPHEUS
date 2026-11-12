"""Manual 142C Sniper retreat through the authored wait and MoveTo."""

import math

from ow174.game import bots, combat
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime

SNIPER = 0x02E00000000001B8
GRAPH = 0x142C
COOLDOWN_MS = 6000  # Static destination-selection policy.

def visible(world, position, target):
    a, b = target.segment()
    return combat.capsule_visible(world, (position[0], position[1] + combat.EYE, position[2]),
                                  a, b, target.radius)


def edge(world, start, x, z, data):
    """Require supported walkable floor, headroom and capsule-width static rays."""
    previous = start
    distance = math.hypot(x - start[0], z - start[2])
    count = max(1, math.ceil(distance / 0.25))
    slope = math.cos(math.radians(data.max_slope))
    for index in range(1, count + 1):
        fraction = index / count
        nx, nz = start[0] + (x - start[0]) * fraction, start[2] + (z - start[2]) * fraction
        floor = world.ground(nx, nz, previous[1] + bots.STEP_UP + 0.05,
                             previous[1] - data.step_down - 0.05)
        if floor is None or floor.normal[1] < slope:
            return None
        point = nx, floor.y, nz
        radius = data.stand_radius
        top = data.stand_height - radius
        if top > radius and combat.ray_world(world, (nx, floor.y + radius, nz),
                                             (0, 1, 0), top - radius) is not None:
            return None
        delta = (point[0] - previous[0], 0, point[2] - previous[2])
        length = math.hypot(delta[0], delta[2])
        if length:
            direction = (delta[0] / length, 0, delta[2] / length)
            side = (-direction[2] * radius, direction[0] * radius)
            for lateral in (-1, 0, 1):
                for height in (radius + 0.01, top):
                    origin = (previous[0] + side[0] * lateral,
                              max(previous[1], point[1]) + height, previous[2] + side[1] * lateral)
                    if combat.ray_world(world, origin, direction, length) is not None:
                        return None
        previous = point
    return previous


def reposition_state(bot):
    if bot.hero != SNIPER or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(16)
            if node is not None and node.cls == "STUStatescriptStateMoveTo":
                return instance.node_state(node)
    return None


def request_reposition(state, tick, operation):
    component = state.instance.component
    delay = max(0, tick * component.frame_ms - component.now)
    state.request(operation, delay)


def reposition_wait(state):
    instance = state.instance
    node = instance.graph.node(94)
    return instance.node_state(node)


def repositioning(bot):
    state = reposition_state(bot)
    if state is None:
        return False
    recovery = state.instance.states.get(24)  # 142C's post-move weapon arbitration stack
    return state.active or reposition_wait(state).active or (recovery is not None and recovery.active)


def start_reposition(state, world, bot, target, tick):
    """Sandbox fallback for the unresolved authored destination-selection state."""
    wait = reposition_wait(state)
    cooling = state.ended_at and state.now - state.ended_at < COOLDOWN_MS
    if wait.active or state.active or cooling:
        return False
    x, y, z = bot.position
    dx, dz = x - target.feet[0], z - target.feet[2]
    distance = math.hypot(dx, dz)
    if not 0.1 <= distance <= 3 or abs(y - target.feet[1]) > 0.75 or not visible(world, bot.position, target):
        return False
    data = mover_data(bot.body)
    for angle in (0, math.pi / 4, -math.pi / 4):
        cos, sin = math.cos(angle), math.sin(angle)
        nx = x + (dx * cos - dz * sin) * 4 / distance
        nz = z + (dz * cos + dx * sin) * 4 / distance
        destination = edge(world, bot.position, nx, nz, data)
        if destination is None or abs(destination[1] - y) > 0.5 or not visible(world, destination, target):
            continue
        state.instance.write((expr.INSTANCE, 1220), expr.Vec3(*destination))
        request_reposition(wait, tick, runtime.BEGIN)
        return True
    return False



def begin(fight, bot, player):
    state = reposition_state(bot)
    target = next((t for t in fight._enemies(bot) if t.owner is player), None)
    if (state is None or target is None
            or not start_reposition(state, fight._collision(), bot, target, fight.tick)):
        raise ValueError("NPC reposition needs a nearby target, clear destination and ready state")
    state.instance.component.run_now()


def stop(bot, tick):
    state = reposition_state(bot)
    if state is None:
        return
    wait = reposition_wait(state)
    if state.active:
        bot.velocity = (0.0, 0.0, 0.0)
    for entry in (wait, state):
        if entry.active:
            request_reposition(entry, tick, runtime.ABORT)
    state.instance.drop_begins([wait, state])


def advance(fight, bot):
    if bot.attack_mode != "reposition":
        return
    state = reposition_state(bot)
    movement = state.move_request() if state is not None else None
    if movement is None:
        return
    destination, speed = movement
    start = tuple(bot.position)
    mover = Mover(start, round(bot.yaw), fight._collision(), mover_data(bot.body))
    outcome = mover._move_to(combat.TICK, tuple(destination), speed)
    bot.position = tuple(mover.position)
    bot.velocity = tuple((b - a) / combat.TICK for a, b in zip(start, bot.position, strict=True))
    bot.moved_tick = fight.tick
    if outcome is not None:
        request_reposition(state, fight.tick, runtime.FINISH if outcome else runtime.ABORT)
        bot.velocity = (0.0, 0.0, 0.0)
