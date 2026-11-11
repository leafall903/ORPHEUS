"""Manual collision-aware pursuit; grid routing is a server reconstruction."""

import heapq
import math
from dataclasses import dataclass

from ow174.game import assassin, bots, combat, sigma_flux
from ow174.game.commands import Command
from ow174.game.mover import IN_AIR, Mover, mover_data, statescript_mods
from ow174.game.script import expr, nodes, runtime

MELEE_REACH = 2.5
TROOPER_MELEE_REACH = 1.4  # stop inside 1422's authored 1.5 m position check
GRID, MAX_NODES, PLAN_INTERVAL, PLAN_SLICE = 1.0, 2048, 30, 4
RIALTO_MAP = 0x080000000000079F
# Server-authored connection through the southern opening in the upper railing.
RIALTO_LEDGE = (-139.081, 3.75, -103.052)
RIALTO_DEPARTURE = (-141.6, 3.75, -103.052)
RIALTO_LANDING = (-141.4, -2.25, -103.25)
HEAVY = 0x02E00000000001BA
TROOPER = 0x02E00000000001AC
CHARGE_LIMIT_MS = 3000  # reconstructed cap, from 1408's phase chase destination
SNIPER = 0x02E00000000001B8
SNIPER_REPOSITION_GRAPH = 0x142C
SNIPER_REPOSITION_NODE = 16
SNIPER_REPOSITION_WAIT = 94
SNIPER_REPOSITION_COOLDOWN_MS = 6000  # reconstructed sandbox policy


@dataclass(frozen=True)
class LedgeDrop:
    started: int | None = None
    released: bool = False


@dataclass
class Navigator:
    mover: Mover
    path: tuple = ()
    target: int | None = None
    goal: tuple | None = None
    next_plan: int = 0
    last_tick: int | None = None
    stuck: int = 0
    search: "Search | None" = None
    forced_landing: bool = False


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


@dataclass
class Search:
    """Serializable frontier; a budget counts heap pops, including stale entries."""
    start: tuple
    target: combat.Target
    queue: list
    points: dict
    parents: dict
    costs: dict
    visited: set
    done: bool = False
    path: tuple = ()


def begin(start, target, reach):
    # Keep a value snapshot, not the player's live controller or mutable pose.
    target = combat.Target(target.entity, target.team, tuple(target.feet), target.height,
                           target.radius, target.eye, None)
    start = tuple(start)
    origin = (0, 0, round(start[1] * 8))
    return Search(start, target, [(max(0, math.dist(start, target.feet) - reach), 0.0, origin)],
                  {origin: start}, {}, {origin: 0.0}, set())


def advance(search, world, data, reach, budget, *, rialto=False):
    """Resume a bounded floor search; retain the frontier between host updates."""
    def remaining(point):
        return max(0, math.dist(point, search.target.feet) - reach)

    start = search.start
    origin = (0, 0, round(start[1] * 8))
    for _ in range(budget):
        if search.done:
            break
        if not search.queue or len(search.visited) >= MAX_NODES:
            search.done = True
            break
        _, cost, current = heapq.heappop(search.queue)
        if current in search.visited:
            continue
        search.visited.add(current)
        point = search.points[current]
        ledge = (rialto and math.dist(search.target.feet, RIALTO_LANDING) < 20
                 and abs(search.target.feet[1] - RIALTO_LANDING[1]) < 1
                 and math.dist(point, RIALTO_LEDGE) <= 1.5
                 and edge(world, point, RIALTO_LEDGE[0], RIALTO_LEDGE[2], data) is not None)
        arrived = remaining(point) == 0 and visible(world, point, search.target)
        horizon = remaining(start) > 24 and cost >= 24 and remaining(point) < remaining(start) - GRID
        if arrived or horizon or ledge:
            route = []
            while current != origin:
                route.append(search.points[current])
                current = search.parents[current]
            search.path, search.done = tuple(reversed(route)), True
            if ledge and not arrived:
                search.path += (RIALTO_LEDGE, LedgeDrop())
            break
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = current[0] + dx, current[1] + dz
            if abs(nx) > 32 or abs(nz) > 32:
                continue
            neighbor = edge(world, point, start[0] + nx * GRID, start[2] + nz * GRID, data)
            if neighbor is None:
                continue
            address = (nx, nz, round(neighbor[1] * 8))
            new_cost = cost + math.dist(point, neighbor)
            if new_cost >= search.costs.get(address, math.inf):
                continue
            search.points[address], search.parents[address] = neighbor, current
            search.costs[address] = new_cost
            heapq.heappush(search.queue, (new_cost + remaining(neighbor), new_cost, address))
    return search.done


def plan(world, start, target, data, reach):
    """Synchronous offline helper; runtime pursuit uses incremental advance."""
    search = begin(start, target, reach)
    while not advance(search, world, data, reach, MAX_NODES):
        pass
    return search.path


def stop(bot):
    if bot.navigation is not None:
        bot.navigation.path = ()
        bot.navigation.goal = None
        bot.navigation.search = None
        bot.navigation.mover.velocity = (0.0, 0.0, 0.0)
    bot.pace = 0.0
    bot.velocity = (0.0, 0.0, 0.0)
    bot.held = (0, 0)
    bot.keys = False


def face_input(bot):
    """Keep locomotion bytes aligned when weapon aiming changes the body's yaw."""
    if bot.navigation is None or not bot.keys:
        return
    dx, _, dz = bot.velocity
    speed = math.hypot(dx, dz)
    if speed > 0.001:
        bot.held = bot.last_held = bots.throttles(bot.yaw, (dx / speed, dz / speed))


def reposition_state(bot):
    if bot.hero != SNIPER or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == SNIPER_REPOSITION_GRAPH:
            node = instance.graph.node(SNIPER_REPOSITION_NODE)
            if node is not None and node.cls == "STUStatescriptStateMoveTo":
                return instance.node_state(node)
    return None


def request_reposition(state, tick, operation):
    component = state.instance.component
    delay = max(0, tick * component.frame_ms - component.now)
    state.request(operation, delay)


def reposition_wait(state):
    instance = state.instance
    node = instance.graph.node(SNIPER_REPOSITION_WAIT)
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
    cooling = state.ended_at and state.now - state.ended_at < SNIPER_REPOSITION_COOLDOWN_MS
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


def melee_reach(bot):
    return TROOPER_MELEE_REACH if bot.hero == TROOPER else MELEE_REACH


def step(match, bot, tick, seconds):
    previous = bot.last_tick
    if previous is not None and tick <= previous:
        return
    bot.last_tick = tick
    first = tick if previous is None else max(previous + 1, tick - bots.CATCH_UP + 1)
    for at in range(first, tick + 1):
        _step(match, bot, at, seconds, PLAN_SLICE if at == tick else 0)


def charge_instance(bot):
    if bot.hero != HEAVY or bot.body_script is None:
        return None
    instances = bot.body_script.component.instances
    return next((i for i in instances.values() if i.graph.index == 0x1408), None)


def charge_motion(bot):
    instance = charge_instance(bot)
    state = instance.states.get(33) if instance is not None else None
    return state if state is not None and state.active and not state.ending else None


def charge_victim(state):
    slot = state.instance.vars.get(536)
    return expr.to_entity(slot.value()) if slot is not None else expr.INVALID_ENTITY


def charge_carries(match, body):
    return any(
        bot.attack_charge and bot.firing and not bot.dead
        and (motion := charge_motion(bot)) is not None and charge_victim(motion) == body
        for bot in match.bots
    )


def cancel_charge(bot):
    """Bridge the host's terminal event to 1408's phase reset and cooldown setters."""
    instance = charge_instance(bot)
    if instance is None:
        return False
    phase = instance.states.get(36)
    if phase is None or not phase.active:
        return False
    phase.end(False)
    motion = instance.state(33)
    if motion.active:
        motion.end(False)
    instance.drop_begins([motion])
    instance.states[41].stop()
    instance.run_node(instance.graph.node(6), None)  # reset #59
    instance.run_node(instance.graph.node(45), None)  # authored #472 -> #61
    return True


def settle_charge(bot):
    """Retire the contact phase when its authored cooldown has expired."""
    instance = charge_instance(bot)
    if instance is None:
        return
    phase = instance.vars.get(59)
    ability = instance.states.get(41)
    if phase is not None and phase.value() == 2 and ability is not None and ability.remaining() == 0:
        instance.run_node(instance.graph.node(6), None)


def _advance_charge(bot, state, world, tick, seconds):
    """Consume the authored vector with a server capsule sweep."""
    config = state.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
    vector = state.evaluate(config, watch=False)
    if not isinstance(vector, expr.Vec3):
        return False
    speed = math.hypot(vector.x, vector.z)
    if speed <= 0:
        return False
    nav = bot.navigation
    if nav is None or nav.mover.world is not world or math.dist(nav.mover.position, bot.position) > 0.1:
        nav = bot.navigation = Navigator(Mover(bot.position, round(bot.yaw), world, mover_data(bot.body)))
    nav.path, nav.goal, nav.search = (), None, None
    nav.last_tick = tick
    destination = tuple(a + b for a, b in zip(bot.position, vector, strict=True))
    return _drive(bot, nav, tick, seconds, destination, speed) is not False


def _carry_charge_target(bot, victim, before, tick, seconds, walls):
    mover = victim.owner.mover
    start = tuple(mover.position)
    delta = tuple(a - b for a, b in zip(bot.position, before, strict=True))
    destination = tuple(a + b for a, b in zip(mover.position, delta, strict=True))
    speed = math.hypot(delta[0], delta[2]) / seconds
    outcome = mover.step_pose(Command(tick, yaw=mover.yaw, pitch=mover.pitch), seconds, tick,
                              move_to=(destination, speed))
    # Carry motion runs before the combat pass removes expired pillars.
    blocked = walls.move_player(victim.owner, start, tick=tick)
    return outcome is not False and not blocked


def navigator(bot, world):
    nav = bot.navigation
    if nav is None or nav.mover.world is not world or math.dist(nav.mover.position, bot.position) > 0.1:
        nav = bot.navigation = Navigator(Mover(bot.position, round(bot.yaw), world, mover_data(bot.body)))
    return nav


def _drive_forced(match, bot, tick, seconds, destination, speed):
    nav = navigator(bot, match.collision_world)
    nav.path, nav.goal, nav.search, nav.last_tick = (), None, None, tick
    bot.pace = 0.0
    outcome = _drive(bot, nav, tick, seconds, destination, speed, walls=match.combat.walls)
    nav.forced_landing = bool(nav.mover.flags_word & IN_AIR)
    return outcome


def displace(match, bot, velocity, seconds, tick):
    """Sweep an external impulse without consuming another navigation pose."""
    nav = navigator(bot, match.collision_world)
    mover = nav.mover
    before = tuple(mover.position)
    clipped = mover.displace(velocity, seconds)
    walls = match.combat.walls
    after, normals = walls.constrain(before, tuple(mover.position), mover.data.stand_radius,
                                     mover.data.stand_height)
    if normals:
        walls.place_mover(mover, after, normals)
    bot.position = tuple(mover.position)
    bot.moved_tick = tick
    nav.forced_landing |= mover.airborne or mover.position[1] > before[1]
    return mover, clipped, normals


def _settle_forced_air(match, bot, nav, tick, seconds, pull=None):
    mover = nav.mover
    mover.use(world=match.collision_world)
    mover.velocity = (0.0, mover.velocity[1], 0.0)
    before = tuple(mover.position)
    mover.step_pose(Command(tick, yaw=round(bot.yaw)), seconds, tick)
    outcome = None
    if pull is not None:
        destination, speed = pull
        outcome = mover.move_toward_horizontal(destination, speed, seconds)
        if _constrain_wall(match.combat.walls, before, mover):
            outcome = False
    bot.position, bot.velocity = tuple(mover.position), mover.velocity
    bot.held, bot.keys, bot.moved_tick = (0, 0), False, tick
    nav.last_tick = tick
    nav.forced_landing = mover.airborne
    return outcome


def _step(match, bot, tick, seconds, search_budget):
    fight = match.combat
    nav = bot.navigation
    lifted = nav is not None and sigma_flux.receiving(bot)
    if lifted or (nav is not None and nav.mover.mods):
        nav.mover.track_mods(statescript_mods(bot.body_script.component) if lifted else {}, tick - 1)
    if (not match.ended and not bot.dead and match.collision_world is not None
            and not fight.is_stunned(bot, tick=tick) and bot.entity not in fight.pins
            and (halt := fight.halts.npc_move(bot.entity, tick)) is not None):
        state, (destination, speed) = halt
        pull = tuple(destination), speed
        if lifted and nav.forced_landing:
            outcome = _settle_forced_air(match, bot, nav, tick, seconds, pull)
        else:
            outcome = _drive_forced(match, bot, tick, seconds, *pull)
        if outcome is not None:
            request_reposition(state, tick, runtime.FINISH if outcome else runtime.ABORT)
        return
    if match.collision_world is not None and (pull := fight.gravitons.npc_move(bot, tick)) is not None:
        if bot.attack_charge:
            cancel_charge(bot)
            settle_charge(bot)
        if lifted and nav.forced_landing:
            _settle_forced_air(match, bot, nav, tick, seconds, pull)
        else:
            _drive_forced(match, bot, tick, seconds, *pull)
        return
    nav = bot.navigation
    if nav is not None and nav.forced_landing and not bot.dead and not match.ended:
        mover = nav.mover
        if match.collision_world is not None and math.dist(mover.position, bot.position) <= 0.1:
            # Ordinary pursuit plans on the ground. Settle the lifted body first.
            _settle_forced_air(match, bot, nav, tick, seconds)
            return
        nav.forced_landing = False
    target = next((t for t in match.combat._targets() if getattr(t.owner, 'entity', None) == bot.attack_target
                   and getattr(t.owner, 'client', None) is not None and not match.combat.vitals_of(t).dead
                   and not getattr(t.owner, 'gone', False)
                   and (match.game_map.free_for_all or t.team != bot.team)), None)
    world = match.collision_world
    reposition = None if bot.dead or match.ended else reposition_state(bot)
    movement = reposition.move_request() if isinstance(reposition, nodes.MoveTo) else None
    wait = reposition_wait(reposition) if reposition is not None else None
    if bot.attack_charge or (bot.autonomous and bot.spawner.get("autonomous_specials") is True):
        settle_charge(bot)
    charge = charge_motion(bot) if bot.attack_charge else None
    if (match.ended or bot.dead or not bot.firing or target is None or world is None
            or match.combat.is_stunned(bot, tick=tick)
            or bot.entity in match.combat.pins):
        if wait is not None and wait.active:
            request_reposition(wait, tick, runtime.ABORT)
        if movement is not None:
            request_reposition(reposition, tick, runtime.ABORT)
        if bot.attack_charge and cancel_charge(bot):
            bot.firing = False
        stop(bot)
        return
    if charge is not None:
        pinned = charge_victim(charge)
        victim = None
        if expr.valid_entity(pinned):
            victim = next(
                (t for t in match.combat._enemies(bot)
                 if t.entity == pinned and isinstance(getattr(t.owner, "mover", None), Mover)), None,
            )
            if victim is None:
                cancel_charge(bot)
                bot.firing = False
                stop(bot)
                return
        before = bot.position
        expired = tick * charge.instance.component.frame_ms - charge.start >= CHARGE_LIMIT_MS
        moving = not expired and _advance_charge(bot, charge, world, tick, seconds)
        if victim is not None and bot.position != before:
            moving = _carry_charge_target(bot, victim, before, tick, seconds, match.combat.walls) and moving
        if not moving:
            if victim is None or expired:
                cancel_charge(bot)
                bot.firing = False
            else:
                # Bridge the blocked sweep to the authored impact phase.
                charge.instance.run_node(charge.instance.graph.node(47), None)
            stop(bot)
        return
    if wait is not None and wait.active:
        stop(bot)
        return
    reach = melee_reach(bot) if bot.attack_melee or bot.hero == assassin.HERO else 8.0
    nav = bot.navigation
    if (nav is not None and nav.path and isinstance(nav.path[0], LedgeDrop)
            and nav.mover.world is world and math.dist(nav.mover.position, bot.position) <= 0.1):
        _drop_step(bot, nav, tick, seconds)
        return
    if (movement is None and reposition is not None and search_budget
            and match.game_map.map_guid == RIALTO_MAP
            and start_reposition(reposition, world, bot, target, tick)):
        stop(bot)
        return
    if (movement is None and math.dist(bot.position, target.feet) <= reach
            and visible(world, bot.position, target)):
        stop(bot)
        return
    if nav is None:
        nav = bot.navigation = Navigator(Mover(bot.position, round(bot.yaw), world, mover_data(bot.body)))
    if nav.last_tick is not None and tick <= nav.last_tick:
        return
    if nav.mover.world is not world or math.dist(nav.mover.position, bot.position) > 0.1:
        nav.mover = Mover(bot.position, round(bot.yaw), world, mover_data(bot.body))
        nav.path, nav.goal, nav.search = (), None, None
    nav.last_tick = tick
    if movement is not None:
        destination, speed = movement
        nav.path, nav.goal, nav.search = (), None, None
        outcome = _drive(bot, nav, tick, seconds, tuple(destination), speed, walls=match.combat.walls)
        if outcome is not None:
            request_reposition(reposition, tick, runtime.FINISH if outcome else runtime.ABORT)
        return
    # Finish a sampled goal before following a moving target's newer pose;
    # restarting every metre could starve a long search indefinitely.
    changed_body = nav.target != target.entity
    if changed_body:
        nav.path, nav.search = (), None
    if nav.search is None and (nav.goal is None or changed_body
            or math.dist(nav.goal, target.feet) > 1
            or (not nav.path and tick >= nav.next_plan) or nav.stuck >= 8):
        nav.path = ()
        nav.search = begin(bot.position, target, reach)
        nav.target, nav.goal, nav.stuck = target.entity, tuple(target.feet), 0
    if (nav.search is not None and search_budget
            and advance(nav.search, world, nav.mover.data, reach, search_budget,
                        rialto=match.game_map.map_guid == RIALTO_MAP)):
        nav.path, nav.search = nav.search.path, None
        nav.next_plan = tick + PLAN_INTERVAL
    while nav.path and not isinstance(nav.path[0], LedgeDrop):
        tolerance = 0.01 if len(nav.path) == 1 else 0.15
        if math.dist(bot.position, nav.path[0]) >= tolerance:
            break
        nav.path = nav.path[1:]
    if nav.path and isinstance(nav.path[0], LedgeDrop):
        _drop_step(bot, nav, tick, seconds)
        return
    if not nav.path:
        bot.pace, bot.velocity, bot.held = 0.0, (0.0, 0.0, 0.0), (0, 0)
        bot.keys = False
        nav.mover.velocity = (0.0, 0.0, 0.0)
        return
    destination = nav.path[0]
    # Revalidate the immediate edge when geometry changes or a route becomes stale.
    if edge(world, bot.position, destination[0], destination[2], nav.mover.data) is None:
        nav.path, nav.next_plan = (), tick + PLAN_INTERVAL
        bot.pace, bot.velocity, bot.held = 0.0, (0.0, 0.0, 0.0), (0, 0)
        bot.keys = False
        nav.mover.velocity = (0.0, 0.0, 0.0)
        return
    maximum = bot.speed * fight.halts.npc_speed_scale(bot.entity, tick)
    bot.pace = min(maximum, bot.pace + bot.accel * seconds)
    _drive(bot, nav, tick, seconds, destination, bot.pace, walls=match.combat.walls)


def _constrain_wall(walls, before, mover):
    if walls is None or not walls.pieces:
        return False
    data = mover.data
    after, normals = walls.constrain(before, tuple(mover.position), data.stand_radius, data.stand_height)
    if normals:
        walls.place_mover(mover, after, normals)
    return bool(normals)


def _drive(bot, nav, tick, seconds, destination, speed, *, walls=None):
    yaw = bots.turned(
        bot.yaw, bots.heading(destination[0] - bot.position[0], destination[2] - bot.position[2]),
        bots.TURN_RATE * seconds)
    before = bot.position
    outcome = nav.mover.step_pose(Command(tick, yaw=round(yaw), pitch=round(bot.pitch)), seconds, tick,
                                  move_to=(destination, speed))
    if _constrain_wall(walls, before, nav.mover):
        outcome = False
    bot.position, bot.velocity, bot.yaw = tuple(nav.mover.position), nav.mover.velocity, float(yaw)
    dx, dz = destination[0] - before[0], destination[2] - before[2]
    distance = math.hypot(dx, dz)
    bot.held = bots.throttles(bot.yaw, (dx / distance, dz / distance)) if distance else (0, 0)
    bot.last_held, bot.input_tick, bot.keys = bot.held, tick, True
    bot.moved_tick = tick
    nav.stuck = nav.stuck + 1 if math.dist(before, bot.position) < 0.001 else 0
    return outcome


def _drop_step(bot, nav, tick, seconds):
    drop = nav.path[0]
    started = tick if drop.started is None else drop.started
    nav.last_tick = tick
    before = bot.position
    landed = (drop.released and not nav.mover.airborne
              and abs(before[1] - RIALTO_LANDING[1]) < 0.15
              and math.dist(before, RIALTO_LANDING) < 1)
    if landed or tick - started >= 120 or before[1] < RIALTO_LANDING[1] - 0.4:
        stop(bot)
        return
    released = drop.released or before[0] <= RIALTO_DEPARTURE[0] + 0.015 or tick - started >= 55
    nav.path = (LedgeDrop(started, released),)
    nav.mover.step_pose(Command(tick, yaw=round(bot.yaw), pitch=round(bot.pitch)), seconds, tick,
                        move_to=None if released else (RIALTO_DEPARTURE, 4.0))
    bot.position, bot.velocity = tuple(nav.mover.position), nav.mover.velocity
    bot.held = (0, 0) if released else bots.throttles(bot.yaw, (-1.0, 0.0))
    bot.last_held, bot.input_tick, bot.keys = bot.held, tick, True
    bot.moved_tick = tick
