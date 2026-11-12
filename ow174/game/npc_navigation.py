"""Manual collision-aware pursuit; grid routing is a server reconstruction."""

import heapq
import math
from dataclasses import dataclass
from functools import partial

from ow174.game import assassin, bots, combat, heavy_assault
from ow174.game.collision import NOT_GROUND
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, nodes, runtime

GRID, MAX_NODES, PLAN_INTERVAL, PLAN_SLICE = 1.0, 2048, 30, 4
RETRY_FRAMES = PLAN_INTERVAL
RIALTO_MAP = 0x080000000000079F
# Server-authored connection through the southern opening in the upper railing.
RIALTO_LEDGE = (-139.081, 3.75, -103.052)
RIALTO_DEPARTURE = (-141.6, 3.75, -103.052)
RIALTO_LANDING = (-141.4, -2.25, -103.25)
SNIPER = 0x02E00000000001B8
SNIPER_REPOSITION_GRAPH = 0x142C
SNIPER_REPOSITION_NODE = 16
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
    next_plan: int | None = 0  # None caches an exhausted search with no dynamic blockers.
    last_tick: int | None = None
    stuck: int = 0
    search: "Search | None" = None
    charge_contact: tuple[int, int] | None = None


def _lower_platform(position):
    return math.dist(position, RIALTO_LANDING) < 20 and abs(position[1] - RIALTO_LANDING[1]) < 1


def visible_aim(world, position, target):
    """Return a clear shot direction to the target capsule, preferring eye height."""
    origin = (position[0], position[1] + combat.EYE, position[2])
    a, b = target.segment()
    x, y, z = target.feet
    eye = (x, y + min(target.eye, target.height - 0.01), z)
    def clear(point):
        delta = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.hypot(*delta)
        if distance <= combat.EPSILON:
            return None
        direction = tuple(axis / distance for axis in delta)
        contact = combat.ray_capsule(origin, direction, a, b, target.radius)
        if contact is not None:
            wall = combat.ray_world(world, origin, direction, contact)
            if wall is None or wall >= contact:
                return direction
        return None

    for point in (eye, b, a):
        direction = clear(point)
        if direction is not None:
            return direction
    dx, dz = x - origin[0], z - origin[2]
    horizontal = math.hypot(dx, dz)
    if horizontal > combat.EPSILON:
        width = target.radius * 0.75 / horizontal
        middle = (a[1] + b[1]) * 0.5
        return clear((x + dz * width, middle, z - dx * width)) or clear(
            (x - dz * width, middle, z + dx * width)
        )
    return None


def visible(world, position, target):
    return visible_aim(world, position, target) is not None


def ballistic_aim(world, position, target, speed: float, gravity: float):
    """Return shot yaw and pitch for a low arc that passes the server's projectile sweep."""
    from ow174.game import mccree

    origin = (position[0], position[1] + combat.EYE, position[2])
    a, b = target.segment()
    dx, dy, dz = ((start + end) * 0.5 - eye for start, end, eye in zip(a, b, origin, strict=True))
    distance = math.hypot(dx, dz)
    if not all(math.isfinite(value) for value in (distance, dy, speed, gravity)):
        return None
    if distance <= combat.EPSILON or speed <= 0.0 or gravity <= 0.0:
        return None
    square_speed = speed * speed
    discriminant = square_speed**2 - gravity * (gravity * distance**2 + 2 * dy * square_speed)
    if discriminant < 0:
        return None
    tangent = (gravity * distance**2 + 2 * dy * square_speed) / (
        distance * (square_speed + math.sqrt(discriminant))
    )
    horizontal = speed / math.hypot(1.0, tangent)
    vertical = horizontal * tangent + gravity * combat.TICK / 2
    yaw = bots.heading(dx, dz)
    pitch = round(-math.atan2(vertical, horizontal) * 65536 / math.tau)
    direction = combat.aim(yaw, pitch)
    velocity = tuple(expr.f32(axis * speed) for axis in direction)
    stride = math.hypot(velocity[0], velocity[2]) * combat.TICK
    if stride <= combat.EPSILON:
        return None
    point = origin
    for _ in range(min(256, math.ceil(distance / stride) + 1)):
        velocity = mccree.gravity_velocity(velocity, gravity, combat.TICK)
        step_speed = math.hypot(*velocity)
        heading = tuple(axis / step_speed for axis in velocity)
        length = step_speed * combat.TICK
        contact = combat.ray_capsule(point, heading, a, b, target.radius)
        wall = combat.ray_world(world, point, heading, length)
        if contact is not None and contact <= length and (wall is None or contact <= wall):
            return yaw, pitch
        if wall is not None:
            return None
        point = tuple(start + axis * combat.TICK for start, axis in zip(point, velocity, strict=True))
    return None


def pin_contact(world, position, target, data):
    """Require a walkable capsule-width approach for Assassin contact."""
    x, y, z = target.feet
    origin = (position[0], position[1] + combat.EYE, position[2])
    a, b = target.segment()
    if (world is None or math.dist(origin, (x, y + target.eye, z)) > assassin.PIN_RANGE
            or not combat.capsule_visible(world, origin, a, b, target.radius)):
        return False
    destination = edge(world, position, x, z, data)
    return destination is not None and abs(destination[1] - y) <= data.step_down


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


def sample_spawn_candidates(world, request, data):
    """Reconstruct local spawn candidates from static floor and capsule clearance."""
    return tuple(point for point in spawn_candidate_probes(world, request, data) if point is not None)


def spawn_candidate_probes(world, request, data):
    """Yield once for every tested location, including rejected candidates."""
    # 0CBA requests a 70 m sector in later Junkenstein waves.
    if not (0 < request.radius <= 70 and 1 <= request.max_candidates <= 300):
        raise ValueError("spawn search exceeds the local collision sampling bounds")
    x, y, z = request.position
    floor = world.ground(x, z, y + bots.STEP_UP, y - data.step_down)
    slope = math.cos(math.radians(data.max_slope))
    if floor is None or floor.surface & NOT_GROUND or floor.normal[1] < slope:
        return
    start = x, floor.y, z
    dx, _, dz = request.direction
    heading = math.atan2(dz, dx)
    golden_turn = (math.sqrt(5) - 1) / 2
    for index in range(request.max_candidates):
        radius = request.radius * math.sqrt((index + 0.5) / request.max_candidates)
        angle = heading + request.half_angle * (2 * (index * golden_turn % 1) - 1)
        point = edge(world, start, x + radius * math.cos(angle), z + radius * math.sin(angle), data)
        yield expr.Vec3(*point) if point is not None else None


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
    blocked_by_teammate: bool = False


def begin(start, target, reach):
    # Keep a value snapshot, not the player's live controller or mutable pose.
    target = combat.Target(target.entity, target.team, tuple(target.feet), target.height,
                           target.radius, target.eye, None)
    start = tuple(start)
    origin = (0, 0, round(start[1] * 8))
    return Search(start, target, [(max(0, math.dist(start, target.feet) - reach), 0.0, origin)],
                  {origin: start}, {}, {origin: 0.0}, set())


def advance(search, world, data, reach, budget, *, rialto=False, contact=False, lane_clear=None,
            require_visibility=True, firing_position=None):
    """Resume a bounded floor search; retain the frontier between host updates."""
    can_fire = firing_position if firing_position is not None else partial(visible, world)
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
        ledge = (rialto and _lower_platform(search.target.feet)
                 and math.dist(point, RIALTO_LEDGE) <= 1.5
                 and edge(world, point, RIALTO_LEDGE[0], RIALTO_LEDGE[2], data) is not None)
        arrived = remaining(point) == 0 and (
            pin_contact(world, point, search.target, data) if contact
            else not require_visibility or can_fire(point, search.target)
        )
        if arrived and contact and lane_clear is not None:
            arrived = lane_clear(point, search.target.feet)
            search.blocked_by_teammate |= not arrived
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
        cardinal_progress = False
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            diagonal = dx != 0 and dz != 0
            # Try oblique exits when no straight step extends the frontier.
            # A clear step back into the existing route cannot replace an exit.
            if diagonal and cardinal_progress:
                break
            nx, nz = current[0] + dx, current[1] + dz
            if abs(nx) > 32 or abs(nz) > 32:
                continue
            neighbor = edge(world, point, start[0] + nx * GRID, start[2] + nz * GRID, data)
            if neighbor is None:
                continue
            # Moving teammates only constrain the first few steps. Farther
            # ahead, the mover checks them again when it reaches that edge.
            if lane_clear is not None and cost < 2 * GRID and not lane_clear(point, neighbor):
                search.blocked_by_teammate = True
                continue
            address = (nx, nz, round(neighbor[1] * 8))
            new_cost = cost + math.dist(point, neighbor)
            if new_cost >= search.costs.get(address, math.inf):
                continue
            if not diagonal:
                cardinal_progress = True
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


class ObjectiveApproach:
    """Drive a collision mover toward the reach threshold of a placed objective."""

    def __init__(self, mover, target, reach):
        self.mover, self.target, self.reach = mover, target, reach
        self.search = None
        self.path = ()
        self.blocked_at = None
        self.failed = False
        self.arrived = False

    def step(self, frame, seconds, *, budget=PLAN_SLICE, speed_scale=1.0):
        if self.failed or self.arrived:
            return False
        body = self.mover
        if math.dist(body.position, self.target.feet) < self.reach:
            self.arrived = True
            return True
        if not self.path:
            if self.search is None:
                self.search = begin(body.position, self.target, self.reach - 0.05)
            if not advance(self.search, body.world, body.data, self.reach - 0.05, budget,
                           require_visibility=False):
                return False
            self.path, self.search = self.search.path, None
            if not self.path:
                self.failed = True
                return False
        destination = self.path[0]
        yaw = bots.heading(destination[0] - body.position[0], destination[2] - body.position[2])
        speed = expr.f32(body.data.run_forward * speed_scale)
        moved = body.step_pose(Command(frame, yaw=yaw), seconds, frame, move_to=(destination, speed))
        if moved is True or math.dist(body.position, destination) < 0.02:
            self.path = self.path[1:]
        elif moved is False:
            self.path = ()
            self.failed = self.blocked_at is not None and math.dist(body.position, self.blocked_at) < 0.25
            self.blocked_at = tuple(body.position)
        return False


def _committed_drop(nav):
    return (nav is not None and nav.path and isinstance(nav.path[0], LedgeDrop)
            and (nav.path[0].released or nav.mover.airborne))


def stop(bot, *, land=False):
    nav = bot.navigation
    keep_drop = land and _committed_drop(nav)
    velocity = (0.0, 0.0, 0.0)
    if nav is not None:
        nav.goal = None
        nav.search = None
        if keep_drop:
            nav.path = (LedgeDrop(nav.path[0].started, released=True),)
        else:
            nav.path = ()
            if land and nav.mover.airborne:
                velocity = (0.0, nav.mover.velocity[1], 0.0)
                nav.mover.driven = False
            nav.mover.velocity = velocity
    bot.pace = 0.0
    if not keep_drop:
        bot.velocity = velocity
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


def reposition_ready(state):
    return (state is not None and not state.active
            and (not state.ended_at or state.now - state.ended_at >= SNIPER_REPOSITION_COOLDOWN_MS))


def reposition_range(position, target_feet):
    x, y, z = position
    tx, ty, tz = target_feet
    return abs(y - ty) <= 0.75 and 0.1 <= math.hypot(x - tx, z - tz) <= 3


def start_reposition(match, state, world, bot, target, tick):
    """Sandbox fallback for the unresolved authored destination-selection state."""
    if not reposition_ready(state) or not reposition_range(bot.position, target.feet):
        return False
    if not visible(world, bot.position, target):
        return False
    x, y, z = bot.position
    dx, dz = x - target.feet[0], z - target.feet[2]
    distance = math.hypot(dx, dz)
    data = mover_data(bot.body)
    for angle in (0, math.pi / 4, -math.pi / 4):
        cos, sin = math.cos(angle), math.sin(angle)
        nx = x + (dx * cos - dz * sin) * 4 / distance
        nz = z + (dz * cos + dx * sin) * 4 / distance
        destination = edge(world, bot.position, nx, nz, data)
        if (destination is None or abs(destination[1] - y) > 0.5 or not visible(world, destination, target)
                or not teammate_clear(match, bot, bot.position, destination, data)):
            continue
        state.instance.write((expr.INSTANCE, 1220), expr.Vec3(*destination))
        request_reposition(state, tick, runtime.BEGIN)
        return True
    return False


def planning_budgets(match, actors, turn):
    """Share one search slice per match update and rotate any extra pops."""
    positions = {player.entity: player.mover.position for player in match.players if player.mover is not None}

    def needs_budget(bot):
        if bot.navigation is None or bot.navigation.search is not None:
            return True
        feet = positions.get(bot.attack_target)
        # A sniper already at its firing position can still need a retreat search.
        return (match.game_map.map_guid == RIALTO_MAP and feet is not None and bot.hero == SNIPER
                and reposition_range(bot.position, feet) and reposition_ready(reposition_state(bot)))

    active = [bot.entity for bot in actors if bot.firing and not bot.dead and needs_budget(bot)]
    if not active:
        return {}
    share, remainder = divmod(PLAN_SLICE, len(active))
    return {entity: share + ((index - turn) % len(active) < remainder)
            for index, entity in enumerate(active)}


def step(match, bot, tick, seconds, *, plan_budget=PLAN_SLICE):
    previous = bot.last_tick
    if previous is not None and tick <= previous:
        return
    bot.last_tick = tick
    first = tick if previous is None else max(previous + 1, tick - bots.CATCH_UP + 1)
    for at in range(first, tick + 1):
        _step(match, bot, at, seconds, plan_budget if at == tick else 0)


def _step(match, bot, tick, seconds, search_budget):
    if match.combat.junkrat.rooted(bot.entity, tick):
        stop(bot)
        bot._pause(tick)
        return
    if bot.impulse_mover is not None and not bot.dead:
        bot._impulse_step(tick, seconds, match.collision_world)
        return
    if bot.attack_mode == "melee":
        return
    if bot.attack_mode == "reposition":
        from ow174.game import sniper_reposition

        if (bot.dead or match.ended or not bot.firing or match.combat.is_stunned(bot, tick=tick)
                or match.combat.is_asleep(bot, tick=tick)
                or bot.entity in match.combat.sombra_controls.locks):
            sniper_reposition.stop(bot, tick)
        else:
            sniper_reposition.advance(match.combat, bot, tick=tick, seconds=seconds)
        return
    target = next((t for t in match.combat._targets() if getattr(t.owner, 'entity', None) == bot.attack_target
                   and getattr(t.owner, 'client', None) is not None and not match.combat.vitals_of(t).dead
                   and not getattr(t.owner, 'gone', False)
                   and (match.game_map.free_for_all or t.team != bot.team)), None)
    world = match.collision_world
    nav = bot.navigation
    current_mover = (nav is not None and world is not None and nav.mover.world is world
                     and math.dist(nav.mover.position, bot.position) <= 0.1)
    if not match.ended and (not bot.dead or bot.retain_corpse) and current_mover:
        if _committed_drop(nav):
            _drop_step(bot, nav, tick, seconds)
            return
        if (bot.dead or not bot.firing) and nav.mover.airborne:
            _settle_step(bot, nav, tick, seconds)
            return
    reposition = None if bot.dead or match.ended else reposition_state(bot)
    movement = reposition.move_request() if isinstance(reposition, nodes.MoveTo) else None
    if (match.ended or bot.dead or not bot.firing or world is None or target is None
            or match.combat.is_stunned(bot, tick=tick)
            or bot.entity in match.combat.pins):
        if movement is not None:
            request_reposition(reposition, tick, runtime.ABORT)
        # Cancel pursuit input while the mover continues to own the fall.
        land = current_mover and not match.ended and not bot.dead and nav.mover.airborne
        stop(bot, land=land)
        if land:
            _settle_step(bot, nav, tick, seconds)
        return
    contact = bot.hero == assassin.HERO
    reach = 2.5 if contact else 8.0  # explicit manual standoff policy
    if (nav is not None and nav.path and isinstance(nav.path[0], LedgeDrop)
            and nav.mover.world is world and math.dist(nav.mover.position, bot.position) <= 0.1):
        if nav.target == target.entity and _lower_platform(target.feet):
            _drop_step(bot, nav, tick, seconds)
            return
        nav.path, nav.goal = (), None
    charge = heavy_assault.charge_motion(bot)
    if charge is not None:
        ability, motion = charge
        if abs(bot.position[1] - target.feet[1]) >= heavy_assault.CHARGE_FLOOR_TOLERANCE:
            heavy_assault.stop_charge(bot.body_script.component, ability)
            stop(bot)
            return
        if motion is None:
            stop(bot)
            return
        if nav is None or nav.mover.world is not world or math.dist(nav.mover.position, bot.position) > 0.1:
            nav = bot.navigation = Navigator(Mover(bot.position, round(bot.yaw), world, mover_data(bot.body)))
        nav.path, nav.goal, nav.search = (), None, None
        destination, speed = motion
        x, y, z = bot.position
        ahead = min(1.0, speed * seconds)
        dx, dz = destination[0] - x, destination[2] - z
        tx, _, tz = target.feet
        center = (tx, 0.0, tz)
        radius = nav.mover.data.stand_radius + target.radius + 0.005
        clipped_to_target = False
        if (tx - x) * dx + (tz - z) * dz > 0:
            contact_distance = combat.ray_capsule((x, 0.0, z), (dx, 0.0, dz), center, center, radius)
            if contact_distance is not None and contact_distance < ahead:
                ahead = contact_distance
                clipped_to_target = True
                if ahead <= 0.002:
                    # Hold at contact while the authored impact query advances.
                    nav.charge_contact = (tick, target.entity)
                    stop(bot)
                    return
                destination = (x + dx * ahead, y, z + dz * ahead)
        next_step = (x + dx * ahead, y, z + dz * ahead)
        floor = world.ground(next_step[0], next_step[2],
                             y + bots.STEP_UP + 0.05, y - heavy_assault.CHARGE_FLOOR_TOLERANCE)
        if (floor is None or floor.surface & NOT_GROUND
                or not teammate_clear(match, bot, bot.position, next_step, nav.mover.data)):
            heavy_assault.stop_charge(bot.body_script.component, ability)
            stop(bot)
            return
        outcome = _drive(bot, nav, tick, seconds, destination, speed)
        if outcome is False or nav.stuck >= 8:
            heavy_assault.stop_charge(bot.body_script.component, ability)
            stop(bot)
        elif clipped_to_target and math.hypot(bot.position[0] - tx, bot.position[2] - tz) <= radius + 0.002:
            nav.charge_contact = (tick, target.entity)
        return
    if bot.attack_mode == "charge":
        return  # Explicit charge waits for its authored MoveTo instead of pursuing.
    if current_mover and movement is None and not nav.path and nav.mover.airborne:
        _settle_step(bot, nav, tick, seconds)
        return
    if (movement is None and reposition is not None and search_budget
            and match.game_map.map_guid == RIALTO_MAP and start_reposition(match, reposition, world, bot, target, tick)):
        stop(bot)
        return
    if movement is None and math.dist(bot.position, target.feet) <= reach:
        if (assassin.contact(match, bot, target) if contact
                else visible(world, bot.position, target)):
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
        distance = math.dist(bot.position, destination)
        if distance > combat.EPSILON:
            fraction = min(1.0, speed * seconds / distance)
            next_step = tuple(start + (end - start) * fraction
                              for start, end in zip(bot.position, destination, strict=True))
            if not teammate_clear(match, bot, bot.position, next_step, nav.mover.data):
                request_reposition(reposition, tick, runtime.ABORT)
                stop(bot)
                return
        outcome = _drive(bot, nav, tick, seconds, tuple(destination), speed)
        if outcome is not None:
            request_reposition(reposition, tick, runtime.FINISH if outcome else runtime.ABORT)
        return
    # Finish a sampled goal and let the bot follow the result before resampling.
    # A target can move farther than the threshold while the search is running.
    # Replanning immediately would discard the new route after one step.
    changed_body = nav.target != target.entity
    if changed_body:
        nav.path, nav.search = (), None
    goal_delta = math.inf if nav.goal is None else math.dist(nav.goal, target.feet)
    resample = (goal_delta >= 0.25 if nav.next_plan is None
                else tick >= nav.next_plan and (not nav.path or goal_delta > 1))
    if nav.search is None and (nav.goal is None or changed_body or nav.stuck >= 8
            or resample):
        nav.path = ()
        nav.search = begin(bot.position, target, reach)
        nav.target, nav.goal, nav.stuck = target.entity, tuple(target.feet), 0
    def lane_clear(start, end):
        return teammate_clear(match, bot, start, end, nav.mover.data)

    if (nav.search is not None and search_budget
            and advance(nav.search, world, nav.mover.data, reach, search_budget,
                        rialto=match.game_map.map_guid == RIALTO_MAP, contact=contact, lane_clear=lane_clear)):
        nav.path = nav.search.path
        nav.next_plan = tick + PLAN_INTERVAL if nav.path or nav.search.blocked_by_teammate else None
        nav.search = None
    while nav.path and not isinstance(nav.path[0], LedgeDrop):
        tolerance = 0.01 if len(nav.path) == 1 else 0.15
        if math.dist(bot.position, nav.path[0]) >= tolerance:
            break
        nav.path = nav.path[1:]
    if nav.path and isinstance(nav.path[0], LedgeDrop):
        if _lower_platform(target.feet):
            _drop_step(bot, nav, tick, seconds)
            return
        nav.path, nav.goal = (), None
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
    bot.pace = min(bot.speed, bot.pace + bot.accel * seconds)
    destination = _avoid_teammates(match, bot, nav, destination, seconds)
    if destination is None:
        nav.stuck += 1
        bot.velocity, bot.held, bot.keys = (0.0, 0.0, 0.0), (0, 0), False
        nav.mover.velocity = (0.0, 0.0, 0.0)
        return
    _drive(bot, nav, tick, seconds, destination, bot.pace)


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


def _avoid_teammates(match, bot, nav, destination, seconds):
    """Choose a short supported sidestep around a living manual teammate."""
    x, y, z = bot.position
    dx, dz = destination[0] - x, destination[2] - z
    length = math.hypot(dx, dz)
    if length <= combat.EPSILON:
        return destination
    ux, uz = dx / length, dz / length
    lookahead = max(0.35, bot.pace * seconds)
    span = min(length, lookahead)
    data = nav.mover.data
    if teammate_clear(match, bot, bot.position, (x + ux * span, y, z + uz * span), data):
        return destination
    side = 1 if bot.entity & 1 else -1
    for angle in (60 * side, -60 * side, 90 * side, -90 * side):
        radians = math.radians(angle)
        sx = ux * math.cos(radians) - uz * math.sin(radians)
        sz = ux * math.sin(radians) + uz * math.cos(radians)
        if teammate_clear(match, bot, bot.position, (x + sx * lookahead, y, z + sz * lookahead), data):
            point = edge(nav.mover.world, bot.position, x + sx * lookahead, z + sz * lookahead, data)
            if point is not None:
                return point
    return None


def _drive(bot, nav, tick, seconds, destination, speed):
    yaw = bots.turned(
        bot.yaw, bots.heading(destination[0] - bot.position[0], destination[2] - bot.position[2]),
        bots.TURN_RATE * seconds)
    before = bot.position
    outcome = nav.mover.step_pose(Command(tick, yaw=round(yaw), pitch=round(bot.pitch)), seconds, tick,
                                  move_to=(destination, speed))
    bot.position, bot.velocity, bot.yaw = tuple(nav.mover.position), nav.mover.velocity, float(yaw)
    dx, dz = destination[0] - before[0], destination[2] - before[2]
    distance = math.hypot(dx, dz)
    bot.held = bots.throttles(bot.yaw, (dx / distance, dz / distance)) if distance else (0, 0)
    bot.last_held, bot.input_tick, bot.keys = bot.held, tick, True
    bot.moved_tick = tick
    nav.stuck = nav.stuck + 1 if math.dist(before, bot.position) < 0.001 else 0
    return outcome


def _settle_step(bot, nav, tick, seconds):
    """Advance a fall without movement input, then invalidate the old floor search."""
    nav.last_tick = tick
    nav.mover.step_pose(Command(tick, yaw=round(bot.yaw), pitch=round(bot.pitch)), seconds, tick)
    bot.position, bot.velocity = tuple(nav.mover.position), nav.mover.velocity
    bot.pace, bot.held, bot.keys = 0.0, (0, 0), False
    bot.moved_tick = tick
    if not nav.mover.airborne:
        nav.goal, nav.search = None, None


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
    bot.last_held, bot.input_tick, bot.keys = bot.held, tick, not bot.dead
    bot.moved_tick = tick


@dataclass
class Pursuit:
    search: Search | None = None
    retry_at: int = 0
    wall_entities: tuple = ()


def stop_pursuit(bot):
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
        stop_pursuit(bot)
        pursuit.wall_entities = entities
        pursuit.retry_at = 0


def update(fight, bot, target, reach):
    pursuit = bot.pursuit
    if (getattr(bot, "wall_mover", None) is not None or fight.is_stunned(bot)
            or fight.junkrat.rooted(bot.entity, fight.tick)):
        stop_pursuit(bot)
        return
    if target is None:
        stop_pursuit(bot)
        pursuit.retry_at = 0
        return
    search = pursuit.search
    if search is not None and (target.entity != search.target.entity
                               or math.dist(target.feet, search.target.feet) > GRID):
        stop_pursuit(bot)
        search = None
        pursuit.retry_at = 0
    if search is None:
        if fight.tick < pursuit.retry_at:
            return
        search = pursuit.search = begin(bot.position, target, reach)
    if search.done:
        if bot.target is None and (not bot.route or bot.index == len(bot.route) - 1):
            stop_pursuit(bot)
            pursuit.retry_at = fight.tick + RETRY_FRAMES
        return
    if advance(search, fight.match.collision_world, mover_data(bot.body), reach, PLAN_SLICE,
               contact=bot.hero == assassin.HERO) and search.path:
        bot.route = list(search.path)
        bot.index, bot.wait, bot.last_tick = -1, 0.0, fight.tick
