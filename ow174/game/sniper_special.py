"""Static movement host for a resolved Sniper 142C destination."""

import math

from ow174.game.collision import NOT_GROUND
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime

HERO = 0x02E00000000001B8
GRAPH = 0x142C


def _engaged_enemy(fight, bot):
    from ow174.game.npc_control import target_player

    for item in fight._enemies(bot):
        player = target_player(fight, item)
        if player is not None and player.entity == bot.attack_target:
            return item
    return None


def native(world, cfg, instance):
    if world.hero != HERO or instance is None or instance.graph.index != GRAPH or world.combat is None:
        return NotImplemented
    kind = cfg.get("$")
    if kind == "STU_B113D362" and any(cfg is instance.graph.node(pos).field("m_value") for pos in (40, 46)):
        from ow174.game.combat import aim

        yaw = expr.to_float(instance.evaluate(cfg["m_7C831A8D"]))
        pitch = expr.to_float(instance.evaluate(cfg["m_C0EDD7EC"]))
        return expr.Vec3(*aim(round(yaw * 65536 / 360), round(pitch * 65536 / 360)))
    if kind not in ("STU_F7262B36", "STU_A37A910D"):
        return NotImplemented
    condition = instance.graph.node(56).field("m_condition")
    controller, target_query = condition["m_configVars"]
    if cfg is not controller and cfg is not target_query:
        return NotImplemented
    fight = world.combat
    bot = next((item for item in fight.match.bots if item.entity == instance.component.entity), None)
    if bot is None or bot.dead or not bot.spawner.get("manual_spawn"):
        return None
    target = _engaged_enemy(fight, bot)
    if cfg is controller:
        return expr.Map(((13650, target is not None),))
    return expr.Entity(target.entity) if target is not None else None


def search(world, state):
    """Supply a clear static landing point to the authored search outputs."""
    if (world.hero != HERO or state.instance.graph.index != GRAPH or state.node.pos != 39
            or world.combat is None):
        return
    fight = world.combat
    collision = fight.match.collision_world
    entity = state.instance.component.entity
    bot = next((item for item in fight.match.bots if item.entity == entity and not item.dead), None)
    if (bot is None or not bot.spawner.get("manual_spawn") or collision is None
            or entity in fight.hacks or fight.is_stunned(bot)):
        state.request(runtime.ABORT)
        return
    root = state.instance
    parent = root.states.get(root.graph.node(38).state)
    if parent is not None and parent.active:
        gate = root.graph.node(56).field("m_condition")
        if not expr.truthy(state.evaluate(gate, watch=False)):
            state.request(runtime.ABORT)
            return
    destination = _destination(state, bot, collision)
    if destination is None:
        direction = state.evaluate(state.node.field("m_E165100C"))
        if not isinstance(direction, expr.Vec3):
            state.request(runtime.ABORT)
        return
    for output in ("m_942D2129", "m_2FFBD269"):
        state.instance.write(expr.lvalue(state.node.field(output)), destination)
    state.request_finish()


def _destination(state, bot, collision):
    origin = state.evaluate(state.node.field("m_6A443DF5"), watch=False)
    if not isinstance(origin, expr.Vec3):
        return None
    limit = expr.to_float(state.evaluate(state.node.field("m_EB691F64"), watch=False))
    spread = expr.to_float(state.evaluate(state.node.field("m_3D5CAF44"), watch=False))
    spacing = expr.to_float(state.evaluate(state.node.field("m_7B36ED50"), watch=False))
    speed = expr.to_float(state.evaluate(state.instance.graph.node(16).field("m_speed"), watch=False))
    if limit <= 0 or spacing <= 0 or speed <= 0:
        return None
    direction = state.evaluate(state.node.field("m_E165100C"), watch=False)
    if isinstance(direction, expr.Vec3) and math.hypot(direction.x, direction.z) > 0:
        heading = math.atan2(direction.x, direction.z)
    else:
        heading = bot.yaw * math.tau / 65536
    candidates = []
    for ray in range(-3, 4):
        angle = heading + math.radians(ray * spread / 3)
        dx, dz = math.sin(angle), math.cos(angle)
        for step in range(1, int(limit / spacing) + 1):
            distance = step * spacing
            x, z = origin.x + distance * dx, origin.z + distance * dz
            floor = collision.ground(x, z, origin.y + limit, bot.position[1] - spacing)
            if floor is None or floor.surface & NOT_GROUND or floor.normal[1] < 0.7:
                continue
            y = floor.y
            vector = (x - origin.x, y + 1 - origin.y, z - origin.z)
            reach = math.hypot(*vector)
            if reach > limit:
                continue
            if collision.cast_ray(tuple(origin), tuple(axis / reach for axis in vector), reach) is None:
                candidates.append((-y, distance, abs(ray), ray, x, y, z))
    for candidate in sorted(candidates):
        point = candidate[-3:]
        probe = Mover(bot.position, round(bot.yaw), collision, mover_data(bot.body))
        seconds = math.dist(bot.position, point) / speed + state.instance.component.frame_ms / 1000
        if probe.step_pose(Command(0, yaw=probe.yaw), seconds, 0, move_to=(point, speed)) is True:
            return expr.Vec3(*point)
    return None


def _root(bot):
    if bot.hero != HERO or bot.body_script is None or not bot.spawner.get("manual_spawn"):
        return None
    return next((instance for instance in bot.body_script.component.instances.values()
                 if instance.graph.index == GRAPH), None)


def replay_pending(bot):
    from ow174.game.npc_control import engaged

    root = _root(bot)
    if bot.dead or root is None:
        return False
    search = root.states.get(root.graph.node(39).state)
    # Engagement can start an authored search on the first omitted world frame.
    return (engaged(bot) or root.context().var(runtime.INSTANCE, 59) == 1
            or (search is not None and search.active))


def _cancel(root):
    state = root.states.get(root.graph.node(16).state)
    if state is not None and state.active:
        state.end(False)  # keep the phase switch alive for the authored landing recovery
    else:
        root.follow(root.graph.node(64), "m_outPlug")  # 09F4 resets a pending windup


def engagement_changed(bot):
    root = _root(bot)
    if root is None:
        return
    gate = root.states.get(root.graph.node(56).state)
    if gate is not None and gate.active:
        gate.dependency_changed()


def interrupt(bot):
    root = _root(bot)
    if root is None:
        return
    search_state = root.states.get(root.graph.node(39).state)
    searching = search_state is not None and search_state.active
    if searching:
        search_state.end(False)
    if searching or root.context().var(runtime.INSTANCE, 59) == 1:
        _cancel(root)


def drive(bot, tick, seconds, collision, paused_until):
    root = _root(bot)
    if root is None or root.context().var(runtime.INSTANCE, 59) != 1:
        return False
    fight = root.component.world.combat
    unavailable = fight is not None and (
        bot.entity in fight.hacks or (bot.attack_target is not None and _engaged_enemy(fight, bot) is None)
    )
    if tick < paused_until or unavailable:
        _cancel(root)
        bot._pause(tick)
        return True
    state = root.states.get(root.graph.node(16).state)
    movement = state.move_request() if state is not None else None
    if movement is None or collision is None:
        return False
    if bot.last_tick is not None and tick <= bot.last_tick:
        return True
    bot.last_tick = tick
    destination, speed = movement
    start = bot.position
    mover = Mover(start, round(bot.yaw), collision, mover_data(bot.body))
    outcome = mover.step_pose(Command(tick, yaw=mover.yaw), seconds, tick,
                              move_to=(tuple(destination), speed))
    bot.position = tuple(mover.position)
    bot.velocity = tuple((axis - old) / seconds for axis, old in zip(bot.position, start, strict=True))
    bot.moved_tick = tick
    if outcome is not None:
        delay = max(0, tick * root.component.frame_ms - root.component.now)
        state.request(runtime.FINISH if outcome else runtime.ABORT, delay)
    return True
