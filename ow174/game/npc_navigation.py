"""Bounded pursuit over static collision and live Ice Wall pillars for explicitly opted-in NPCs."""

import heapq
import math
from dataclasses import dataclass

from ow174.game import bots, combat, mei_wall
from ow174.game.mover import Mover, mover_data
from ow174.game.npc_context import HEAVY_ASSAULT
from ow174.game.script import expr

GRID, MAX_NODES, PLAN_SLICE, RETRY_FRAMES = 1.0, 2048, 8, 30


def visible(world, origin, point, target, *, pillars=()):
    distance = math.dist(origin, point)
    if distance <= combat.EPSILON:
        return False
    direction = tuple((end - start) / distance for start, end in zip(origin, point, strict=True))
    hit = combat.ray_capsule(origin, direction, *target.segment(), target.radius)
    if hit is None:
        return False
    wall = combat.ray_world(world, origin, direction, hit)
    return (wall is None or wall >= hit - combat.EPSILON) and not any(
        mei_wall.ray(piece, origin, direction, hit) is not None for piece in pillars
    )


def edge(world, start, x, z, data, *, pillars=()):
    """Require supported walkable floor, headroom and capsule-width clearance rays."""
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
                    if (combat.ray_world(world, origin, direction, length) is not None
                            or any(mei_wall.ray(piece, origin, direction, length) is not None
                                   for piece in pillars)):
                        return None
        previous = point
    return previous


@dataclass
class Search:
    """Serializable frontier; a budget counts heap pops, including stale entries."""
    start: tuple
    target: combat.Target | tuple[float, float, float]
    queue: list
    points: dict
    parents: dict
    costs: dict
    visited: set
    done: bool = False
    path: tuple = ()


def begin(start, target, reach):
    # Keep a value snapshot, not the player's live controller or mutable pose.
    if isinstance(target, combat.Target):
        target = combat.Target(target.entity, target.team, tuple(target.feet), target.height,
                               target.radius, target.eye, None)
        point = target.feet
    else:
        target = point = tuple(target)
    start = tuple(start)
    origin = (0, 0, round(start[1] * 8))
    return Search(start, target, [(max(0, math.dist(start, point) - reach), 0.0, origin)],
                  {origin: start}, {}, {origin: 0.0}, set())


def advance(search, world, data, reach, budget, *, pillars=()):
    """Resume a bounded floor search; retain the frontier between host updates."""
    entity_target = search.target if isinstance(search.target, combat.Target) else None
    destination = entity_target.feet if entity_target is not None else search.target

    def remaining(point):
        return max(0, math.dist(point, destination) - reach)

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
        x, y, z = point
        tx, ty, tz = destination
        arrived = remaining(point) == 0 and (entity_target is None or visible(
            world, (x, y + combat.EYE, z), (tx, ty + entity_target.eye, tz), entity_target, pillars=pillars,
        ))
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
            neighbor = edge(world, point, start[0] + nx * GRID, start[2] + nz * GRID, data,
                            pillars=pillars)
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


@dataclass
class Pursuit:
    search: Search | None = None
    retry_at: int = 0
    wall_entities: tuple = ()


def stop(bot):
    pursuit = bot.pursuit
    if pursuit.search is None and not bot.route:
        return
    pursuit.search = None
    bot.route, bot.target, bot.keys = [], None, False
    bot.turning = 0.0
    bot.pace, bot.velocity, bot.held = 0.0, (0.0, 0.0, 0.0), (0, 0)


def refresh_obstacles(bot, pieces):
    pursuit = getattr(bot, 'pursuit', None)
    if pursuit is None:
        return
    entities = tuple(pieces)
    if entities != pursuit.wall_entities:
        stop(bot)
        pursuit.wall_entities = entities
        pursuit.retry_at = 0


def update(fight, bot, target, reach):
    pursuit = bot.pursuit
    if getattr(bot, "wall_mover", None) is not None:
        stop(bot)
        return
    if target is None:
        stop(bot)
        pursuit.retry_at = 0
        return
    search = pursuit.search
    if search is not None and (target.entity != search.target.entity
                               or math.dist(target.feet, search.target.feet) > GRID):
        stop(bot)
        search = None
        pursuit.retry_at = 0
    if search is None:
        if fight.tick < pursuit.retry_at:
            return
        search = pursuit.search = begin(bot.position, target, reach)
    if search.done:
        if bot.target is None and (not bot.route or bot.index == len(bot.route) - 1):
            stop(bot)
            pursuit.retry_at = fight.tick + RETRY_FRAMES
        return
    if advance(search, fight.match.collision_world, mover_data(bot.body), reach, PLAN_SLICE,
               pillars=fight.ice_wall.pieces.values()) and search.path:
        bot.route = list(search.path)
        bot.index, bot.wait, bot.last_tick = -1, 0.0, fight.tick


CHARGE_GRAPH = 0x1408
CHARGE_BUTTON = 35
CHARGE_LIMIT_MS = 3000  # Host movement cap for an uncompleted charge.


def charge_instance(bot):
    if bot.hero != HEAVY_ASSAULT:
        return None
    instances = bot.body_script.component.instances.values()
    return next((instance for instance in instances if instance.graph.index == CHARGE_GRAPH), None)


def _finish_charge(fight, bot, instance):
    motion = instance.state(33)
    if motion.active:
        motion.end(False)
    instance.drop_begins([motion])
    instance.state(36).end(False)
    instance.state(41).stop()
    instance.run_node(instance.graph.node(6), None)
    instance.run_node(instance.graph.node(45), None)
    instance.write((expr.INSTANCE, 536), expr.Entity(0))
    fight.pins.pop(bot.entity, None)
    instance.component.edges([(CHARGE_BUTTON, False)])
    bot.charge_mover = None
    bot.velocity = (0.0, 0.0, 0.0)


def interrupt_charge(fight, bot):
    instance = charge_instance(bot)
    if instance is None:
        return
    phase = instance.states.get(36)
    if phase is not None and phase.active:
        with instance.component.operation():
            _finish_charge(fight, bot, instance)


def retire_carried_body(fight, bot, entity):
    """End the charge while its latched receiver is still the old body."""
    script = bot.body_script
    if bot.dead or script is None or script.component.strict or script.component.fault is not None:
        return
    instance = charge_instance(bot)
    if instance is None:
        return
    phase = instance.states.get(36)
    slot = instance.vars.get(536)
    if phase is not None and phase.active and slot is not None and expr.to_entity(slot.value()) == entity:
        with script.component.operation():
            _finish_charge(fight, bot, instance)


def advance_heavy_charge(fight, bot, script):
    """Drive 1408's live motion with a server capsule sweep and retire it on impact."""
    if bot.hero != HEAVY_ASSAULT or script.component.strict or script.component.fault is not None:
        return
    instance = charge_instance(bot)
    if instance is None:
        return
    phase = instance.states.get(36)
    motion = instance.states.get(33)
    if phase is None or not phase.active:
        fight.pins.pop(bot.entity, None)
        if getattr(bot, "charge_mover", None) is not None:
            script.component.edges([(CHARGE_BUTTON, False)])
            bot.charge_mover = None
            bot.velocity = (0.0, 0.0, 0.0)
        ability = instance.states.get(41)
        if ability is not None and ability.remaining() == 0 and instance.vars[59].value() == 2:
            instance.run_node(instance.graph.node(6), None)
            instance.write((expr.INSTANCE, 536), expr.Entity(0))
        return
    if bot.entity in fight.sombra_controls.locks or fight.is_asleep(bot) or fight.is_stunned(bot):
        _finish_charge(fight, bot, instance)
        return
    if motion is None or not motion.active or motion.ending:
        return
    if script.component.now - motion.start >= CHARGE_LIMIT_MS:
        _finish_charge(fight, bot, instance)
        return
    config = motion.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
    vector = motion.evaluate(config, watch=False)
    if not isinstance(vector, expr.Vec3) or math.hypot(vector.x, vector.z) <= combat.EPSILON:
        _finish_charge(fight, bot, instance)
        return
    slot = instance.vars.get(536)
    latched = expr.to_entity(slot.value()) if slot is not None else expr.INVALID_ENTITY
    victim = None
    if expr.valid_entity(latched):
        victim = next((target.owner for target in fight._enemies(bot) if target.entity == latched
                       and isinstance(getattr(target.owner, "mover", None), Mover)), None)
        if victim is None:
            _finish_charge(fight, bot, instance)
            return
        if fight.pins.get(bot.entity) != latched:
            fight.pins[bot.entity] = latched
            fight._interrupt_body(victim)
    world = fight.match.collision_world
    mover = getattr(bot, "charge_mover", None)
    if mover is None or mover.world is not world or tuple(mover.position) != tuple(bot.position):
        mover = bot.charge_mover = Mover(bot.position, round(bot.yaw), world, mover_data(bot.body))
    requested = vector.x, 0.0, vector.z
    constrain = fight.ice_wall.constrain_displacement if fight.ice_wall.pieces else None
    before = bot.position
    clipped = mover.displace(requested, combat.TICK, constrain=constrain)
    bot.position = tuple(mover.position)
    bot.velocity = clipped
    bot.moved_tick = fight.tick
    blocked = math.hypot(clipped[0] - requested[0], clipped[2] - requested[2]) > 0.01
    if victim is not None:
        delta = tuple((end - start) / combat.TICK for start, end in zip(before, bot.position, strict=True))
        carried = victim.mover.displace(delta, combat.TICK, constrain=constrain)
        if blocked or math.hypot(carried[0] - delta[0], carried[2] - delta[2]) > 0.01:
            instance.run_node(instance.graph.node(47), None)
            bot.velocity = (0.0, 0.0, 0.0)
    elif blocked:
        _finish_charge(fight, bot, instance)
