"""Manual collision-aware pursuit; grid routing is a server reconstruction."""

import heapq
import math
from dataclasses import dataclass

from ow174.game import assassin, bots, combat, tracer
from ow174.game.commands import Command
from ow174.game.mover import SPEED, Mod, Mover, _movers, intent, mover_data

GRID, MAX_NODES, PLAN_INTERVAL, PLAN_SLICE = 1.0, 2048, 30, 4


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


def visible(world, position, target, *, walls=None):
    a, b = target.segment()
    return combat.capsule_visible(world, (position[0], position[1] + combat.EYE, position[2]),
                                  a, b, target.radius, walls=walls)


def edge(world, start, x, z, data, *, walls=None):
    """Require supported floor, headroom and clearance from scene and pillar geometry."""
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
        if walls is not None and walls.blocks_movement(previous, point, radius, data.stand_height):
            return None
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


def advance(search, world, data, reach, budget, *, walls=None):
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
        arrived = remaining(point) == 0 and visible(world, point, search.target, walls=walls)
        horizon = remaining(start) > 24 and cost >= 24 and remaining(point) < remaining(start) - GRID
        if arrived or horizon:
            route = []
            while current != origin:
                route.append(search.points[current])
                current = search.parents[current]
            search.path, search.done = tuple(reversed(route)), True
            break
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = current[0] + dx, current[1] + dz
            if abs(nx) > 32 or abs(nz) > 32:
                continue
            neighbor = edge(world, point, start[0] + nx * GRID, start[2] + nz * GRID, data, walls=walls)
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


def step(match, bot, tick, seconds):
    previous = bot.last_tick
    if previous is not None and tick <= previous:
        return
    bot.last_tick = tick
    first = tick if previous is None else max(previous + 1, tick - bots.CATCH_UP + 1)
    for at in range(first, tick + 1):
        _step(match, bot, at, seconds, PLAN_SLICE if at == tick else 0)


def _step(match, bot, tick, seconds, search_budget):
    target = next((t for t in match.combat._targets() if getattr(t.owner, 'entity', None) == bot.attack_target
                   and getattr(t.owner, 'client', None) is not None and not match.combat.vitals_of(t).dead
                   and not getattr(t.owner, 'gone', False)
                   and not tracer.recalling(t.owner)
                   and (match.game_map.free_for_all or t.team != bot.team)), None)
    world = match.collision_world
    if (match.ended or bot.dead or not bot.firing or target is None or world is None
            or match.combat.is_stunned(bot, tick=tick)
            or bot.entity in match.combat.pins):
        stop(bot)
        return
    if bot.hero == assassin.HERO and bot.body not in _movers():
        stop(bot)  # missing authored metadata is not generic-mover acceptance
        return
    reach = 2.5 if bot.hero == assassin.HERO else 8.0  # explicit manual standoff policy
    walls = match.combat.walls
    if math.dist(bot.position, target.feet) <= reach and visible(world, bot.position, target, walls=walls):
        stop(bot)
        return
    nav = bot.navigation
    if nav is None:
        nav = bot.navigation = Navigator(Mover(bot.position, round(bot.yaw), world, mover_data(bot.body)))
    if nav.last_tick is not None and tick <= nav.last_tick:
        return
    if nav.mover.world is not world or math.dist(nav.mover.position, bot.position) > 0.1:
        nav.mover = Mover(bot.position, round(bot.yaw), world, mover_data(bot.body))
        nav.path, nav.goal, nav.search = (), None, None
    nav.last_tick = tick
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
            and advance(nav.search, world, nav.mover.data, reach, search_budget, walls=walls)):
        nav.path, nav.search = nav.search.path, None
        nav.next_plan = tick + PLAN_INTERVAL
    while nav.path and math.dist(bot.position, nav.path[0]) < (0.15 if len(nav.path) > 1 else 0.01):
        nav.path = nav.path[1:]
    if not nav.path:
        bot.pace, bot.velocity, bot.held = 0.0, (0.0, 0.0, 0.0), (0, 0)
        bot.keys = False
        nav.mover.velocity = (0.0, 0.0, 0.0)
        return
    destination = nav.path[0]
    # Revalidate the immediate edge when geometry changes or a route becomes stale.
    if edge(world, bot.position, destination[0], destination[2], nav.mover.data, walls=walls) is None:
        nav.path, nav.next_plan = (), tick + PLAN_INTERVAL
        bot.pace, bot.velocity, bot.held = 0.0, (0.0, 0.0, 0.0), (0, 0)
        bot.keys = False
        nav.mover.velocity = (0.0, 0.0, 0.0)
        return
    bot.pace = min(bot.speed, bot.pace + bot.accel * seconds)
    yaw = bots.turned(bot.yaw, bots.heading(destination[0] - bot.position[0],
                                           destination[2] - bot.position[2]), bots.TURN_RATE * seconds)
    before = bot.position
    # Navigation runs before this tick's combat update, so it sees the preceding aura sample.
    nav.mover.track_mods(match.combat.lucio_auras.mods(bot.entity), tick - 1)
    mods = sorted((mod for mod in nav.mover.mods.values() if mod.acts(tick)), key=Mod.order)
    speed = bot.pace * max(0.0, intent(mods, SPEED, 1.0))
    nav.mover.step_pose(Command(tick, yaw=round(yaw), pitch=round(bot.pitch)), seconds, tick,
                        move_to=(destination, speed))
    bot.position, bot.velocity, bot.yaw = tuple(nav.mover.position), nav.mover.velocity, float(yaw)
    dx, dz = destination[0] - before[0], destination[2] - before[2]
    distance = math.hypot(dx, dz)
    bot.held = bots.throttles(bot.yaw, (dx / distance, dz / distance)) if distance else (0, 0)
    bot.last_held, bot.input_tick, bot.keys = bot.held, tick, True
    bot.moved_tick = tick
    nav.stuck = nav.stuck + 1 if math.dist(before, bot.position) < 0.001 else 0
