"""Collision checks shared with the parent NPC navigation candidate."""

import heapq
import math
from dataclasses import dataclass

from ow174.game import bots, combat
from ow174.game.collision import NOT_GROUND
from ow174.game.mover import mover_data


GRID, MAX_NODES, PLAN_INTERVAL, PLAN_SLICE = 1.0, 2048, 30, 4


@dataclass
class Search:
    start: tuple
    target: combat.Target
    queue: list
    points: dict
    parents: dict
    costs: dict
    visited: set
    done: bool = False
    path: tuple = ()


@dataclass
class Pursuit:
    world: object
    search: Search
    path: tuple = ()
    next_plan: int = 0


def pin_contact(world, position, target, data, reach):
    origin = (position[0], position[1] + combat.EYE, position[2])
    x, y, z = target.feet
    a, b = target.segment()
    if (math.dist(origin, (x, y + target.eye, z)) > reach
            or not combat.capsule_visible(world, origin, a, b, target.radius)):
        return False
    destination = edge(world, position, x, z, data)
    return destination is not None and abs(destination[1] - y) <= data.step_down


def begin(start, target, reach):
    target = combat.Target(target.entity, target.team, tuple(target.feet), target.height,
                           target.radius, target.eye, None)
    start = tuple(start)
    origin = (0, 0, round(start[1] * 8))
    return Search(start, target, [(max(0, math.dist(start, target.feet) - reach), 0.0, origin)],
                  {origin: start}, {}, {origin: 0.0}, set())


def advance(search, world, data, reach, budget, lane_clear):
    """Resume the shared floor-grid search toward a clear pin position."""
    def remaining(point):
        return max(0, math.dist(point, search.target.feet) - reach)

    start = search.start
    origin = (0, 0, round(start[1] * 8))
    for _ in range(budget):
        if not search.queue or len(search.visited) >= MAX_NODES:
            search.done = True
            break
        _, cost, current = heapq.heappop(search.queue)
        if current in search.visited:
            continue
        search.visited.add(current)
        point = search.points[current]
        if (remaining(point) == 0 and pin_contact(world, point, search.target, data, reach)
                and lane_clear(point, search.target.feet)):
            route = []
            while current != origin:
                route.append(search.points[current])
                current = search.parents[current]
            search.path, search.done = tuple(reversed(route)), True
            break
        cardinal_clear = False
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            diagonal = dx != 0 and dz != 0
            if diagonal and cardinal_clear:
                break
            nx, nz = current[0] + dx, current[1] + dz
            if abs(nx) > 32 or abs(nz) > 32:
                continue
            neighbor = edge(world, point, start[0] + nx * GRID, start[2] + nz * GRID, data)
            if neighbor is None or not lane_clear(point, neighbor):
                continue
            if not diagonal:
                cardinal_clear = True
            address = (nx, nz, round(neighbor[1] * 8))
            new_cost = cost + math.dist(point, neighbor)
            if new_cost >= search.costs.get(address, math.inf):
                continue
            search.points[address], search.parents[address] = neighbor, current
            search.costs[address] = new_cost
            heapq.heappush(search.queue, (new_cost + remaining(neighbor), new_cost, address))


def edge(world, start, x, z, data, *, side_clearance=True):
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
            for lateral in ((-1, 0, 1) if side_clearance else (0,)):
                for height in (radius + 0.01, top):
                    origin = (previous[0] + side[0] * lateral,
                              max(previous[1], point[1]) + height, previous[2] + side[1] * lateral)
                    if combat.ray_world(world, origin, direction, length) is not None:
                        return None
        previous = point
    return previous


def teammate_clear(match, bot, start, end, data):
    x, y, z = start
    dx, dz = end[0] - x, end[2] - z
    length = math.hypot(dx, dz)
    if length <= combat.EPSILON:
        return True
    ux, uz = dx / length, dz / length
    for other in match.bots:
        if (other is bot or other.dead or other.team != bot.team
                or other.spawner.get("manual_spawn") is not True):
            continue
        ox, oy, oz = other.position
        other_data = mover_data(other.body)
        if y >= oy + other_data.stand_height or oy >= y + data.stand_height:
            continue
        bx, bz = ox - x, oz - z
        radius = data.stand_radius + other_data.stand_radius + 0.02
        along = min(length, max(0.0, bx * ux + bz * uz))
        if math.hypot(bx - ux * along, bz - uz * along) < radius:
            if along > 0.0 or math.hypot(bx - dx, bz - dz) <= math.hypot(bx, bz):
                return False
    return True
