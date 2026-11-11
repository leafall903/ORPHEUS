"""Host the authored Sniper 142C reposition graph on static collision."""

import math

from ow174.game import bots, sombra_hack
from ow174.game.commands import Command
from ow174.game.collision import MOVER, NOT_GROUND, TEAM0, TEAM1
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime

BODY = 0x0400000000001E7D
GRAPH = 0x142C
MOVE_NODE = 16
SEARCH_NODE = 39
GATE_NODE = 56


def instance_for(bot):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def state_at(instance, position, *, create=False):
    node = instance.graph.node(position)
    return instance.node_state(node) if create else instance.states.get(node.state)


def move_state(bot, *, create=False):
    instance = instance_for(bot)
    return None if instance is None else state_at(instance, MOVE_NODE, create=create)


def phase(instance):
    return instance.context().var(expr.INSTANCE, 59)


def busy(bot):
    return getattr(bot, "sniper_target", None) is not None


def moving(bot):
    instance = instance_for(bot)
    return instance is not None and phase(instance) == 1 and busy(bot)


def blocked(fight, bot):
    return fight.is_incapacitated(bot) or sombra_hack.active(fight, bot.entity, fight.tick)


def hostile_target(fight, bot, entity):
    return next((target for target in fight._enemies(bot) if target.owner.entity == entity), None)


def close_target(fight, bot, entity):
    from ow174.game import combat

    target = hostile_target(fight, bot, entity)
    world = fight.match.collision_world
    if target is None or world is None:
        return None
    x, y, z = bot.position
    tx, ty, tz = target.feet
    if not (0.1 <= math.hypot(x - tx, z - tz) <= 3 and abs(y - ty) <= 0.75):
        return None
    a, b = target.segment()
    if combat.capsule_visible(world, (x, y + combat.EYE, z), a, b, target.radius, walls=fight.walls):
        return target
    return None


def start_gate(instance):
    root = state_at(instance, 17)
    if root is None or not root.active or phase(instance) in (1, 2):
        return
    gate = state_at(instance, GATE_NODE, create=True)
    if not gate.active:
        gate.request(runtime.BEGIN)
    elif not gate.current:
        gate.dependency_changed()


def request(match, bot, opponent):
    """Admit a close hostile target to the authored search and windup."""
    fight = match.combat
    if match.ended or bot not in match.bots or blocked(fight, bot) or busy(bot):
        return False
    instance = instance_for(bot)
    if instance is None or instance.component.strict or phase(instance) in (1, 2):
        return False
    root = state_at(instance, 17)
    if phase(instance) is not None and (root is None or not root.active):
        return False
    if close_target(fight, bot, opponent.entity) is None:
        return False
    category = MOVER
    if not match.game_map.free_for_all and bot.team in (0, 1):
        category = (TEAM0, TEAM1)[bot.team]
    if bot.ensure_manual_mover(match.tick, match.collision_world, category) is None:
        return False
    bot.body_script.advance_live(match.tick, bot.position)
    bot.sniper_target = opponent.entity
    start_gate(instance)
    instance.component.run_now()
    return True


def cancel(bot, *, tick=None):
    instance = instance_for(bot)
    if instance is None or not busy(bot):
        return
    bot.sniper_target = None
    motion = state_at(instance, MOVE_NODE)
    if motion is not None and motion.active:
        finish(bot, motion, False, tick)
        return
    gate = state_at(instance, GATE_NODE)
    if gate is not None:
        if gate.active:
            gate.end(False)
        else:
            instance.cancel(gate)
    if phase(instance) == 1:
        instance.follow(instance.graph.node(64), "m_outPlug")
    instance.component.run_now()


def search_owner(world, instance):
    return next((bot for bot in world.match.bots if bot.entity == instance.component.entity
                 and bot.body == BODY and not bot.dead and bot.body_script is not None
                 and bot.body_script.component is instance.component), None)


def native(world, cfg, instance):
    if (world.match is None or instance is None or instance.component.strict
            or instance.graph.index != GRAPH):
        return NotImplemented
    kind = cfg.get("$")
    if kind in ("STU_F7262B36", "STU_A37A910D"):
        controller, query = instance.graph.node(56).field("m_condition.m_configVars")
        if cfg is not controller and cfg is not query:
            return NotImplemented
        bot = search_owner(world, instance)
        target = (close_target(world.match.combat, bot, getattr(bot, "sniper_target", None))
                  if bot is not None else None)
        if cfg is controller:
            return expr.Map(((13650, target is not None),))
        return expr.Entity(target.entity) if target is not None else None
    if kind == "STU_71A87E92" and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity):
        owner = search_owner(world, instance)
        return expr.Vec3(*owner.position) if owner is not None else None
    if kind == "STU_B113D362" and any(cfg is instance.graph.node(pos).field("m_value") for pos in (40, 46)):
        from ow174.game import combat

        yaw = expr.to_float(instance.evaluate(cfg["m_7C831A8D"]))
        pitch = expr.to_float(instance.evaluate(cfg["m_C0EDD7EC"]))
        return expr.Vec3(*combat.aim(round(yaw * 65536 / 360), round(pitch * 65536 / 360)))
    return NotImplemented


def search(world, state):
    if (world.match is None or state.instance.graph.index != GRAPH
            or state.instance.component.strict or state.node.pos != SEARCH_NODE):
        return
    # The authored 41/46 cycle changes this input every 250 ms when no point qualifies.
    direction = state.evaluate(state.node.field("m_E165100C"))
    fight = world.match.combat
    bot = search_owner(world, state.instance)
    if bot is None:
        return
    target = close_target(fight, bot, getattr(bot, "sniper_target", None))
    origin = state.evaluate(state.node.field("m_6A443DF5"), watch=False)
    if target is None or blocked(fight, bot) or not isinstance(origin, expr.Vec3):
        state.request(runtime.ABORT)
        return
    destination = landing_point(state, fight, bot, target, origin, direction)
    if destination is None:
        return
    for output in ("m_942D2129", "m_2FFBD269"):
        state.instance.write(expr.lvalue(state.node.field(output)), expr.Vec3(*destination))
    state.request_finish()


def landing_point(state, fight, bot, target, origin, direction):
    """Sample the authored cone on static floor, preferring near outward landings."""
    from ow174.game import combat

    collision = fight.match.collision_world
    if not isinstance(direction, expr.Vec3) or math.hypot(direction.x, direction.z) == 0:
        return None
    heading = math.atan2(direction.x, direction.z)
    spacing = expr.to_float(state.evaluate(state.node.field("m_7B36ED50"), watch=False))
    reach = expr.to_float(state.evaluate(state.node.field("m_EB691F64"), watch=False))
    spread = math.radians(expr.to_float(state.evaluate(state.node.field("m_3D5CAF44"), watch=False)))
    speed = expr.to_float(state.evaluate(state.instance.graph.node(MOVE_NODE).field("m_speed"), watch=False))
    if (not all(math.isfinite(value) for value in (spacing, reach, spread, speed))
            or spacing <= 0 or reach < spacing or speed <= 0):
        return None
    y = bot.position[1]
    dx, dz = origin.x - target.feet[0], origin.z - target.feet[2]
    rays = [(math.sin(heading + offset * spread / 3), math.cos(heading + offset * spread / 3))
            for offset in (0, 1, -1, 2, -2, 3, -3)]
    outward = [(sx, sz) for sx, sz in rays if sx * dx + sz * dz > 0]
    data = mover_data(bot.body)
    slope = math.cos(math.radians(data.max_slope))
    a, b = target.segment()
    # An obstructed path cannot reach a farther point on the same ray.
    for step in range(1, int(reach / spacing) + 1):
        distance = step * spacing
        remaining = []
        for sx, sz in outward:
            nx, nz = origin.x + sx * distance, origin.z + sz * distance
            floor = collision.ground(nx, nz, y + 0.5, y - 0.5)
            if floor is None or floor.surface & NOT_GROUND or floor.normal[1] < slope:
                continue
            destination = (nx, floor.y, nz)
            if not reachable(fight, bot, destination, data, speed):
                continue
            if combat.capsule_visible(collision, (nx, floor.y + combat.EYE, nz),
                                      a, b, target.radius, walls=fight.walls):
                return destination
            remaining.append((sx, sz))
        outward = remaining
        if not outward:
            break
    return None


def reachable(fight, bot, destination, data, speed):
    """Use the same capsule solver and tick interval as the ensuing MoveTo."""
    from ow174.game import combat

    yaw = round(bot.yaw)
    probe = Mover(bot.position, yaw, fight.match.collision_world, data, category=bot.impulse_mover.category)
    frames = math.ceil(math.dist(bot.position, destination) / (speed * combat.TICK)) + 2
    for tick in range(frames):
        before = tuple(probe.position)
        ground = fight.walls.ground(probe) if fight.walls.pieces else None
        result = probe.step_pose(Command(tick, yaw=yaw), combat.TICK, tick,
                                 move_to=(destination, speed), ground_override=ground)
        if probe.airborne or result is False or fight.walls.move_mover(probe, before):
            return False
        if result is True:
            return True
    return False


def finish(bot, state, arrived, tick=None):
    bot.sniper_target = None
    state.end(arrived)
    state.instance.component.run_now()
    if tick is not None:
        # Match movement can complete before the body VM catches up to this pose.
        state.ended_at = max(state.ended_at, tick * state.instance.component.frame_ms)
    bot.velocity = (0.0, 0.0, 0.0)
    if bot.impulse_mover is not None:
        bot.impulse_mover.velocity = bot.velocity


def step(fight, bot, frame, seconds):
    if not busy(bot):
        return False
    instance = instance_for(bot)
    if instance is None:
        return False
    if hostile_target(fight, bot, bot.sniper_target) is None or blocked(fight, bot):
        cancel(bot, tick=frame)
        return False
    state = state_at(instance, MOVE_NODE)
    if state is None or not state.active:
        if phase(instance) != 1:
            if close_target(fight, bot, bot.sniper_target) is None:
                cancel(bot, tick=frame)
            else:
                start_gate(instance)
        return False
    movement = state.move_request()
    mover = bot.impulse_mover
    if (movement is None or mover is None or mover.world is not fight.match.collision_world
            or blocked(fight, bot)):
        finish(bot, state, False)
        return False
    destination, speed = movement
    first = frame if bot.last_tick is None else max(frame - bots.CATCH_UP + 1, bot.last_tick + 1)
    for tick in range(first, frame + 1):
        outcome = move_pose(fight, bot, mover, destination, speed, tick, seconds)
        if outcome is not None:
            finish(bot, state, outcome, tick)
            break
    return True


def move_pose(fight, bot, mover, destination, speed, tick, seconds):
    x, _, z = bot.position
    yaw = bots.turned(bot.yaw, bots.heading(destination.x - x, destination.z - z),
                      bots.TURN_RATE * seconds)
    command = Command(tick, yaw=round(yaw), pitch=round(bot.pitch))
    before = tuple(mover.position)
    ground = fight.walls.ground(mover) if fight.walls.pieces else None
    # Give an admitted combat impulse the normal physics sweep before resuming MoveTo.
    move_to = None if mover.external_impulse else (tuple(destination), speed)
    outcome = mover.step_pose(command, seconds, tick, move_to=move_to, ground_override=ground)
    if fight.walls.move_mover(mover, before):
        outcome = False
    mover.last_command = command
    bot.position, bot.velocity, bot.yaw = tuple(mover.position), mover.velocity, float(yaw)
    bot.held = (0, 0)
    bot.last_tick = bot.moved_tick = tick
    return outcome
