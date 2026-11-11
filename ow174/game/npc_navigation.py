"""Incremental local floor routing for opted-in sandbox NPCs."""

import heapq
import math
from dataclasses import dataclass

from ow174.game import bots, combat
from ow174.game.collision import NOT_GROUND
from ow174.game.mover import mover_data

# A local detour deliberately searches only the immediate cover around the NPC.
RADIUS = 6
SEARCH_SLICE = 4


def sight(world, position, eye, reach):
    origin = (position[0], position[1] + combat.EYE, position[2])
    delta = tuple(b - a for a, b in zip(origin, eye, strict=True))
    distance = math.hypot(*delta)
    if not 0 < distance <= reach:
        return None
    direction = tuple(axis / distance for axis in delta)
    hit = combat.ray_world(world, origin, direction, distance)
    return (distance, delta) if hit is None or hit >= distance else None


def firing_spot(world, position, eye, reach, margin):
    line = sight(world, position, eye, reach)
    if line is None:
        return False
    _, (dx, _, dz) = line
    horizontal = math.hypot(dx, dz)
    if horizontal < combat.EPSILON:
        return True
    x, y, z = position
    side_x, side_z = -dz * margin / horizontal, dx * margin / horizontal
    return all(sight(world, (x + side_x * side, y, z + side_z * side), eye, reach)
               is not None for side in (-1, 1))


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


@dataclass
class Detour:
    world: object
    target: int
    eye: tuple
    start: tuple
    queue: list
    points: dict
    parents: dict
    costs: dict
    path: tuple = ()

    @classmethod
    def begin(cls, world, target, eye, start):
        origin = (0, 0)
        start = tuple(start)
        return cls(world, target, eye, start, [(math.dist(start, eye), 0.0, origin)],
                   {origin: start}, {}, {origin: 0.0})

    def advance(self, data, reach):
        """Reuse the sampled walkable-floor search with a fixed local horizon."""
        for _ in range(SEARCH_SLICE):
            if not self.queue:
                return
            _, cost, current = heapq.heappop(self.queue)
            if cost != self.costs[current]:
                continue
            point = self.points[current]
            if firing_spot(self.world, point, self.eye, reach, data.stand_radius):
                route = []
                while current != (0, 0):
                    route.append(self.points[current])
                    current = self.parents[current]
                self.path = tuple(reversed(route))
                self.queue.clear()
                return
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbor = (current[0] + dx, current[1] + dz)
                if max(map(abs, neighbor)) > RADIUS:
                    continue
                x, z = self.start[0] + neighbor[0], self.start[2] + neighbor[1]
                step = edge(self.world, point, x, z, data)
                if step is None:
                    continue
                total = cost + math.dist(point, step)
                if total >= self.costs.get(neighbor, math.inf):
                    continue
                self.points[neighbor], self.parents[neighbor], self.costs[neighbor] = step, current, total
                heapq.heappush(self.queue, (total + math.dist(step, self.eye), total, neighbor))


def waypoint(world, bot, target, eye, reach):
    """Advance a bot's local route and return its next walkable waypoint."""
    route = bot.combat_detour
    if (route is None or route.world is not world or route.target != target
            or math.dist(route.eye, eye) > .75
            or (not route.path and math.dist(route.start, bot.position) > .75)):
        route = bot.combat_detour = Detour.begin(world, target, eye, bot.position)
    data = mover_data(bot.body)
    if not route.path:
        route.advance(data, reach)
    if not route.path:
        return None
    if math.dist(bot.position, route.path[0]) < .15:
        route.path = route.path[1:]
        if not route.path:
            bot.combat_detour = None
            return None
    point = route.path[0]
    if edge(world, bot.position, point[0], point[2], data) is None:
        bot.combat_detour = None
        return None
    return point
