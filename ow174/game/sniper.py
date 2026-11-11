"""Manual Sniper retreat through the authored fixed-target MoveTo state."""

import math

from ow174.game import bots, combat
from ow174.game.collision import NOT_GROUND
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, nodes, runtime

SNIPER = 0x02E00000000001B8
SNIPER_REPOSITION_GRAPH = 0x142C
SNIPER_REPOSITION_NODE = 16


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
        if floor is None or floor.surface & NOT_GROUND or floor.normal[1] < slope:
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
    component = bot.body_script.component
    if component.strict:
        return None
    for instance in component.instances.values():
        if instance.graph.index == SNIPER_REPOSITION_GRAPH:
            node = instance.graph.node(SNIPER_REPOSITION_NODE)
            if node is not None and node.cls == "STUStatescriptStateMoveTo":
                return instance.node_state(node)
    return None


def request_reposition(state, tick, operation):
    component = state.instance.component
    delay = max(0, tick * component.frame_ms - component.now)
    state.request(operation, delay)


def close_target(fight, bot):
    collision = fight.match.collision_world
    if collision is None or bot is None or bot.dead or not bot.firing or fight.is_stunned(bot):
        return None
    x, y, z = bot.position
    for target in fight._enemies(bot):
        if (isinstance(target.owner, bots.Bot) or target.owner.entity != bot.attack_target
                or target.owner.gone):
            continue
        tx, ty, tz = target.feet
        if (0.1 <= math.hypot(x - tx, z - tz) <= 3 and abs(y - ty) <= 0.75
                and visible(collision, bot.position, target)):
            return target
    return None


def search_owner(world, instance):
    return next((bot for bot in world.match.bots if bot.entity == instance.component.entity
                 and bot.spawner.get("manual_spawn") is True and bot.body_script is not None
                 and bot.body_script.component is instance.component), None)


def native(world, cfg, instance):
    if (world.hero != SNIPER or world.match is None or instance is None or instance.component.strict
            or instance.graph.index != SNIPER_REPOSITION_GRAPH):
        return NotImplemented
    kind = cfg.get("$")
    if kind in ("STU_F7262B36", "STU_A37A910D"):
        controller, query = instance.graph.node(56).field("m_condition.m_configVars")
        if cfg is not controller and cfg is not query:
            return NotImplemented
        phase = instance.states.get(instance.graph.node(17).state)
        target = (close_target(world.match.combat, search_owner(world, instance))
                  if phase is not None and phase.active else None)
        if cfg is controller:
            return expr.Map(((13650, target is not None),))
        return expr.Entity(target.entity) if target is not None else None
    if kind == "STU_71A87E92" and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity):
        owner = search_owner(world, instance)
        return expr.Vec3(*owner.position) if owner is not None else None
    if kind == "STU_B113D362" and any(cfg is instance.graph.node(pos).field("m_value") for pos in (40, 46)):
        yaw = expr.to_float(instance.evaluate(cfg["m_7C831A8D"]))
        pitch = expr.to_float(instance.evaluate(cfg["m_C0EDD7EC"]))
        return expr.Vec3(*combat.aim(round(yaw * 65536 / 360), round(pitch * 65536 / 360)))
    return NotImplemented


def search(world, state):
    if (world.hero != SNIPER or world.match is None or state.instance.graph.index != SNIPER_REPOSITION_GRAPH
            or state.instance.component.strict or state.node.pos != 39):
        return
    # The authored 41/46 cycle changes this input every 250 ms when no point qualifies.
    direction = state.evaluate(state.node.field("m_E165100C"))
    fight = world.match.combat
    bot = search_owner(world, state.instance)
    target = close_target(fight, bot)
    origin = state.evaluate(state.node.field("m_6A443DF5"), watch=False)
    if target is None or not isinstance(origin, expr.Vec3):
        state.request(runtime.ABORT)
        return
    destination = landing_point(state, fight.match.collision_world, bot, target, origin, direction)
    if destination is None:
        return
    for output in ("m_942D2129", "m_2FFBD269"):
        state.instance.write(expr.lvalue(state.node.field(output)), expr.Vec3(*destination))
    state.request_finish()


def landing_point(state, collision, bot, target, origin, direction):
    """Sample the authored cone on static floor, preferring near outward landings."""
    if not isinstance(direction, expr.Vec3) or math.hypot(direction.x, direction.z) == 0:
        return None
    heading = math.atan2(direction.x, direction.z)
    spacing = expr.to_float(state.evaluate(state.node.field("m_7B36ED50"), watch=False))
    reach = expr.to_float(state.evaluate(state.node.field("m_EB691F64"), watch=False))
    spread = math.radians(expr.to_float(state.evaluate(state.node.field("m_3D5CAF44"), watch=False)))
    speed = expr.to_float(state.evaluate(state.instance.graph.node(SNIPER_REPOSITION_NODE).field("m_speed"), watch=False))
    if (not all(math.isfinite(value) for value in (spacing, reach, spread, speed))
            or spacing <= 0 or reach < spacing or speed <= 0):
        return None
    y = bot.position[1]
    dx, dz = origin.x - target.feet[0], origin.z - target.feet[2]
    rays = [(math.sin(heading + offset * spread / 3), math.cos(heading + offset * spread / 3))
            for offset in (0, 1, -1, 2, -2, 3, -3)]
    outward = [(sx, sz) for sx, sz in rays if sx * dx + sz * dz > 0]
    data = mover_data(bot.body)
    # An obstructed path cannot reach a farther point on the same ray.
    for step in range(1, int(reach / spacing) + 1):
        distance = step * spacing
        remaining = []
        for sx, sz in outward:
            destination = edge(collision, bot.position, origin.x + sx * distance, origin.z + sz * distance, data)
            if destination is None:
                continue
            if (abs(destination[1] - y) <= 0.5 and visible(collision, destination, target)
                    and reachable(collision, bot, destination, data, speed)):
                return destination
            remaining.append((sx, sz))
        outward = remaining
        if not outward:
            break
    return None


def reachable(collision, bot, destination, data, speed):
    """Use the same capsule solver and tick interval as the ensuing MoveTo."""
    yaw = round(bot.yaw)
    probe = Mover(bot.position, yaw, collision, data)
    frames = math.ceil(math.dist(bot.position, destination) / (speed * combat.TICK)) + 2
    for tick in range(frames):
        result = probe.step_pose(Command(tick, yaw=yaw), combat.TICK, tick, move_to=(destination, speed))
        if probe.airborne or result is False:
            return False
        if result is True:
            return True
    return False


def start_reposition(state, fight, bot):
    instance = state.instance
    gate = instance.node_state(instance.graph.node(56))
    if gate.active:
        admitted = expr.truthy(gate.evaluate(gate.node.field("m_condition"), watch=False))
        if gate.current != admitted:
            gate.dependency_changed()
        # Ordinary fire stays available while a blocked search waits to retry.
        return admitted and not gate.current
    if close_target(fight, bot) is None:
        return False
    phase = instance.states.get(instance.graph.node(17).state)
    if phase is None or not phase.active:
        return expr.truthy(instance.context().var(expr.INSTANCE, 9466))
    request_reposition(gate, fight.tick, runtime.BEGIN)
    return expr.truthy(gate.evaluate(gate.node.field("m_condition"), watch=False))


def stop(bot):
    state = reposition_state(bot)
    if state is None:
        return
    instance = state.instance
    gate = instance.states.get(instance.graph.node(56).state)
    if gate is not None and gate.active:
        gate.end(False)
    if state.active:
        request_reposition(state, state.instance.component.frame, runtime.ABORT)
        if bot.effect_mover is None:
            bot.velocity = (0.0, 0.0, 0.0)
    elif retreat_phase(state) == 1:
        instance.follow(instance.graph.node(64), "m_outPlug")


def retreat_phase(state):
    return state.instance.context().var(expr.INSTANCE, 59)


def update(fight, bot, enabled):
    """Drive the authored search and movement for the manual engagement."""
    state = reposition_state(bot)
    if not isinstance(state, nodes.MoveTo):
        return False
    if not enabled or bot.effect_mover is not None:
        stop(bot)
        return False
    world = fight.match.collision_world
    if not state.active:
        if retreat_phase(state) in (1, 2):
            return True
        return start_reposition(state, fight, bot)
    movement = state.move_request()
    if movement is None:
        request_reposition(state, fight.tick, runtime.ABORT)
        return True
    destination, speed = movement
    data = mover_data(bot.body)
    x, _, z = bot.position
    distance = math.hypot(destination.x - x, destination.z - z)
    if distance:
        share = min(1, speed * combat.TICK / distance)
        nx = x + (destination.x - x) * share
        nz = z + (destination.z - z) * share
        if edge(world, bot.position, nx, nz, data) is None:
            stop(bot)
            return True
    mover = Mover(bot.position, round(bot.yaw), world, data)
    command = Command(fight.tick, yaw=round(bot.yaw), pitch=round(bot.pitch))
    outcome = mover.step_pose(command, combat.TICK, fight.tick, move_to=(tuple(destination), speed))
    bot.position = tuple(mover.position)
    bot.velocity = mover.velocity
    bot.moved_tick = fight.tick
    if outcome is not None or mover.airborne:
        operation = runtime.FINISH if outcome and not mover.airborne else runtime.ABORT
        request_reposition(state, fight.tick, operation)
        if mover.airborne:
            bot.effect_mover = mover
            bot.last_tick = fight.tick
        else:
            bot.velocity = (0.0, 0.0, 0.0)
    return True
