"""The training bots: what a map's AI spawners make (data/ai_spawners_174.json, made by
tools/extract_ai_spawners.py from each map's ENTITY chunk).

The Practice Range has 15 spawners: 11 Training Bots on the red team and 4 Friendly Bots on the blue one
(retail's OW2 Practice Range showed 15 bots too, each with a movement record in every frame, idle or not). A
spawner names a hero, a team and the graphs that drive the bot:

- 0B8E walks a route. Its variable 6947 lists AI hint points, variable 39 is the route index (2 to start).
  It waits 2 s, then 1 s (two StateWaits), sets #39 = (#39 + 1) % count(#6947), and walks to the point
  #6947[#39] (STU_ECAEE401 with the move-to 194D744E and the face-to AB9B30EF); after a walk it waits 1 s
  (the second wait) and goes on.
- 0B92 alternates firing and waiting phases of 1.75 to 2.5 s within 0.4 m of spawn, and walks back when
  displaced. Its 0013.025 reset branch delays firing eligibility by 2.75 s. The server advances the
  weapon body and its volleys. Production AI selection and input delivery still need the native hooks.
- No graph: the bot stands.

A bot is a hero body without a player: the hero's m_gameplayEntity, its health (the body's override of
the common body graph 0033's variable 199: Training Bots 200, Friendly Bots 225), the team and the hero, as
a player's body (match.Match._bot_create). The server moves it itself and sends its state as another
player's body's. The client's code for the AI's move-to, face-to and fire states is a stub (one shared
class, vtable 0x7FF78B6BC800): they ran on the server only.

How it walks was measured on the retail OW2 Practice Range (its live traffic), whose four
walking bots walk the far routes of these spawners (MEASURED unless marked):
- It sets off when its wait ends, whichever way it faces (backward or sideways at first), its keys held
  all the way (throttles of 127), and turns toward the way it walks at 60 degrees a second (TURN_RATE)
  until 0.5 s after it lets go of the keys (TURN_AFTER).
- It speeds up at its body's ground acceleration times run speed to its run speed, and lets go of the
  keys where its ground friction (base times run speed) stops it on its point: 4.5 m/s^2, 4.5 m/s and
  67.5 m/s^2, the Training Bot body's values (DATA: data/game_movers_174.json, 1403). On a ramp its speed
  is along the floor.
- The next walk starts WALK_GAP after the last tick with keys (the graph's 1 s wait and its state
  changes); the first one FIRST_WAIT after it spawns (DATA, not in the captures).
- unverified: it stops on its point. Retail stopped 0.05-0.45 m short of the 1.74 points, but its own points
  may be as far from them.
- The far runners (hero 016E) walk with their own body 14ED's values (DATA, 1.74): 6 m/s, up at 66 m/s^2
  (ground acceleration 11), down at 39 m/s^2 (friction base 6.5), the fast targets 1.74 made them. OW2
  put ordinary Training Bots on these routes (4.5 m/s); the server keeps 1.74's. The routes are 1.74's
  (DATA); OW2 changed three of them.
It follows the floor and sweeps its standing capsule through the same static collision as players.
For a blocked straight leg, it tries another authored hint, then a short lateral detour along
the same floor. The original server's navigation is unverified. When no floor can be sampled,
its requested feet height follows the line between its last point and the next one.
"""

import json
import math
from functools import cache
from threading import Lock
from types import MappingProxyType
from weakref import WeakKeyDictionary

from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.commands import Command
from ow174.game.mover import FELL, GRAVITY_SCALE, IN_AIR, Mod, Mover, MoverData, encode_spring, modded, mover_data, loaded_mover_table, statescript_mods
from ow174.game.ice_wall import route_stride
from ow174.game.world import NO_INPUT, Movement
from ow174.paths import DATA_DIR

from ow174.game import contacts
from ow174.game.collision import NOT_GROUND, World, mover_category
from ow174.game.mover import ROUNDS, capsule_dimensions

SPAWNERS_PATH = DATA_DIR / "ai_spawners_174.json"
PATROL = 0x0580000000000B8E
FIRING = 0x0580000000000B92
ROUTE = "0x0D80000000001B23"  # #6947, the route's hint points
INDEX = "0x0D80000000000027"  # #39, the route index
FIRST_WAIT = 2.0 + 1.0  # seconds: the patrol graph's two waits before its first walk (DATA)
WALK_GAP = 1.2  # seconds from the last tick with keys held to the next walk (75 ticks)
ARRIVED = 0.05  # metres from the point at which a walk needs no keys
STEP_UP = 0.6  # metres above its feet the floor under a walking bot may be (a step, a ramp)
DROP = 4.0  # metres below its feet the floor may be
MAX_SLOPE = 1.0  # rise per metre up to which the speed is along the floor (the bodies' max_slope, 45 deg)
BOT_ENTITY = 0xC0001000  # bot n is BOT_ENTITY + n: entity id type 2, like the game mode entity
FORWARD = 127  # the throttle byte of a key held all the way
YAW_UNITS = 65536  # s16 angle units per turn
TURN_RATE = YAW_UNITS / 6  # yaw units per second it turns: 60 degrees (174.76 units per tick)
TURN_AFTER = 0.5  # seconds it turns on after it lets go of the keys (28 to 33 ticks)
FAR_RUNNER = 0x02E000000000016E  # the far runners' hero (body 14ED)
CATCH_UP = 625  # ticks a late step walks at most (10 s)
EPSILON = 1e-9
MAX_DETOUR_FLOOR_STEPS = 64
# A walking bot's state goes in every frame, as retail sent them. A standing one changes nothing, so its
# record goes once a second (staggered by bot), and in every frame for a second after it stopped.
SETTLE_TICKS = 63  # about a second of 16 ms ticks
_PATROL_PLANS = WeakKeyDictionary()
_PATROL_PLANS_LOCK = Lock()


@cache
def table() -> dict:
    if not SPAWNERS_PATH.is_file():
        return {"heroes": {}, "maps": {}}
    return json.loads(SPAWNERS_PATH.read_text(encoding="utf-8"))


def s16(units):
    return (units + 0x8000) % YAW_UNITS - 0x8000


def yaw_of(rotation) -> int:
    """A rotation about +Y (x, y, z, w) as the commands' yaw units (match._rotation's inverse)."""
    _, y, _, w = rotation
    return s16(round(math.atan2(y, w) * YAW_UNITS / math.pi))


def heading(dx: float, dz: float) -> int:
    """The yaw that faces (dx, dz): the mover's forward is (sin, cos) of the yaw (x, z)."""
    return s16(round(math.atan2(dx, dz) * YAW_UNITS / (2 * math.pi)))


def turned(yaw, toward, units):
    """`yaw` turned toward `toward` by at most `units`, the short way."""
    left = s16(toward - yaw)
    return s16(yaw + max(-units, min(units, left)))


def throttles(yaw, direction: tuple) -> tuple[int, int]:
    """(right, forward) bytes of a walk along `direction` (x, z) by a body facing `yaw`: forward is
    (sin, cos) of the yaw, right (-cos, sin), as in the mover."""
    angle = yaw * 2 * math.pi / YAW_UNITS
    dx, dz = direction
    forward = dx * math.sin(angle) + dz * math.cos(angle)
    right = -dx * math.cos(angle) + dz * math.sin(angle)
    return round(right * FORWARD), round(forward * FORWARD)


def planar_distance(a, b) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


DEAD = 0x2000  # the movement state's dead flag (combat.py): the 3P animation's dead pose


class Bot:
    def __init__(self, entity: int, spawner: dict, hero: dict, hints: dict, *, mover: MoverData | None = None, free_for_all: bool = False) -> None:
        self.entity = entity
        self.spawner, self.hero_data, self.hints = spawner, hero, hints  # for a respawn (respawned)
        self.dead = False  # killed (combat.py): it stands still with the dead flag until it is made again
        self.combat_owner = None  # Original actor-backed deployable's player controller.
        self.combat_geometry = None  # Optional height/radius/eye/critical tuple.
        self.retain_corpse = False  # opt-in manual NPC lifetime; explicit removal owns retirement
        self.attack_target = None  # engaged sandbox NPC's current player controller
        self.attack_input = False  # last queued primary input, including edges not consumed by the VM yet
        self.attack_mode = "primary"
        self.reposition_body = None  # Original authored search's engaged body.
        self.pitch = 0.0
        self.body_script = None  # optional statescript from the match's prepared graph bundle
        self.owner = 0
        self.pet_owner = None
        self.source_body = 0
        self.control_mover: Mover | None = None
        self.control_buttons = 0
        self.clock_offset = 0
        self.detached_bomb = False
        self.navigation = None  # explicit manual pursuit state, allocated on first movement
        self.records_from: int | None = None  # a respawned bot's first record tick (after its create)
        self.hero = int(spawner["hero"], 16)
        self.name = hero["name"]
        self.body = int(hero["body"], 16)
        self.team = spawner["team"]
        self.free_for_all = free_for_all
        self.category = mover_category(self.team, free_for_all)
        self.health = hero["health"]
        self.movement_data = mover  # Original explicit per-definition prepared input.
        values = self.movement_data or mover_data(self.body)
        self.radius, segment = capsule_dimensions(values, False)
        self.capsule_top = contacts.f32(segment + self.radius)
        self.pogo = contacts.f32(values.stand_pogo)
        self.physics = None
        self.physics_world = None
        self.speed = values.run_forward
        self.accel = values.accel_ground * values.run_forward  # m/s^2 up to full speed
        self.brake = values.decel_ground_base * values.run_forward  # m/s^2 down to a stop
        self.pace = 0.0  # its speed now
        self.direction = (0.0, 1.0)  # the way it walks now (x, z)
        self.position = tuple(spawner["position"])
        self.yaw = float(yaw_of(spawner["rotation"]))
        self.pitch = 0.0  # encoded command-angle units; explicit NPC controllers may aim vertically
        self.velocity = (0.0, 0.0, 0.0)
        self.impulse_mover: Mover | None = None  # external displacement, independent of route/control input
        variables = spawner.get("vars", {})
        roots = {int(g, 16) for g in spawner["graphs"]}
        self.firing = FIRING in roots
        route = variables.get(ROUTE, []) if PATROL in roots else []
        self.route = [tuple(hints[point]) for point in route if point in hints]
        self.index = variables.get(INDEX, 0)
        self.wait = FIRST_WAIT  # seconds to the next walk
        self.target: tuple | None = None
        self.waypoints: list[tuple] = []
        self.start = self.position  # where the walk to the target began
        self.keys = False  # it holds its movement keys
        self.turning = 0.0  # seconds it still turns after it let go of the keys
        self.held = (0, 0)  # the throttles of its last tick
        self.last_held = (0, 0)  # the throttles of the last tick it held the keys in
        self.input_tick: int | None = None  # the last tick it held the keys in
        self.moved_tick: int | None = None  # the last tick it moved or turned in
        self.last_tick: int | None = None

    def step(
        self, tick: int, seconds: float, collision=None, *,
        paused_until: int = 0, movement_mods: dict | None = None, pillars=(),
    ) -> None:
        """Walk the route up to `tick`, a tick being `seconds` long, on the map's collision if loaded."""
        if self.dead or self.control_mover is not None:
            return
        if not self.route and self.impulse_mover is None:
            if tick < paused_until:
                self._pause(tick)
                self.last_tick = tick
            return
        ticks = 1 if self.last_tick is None else tick - self.last_tick
        if ticks <= 0:
            return
        self.last_tick = tick
        pace_limits = (self.speed, self.accel, self.brake)
        if movement_mods:
            mods = [Mod(index, op, value, tick, priority=priority, key=key)
                    for key, (index, op, value, priority) in movement_mods.items()]
            values = modded(self.movement_data or mover_data(self.body), sorted(mods, key=Mod.order))
            speed = values.run_forward
            pace_limits = (speed, values.accel_ground * speed, values.decel_ground_base * speed)
        for at in range(tick - min(ticks, CATCH_UP) + 1, tick + 1):
            if self.impulse_mover is not None:
                self._impulse_step(at, seconds, collision)
            elif at < paused_until:
                self._pause(at)
            elif self.route:
                active = tuple(pillar for pillar in pillars
                               if pillar.created_tick <= at < pillar.expires_tick)
                self._tick(at, seconds, collision, pace_limits, active)

    def _trap_rooted(self, tick):
        component = getattr(self.body_script, "component", None)
        match = getattr(getattr(component, "world", None), "match", None)
        return match is not None and match.combat.junkrat.rooted(self.entity, tick)

    def apply_impulse(self, velocity, tick, collision, *, category=MOVER) -> bool:
        """Reconstructed manual-NPC impulse using its actual prepared body data.

        Authored modifier26 controls prepared manual bodies. Native force conversion,
        resistance services and AI/contact policy are unproven. The existing route
        and possessed-body consumers remain separate from this manual-body policy.
        """
        velocity = tuple(velocity)
        if len(velocity) != 3 or not all(math.isfinite(value) for value in velocity):
            raise ValueError("invalid external impulse")
        if self.dead or self._trap_rooted(tick):
            return False
        if self.control_mover is not None:
            self.control_mover.apply_impulse(velocity)
            self.velocity = self.control_mover.velocity
            return any(velocity)
        if self.spawner.get('manual_spawn') is not True:
            self._apply_legacy_impulse(velocity, tick, collision, category=category)
            return any(velocity)
        if (self.dead or self.spawner.get('manual_spawn') is not True or self.route
                or collision is None or (self.movement_data is None and self.body not in (loaded_mover_table() or {}))):
            return False
        mover = self.impulse_mover
        if mover is None:
            mover = Mover(self.position, s16(round(self.yaw)), collision,
                          self.movement_data or mover_data(self.body), category=category)
            mover.velocity = self.velocity
        mover.use(world=collision)
        if mover.category != category:
            mover.category, mover.body = category, None
        component = getattr(self.body_script, 'component', None)
        active = statescript_mods(component) if component is not None else {}
        # The first manual physics handoff reconstructs current modifier timing;
        # it must not discard Heavy1254's already active zero response.
        mover.track_mods(active, tick-1)
        from ow174.game import sniper_reposition

        reposition = sniper_reposition.reposition_state(self)
        if not any(mover.add_impulse(velocity, tick, carry=reposition is not None and reposition.active)):
            return False
        if self.impulse_mover is None:
            self.impulse_mover, self.last_tick = mover, tick
        self.velocity = mover.velocity
        self.moved_tick = tick
        return True

    def ensure_manual_mover(self, tick, collision, category):
        if (self.impulse_mover is None and not self.dead and not self.route
                and self.spawner.get('manual_spawn') is True and collision is not None
                and (self.movement_data is not None or self.body in (loaded_mover_table() or {}))):
            self.impulse_mover = Mover(self.position, s16(round(self.yaw)), collision,
                                       self.movement_data or mover_data(self.body), category=category)
            self.impulse_mover.velocity = self.velocity
            self.last_tick = tick
        return self.impulse_mover

    def _step_manual_body(self, tick, seconds, collision, category, forward, walls) -> None:
        mover = self.impulse_mover
        if collision is None:
            return  # collision readiness is normally guaranteed by Match
        ticks = 1 if self.last_tick is None else tick-self.last_tick
        if ticks <= 0:
            return
        self.last_tick = tick
        mover.use(world=collision)
        if mover.category != category:
            mover.category, mover.body = category, None
        self.held = (0, forward)
        if forward:
            self.last_held = self.held
            self.input_tick = tick
        before = (self.position, self.velocity)
        for at in range(tick-min(ticks, CATCH_UP)+1, tick+1):
            component = getattr(self.body_script, 'component', None)
            active = statescript_mods(component) if component is not None else {}
            mover.track_mods(active, at-1)
            command = Command(at, yaw=s16(round(self.yaw)), pitch=s16(round(self.pitch)), forward=forward)
            if walls is None or not walls.pieces:
                mover.step(command, seconds, at)
            else:
                before_pose = tuple(mover.position)
                mover.step(command, seconds, at, simulate_pose=lambda command, dt, tick: mover.step_pose(
                    command, dt, tick, ground_override=walls.ground(mover),
                ))
                walls.move_mover(mover, before_pose)
        self.position, self.velocity = tuple(mover.position), mover.velocity
        if (self.position, self.velocity) != before:
            self.moved_tick = tick

    def _apply_legacy_impulse(self, impulse, tick: int, collision, *, category: int) -> None:
        """Reconstructed external-force adapter using this body's actual mover data."""
        impulse = tuple(impulse)
        if len(impulse) != 3 or not all(math.isfinite(value) for value in impulse):
            raise ValueError("invalid external impulse")
        if self.dead:
            return
        if self.control_mover is not None:
            self.control_mover.apply_impulse(impulse)
            self.velocity = self.control_mover.velocity
            return
        if self.impulse_mover is None:
            if self.navigation is not None:
                from ow174.game import npc_navigation

                npc_navigation.stop(self)
            self.impulse_mover = Mover(self.position, round(self.yaw), collision,
                                       self.movement_data or mover_data(self.body), category=category)
            self.impulse_mover.velocity = self.velocity
            self.impulse_mover.last_command = Command(tick)
            self.last_tick = tick
        mover = self.impulse_mover
        mover.velocity = tuple(a + b for a, b in zip(mover.velocity, impulse, strict=True))
        if mover.velocity[1] > 0:
            mover.flags_word |= FELL
            mover.fall = 0.0
        self.velocity = mover.velocity
        self.pace, self.held = 0.0, (0, 0)
        self.moved_tick = tick

    def impulse(self, velocity: tuple, tick: int, collision=None, *, free_for_all=False) -> None:
        """Temporarily give external motion to the shared capsule solver.

        Route intent waits while displaced; control pauses do not erase physical momentum.
        Native NPC resistance/force ordering remains unproved.
        """
        if self.dead or self._trap_rooted(tick):
            return
        category = MOVER if free_for_all or self.team not in (0, 1) else (TEAM0, TEAM1)[self.team]
        if self.impulse_mover is None:
            carry = self.velocity
            if self.navigation is not None:
                from ow174.game import npc_navigation

                npc_navigation.stop(self)
            self.impulse_mover = Mover(self.position, round(self.yaw), collision, self.movement_data or mover_data(self.body),
                                       category=category)
            self.impulse_mover.velocity = carry
        else:
            self.impulse_mover.use(world=collision)
            if self.impulse_mover.category != category:
                self.impulse_mover.category = category
                self.impulse_mover.body = None
        self.impulse_mover.impulse(velocity)
        self.velocity = self.impulse_mover.velocity
        self.last_tick = tick
        self.pace = 0.0
        self.held = (0, 0)
        self.moved_tick = tick

    def _impulse_step(self, tick: int, seconds: float, collision) -> None:
        if (self.spawner.get('manual_spawn') is True and not self.route
                and (self.movement_data is not None or self.body in (loaded_mover_table() or {}))):
            component = getattr(self.body_script, "component", None)
            match = getattr(getattr(component, "world", None), "match", None)
            category = MOVER if match is None or match.game_map.free_for_all or self.team not in (0, 1) else (TEAM0, TEAM1)[self.team]
            walls = match.combat.walls if match is not None else None
            finite_only = (not self.impulse_mover.external_impulse
                           and any(key[0] == -3 for key in self.impulse_mover.mods))
            # Both route and navigation schedulers call this for one labelled tick.
            self.last_tick = tick - 1
            self._step_manual_body(tick, seconds, collision, category, 0, walls)
            mover = self.impulse_mover
            if ((self.attack_target is not None or finite_only) and not mover.airborne and not any(mover.velocity)
                    and not mover.pending_frame_modifiers(tick)):
                # Preserve the existing explicit navigation owner once displacement settles.
                self.impulse_mover = None
                self.start = self.position
            return
        mover = self.impulse_mover
        mover.use(world=collision)
        mover.step_pose(Command(tick, yaw=round(self.yaw), pitch=round(self.pitch)), seconds, tick)
        self.position, self.velocity = tuple(mover.position), mover.velocity
        self.held = (0, 0)
        self.moved_tick = tick
        if (not mover.flags_word & IN_AIR and not any(mover.velocity)
                and not mover.pending_frame_modifiers(tick)):
            self.impulse_mover = None
            self.start = self.position  # a resumed route interpolates from the displaced position

    def _pause(self, tick: int) -> None:
        self.pace = 0.0
        self.velocity = (0.0, 0.0, 0.0)
        self.held = (0, 0)
        self.moved_tick = tick

    def _tick(self, tick: int, dt: float, collision, pace_limits: tuple, pillars=()) -> None:
        before = (self.position, round(self.yaw))
        self.held = (0, 0)
        if not self.keys:
            self.wait -= dt
            if self.target is None and self.wait <= EPSILON:
                self.index = (self.index + 1) % len(self.route)
                self.target = self.route[self.index]
                self.start = self.position
                if isinstance(collision, World):
                    detour = self._leg_detour(collision)
                    if detour:
                        self.waypoints = [*detour[1:], self.target]
                        self.target = detour[0]
                self.keys = True
                self.turning = 0.0
        if self.keys:
            self._walk(tick, dt, collision, pace_limits, pillars)
        else:
            if self.target is not None:
                self._slide(dt, collision, pace_limits[2], pillars)
            if self.turning > EPSILON:
                self.turning -= dt
                self._turn(dt)
        if (self.position, round(self.yaw)) != before:
            self.moved_tick = tick

    def _walk(self, tick: int, dt: float, collision, pace_limits: tuple, pillars=()) -> None:
        x, _, z = self.position
        tx, _, tz = self.target
        distance = math.hypot(tx - x, tz - z)
        if distance <= ARRIVED and self.waypoints:
            self.target = self.waypoints.pop(0)
            self.start = self.position
            tx, _, tz = self.target
            distance = math.hypot(tx - x, tz - z)
        if distance <= ARRIVED:
            self.keys = False
            self.wait = WALK_GAP
            self._stand()
            return
        self.direction = ((tx - x) / distance, (tz - z) / distance)
        self._turn(dt)
        speed, accel, brake = pace_limits
        self.pace = min(speed, self.pace + accel * dt)
        self.held = self.last_held = throttles(self.yaw, self.direction)
        self.input_tick = tick
        stride, slide = self.pace * dt, self._slide_length(dt, brake)
        if not self.waypoints and distance - stride <= slide:
            # its last tick with keys: from here its friction slides it onto the point
            stride = max(0.0, distance - slide)
            self.keys = False
            self.wait = WALK_GAP
            self.turning = TURN_AFTER
        self._advance(stride, distance, dt, collision, pillars)

    def _slide(self, dt: float, collision, brake: float, pillars=()) -> None:
        """No keys: its friction stops it, never past its point."""
        self.pace = max(0.0, self.pace - brake * dt)
        x, _, z = self.position
        tx, _, tz = self.target
        distance = math.hypot(tx - x, tz - z)
        moving = self.pace > 0.0 and distance > EPSILON
        if moving and self._advance(self.pace * dt, distance, dt, collision, pillars) < distance - EPSILON:
            return
        self._stand()

    def _stand(self) -> None:
        self.pace = 0.0
        self.velocity = (0.0, 0.0, 0.0)
        self.target = None
        self.waypoints.clear()

    def _slide_length(self, dt: float, brake: float) -> float:
        """How far its friction slides it from its speed now, tick by tick."""
        slow = brake * dt
        if slow <= 0:
            return 0.0
        length, pace = 0.0, self.pace - slow
        while pace > 0.0:
            length += pace * dt
            pace -= slow
        return length

    def _turn(self, dt: float) -> None:
        self.yaw = turned(self.yaw, heading(*self.direction), TURN_RATE * dt)

    def _advance(self, stride: float, distance: float, dt: float, collision, pillars=()) -> float:
        """Move `stride` along the floor the way it walks, not past its point (`distance` away); the
        ground distance gone."""
        x, y, z = self.position
        dx, dz = self.direction
        probe = min(stride, distance)
        probe_y = self._floor(x + dx * probe, y, z + dz * probe, collision)
        rise = probe_y - y
        if 0.0 < abs(rise) <= MAX_SLOPE * probe:
            stride *= probe / math.hypot(probe, rise)
        if pillars:
            intended = min(stride, distance)
            clipped = route_stride(self, intended, pillars)
            if clipped < intended:
                stride = clipped
                self.pace = clipped / dt
                self.keys = True
        # Snapping to the authored target can use different coordinates from the probe.
        reuse_probe = stride == probe and stride < distance
        if stride >= distance:
            stride = distance
            nx, nz = self.target[0], self.target[2]
        else:
            nx, nz = x + dx * stride, z + dz * stride
        ny = probe_y if reuse_probe else self._floor(nx, y, nz, collision)
        if isinstance(collision, World):
            body = self._engine_body(collision)
            body.teleport(self._body_position(self.position))
            requested = self._body_position((nx, ny, nz))
            displacement = contacts.sub(requested, body.position)
            expected = contacts.add(body.position, displacement)
            moved, _, _ = body.move(displacement, contacts.f32(dt), ROUNDS)
            if moved != expected:
                nx, by, nz = moved
                ny = by - self.pogo
                stride = max(0.0, (nx - x) * dx + (nz - z) * dz)
        self.position = (nx, ny, nz)
        self.velocity = ((nx - x) / dt, (ny - y) / dt, (nz - z) / dt)
        return stride

    def _floor(self, x: float, y: float, z: float, collision) -> float:
        """The height its feet take at (x, z): the floor of the collision, else the height between the
        walk's start and its target at the share of the way it has gone."""
        bounds = (x, z, y + STEP_UP, y - DROP)
        if isinstance(collision, World):
            floor = self._walkable_floor(collision, *bounds)
            if floor is not None:
                return floor
        elif collision is not None and (ground := collision.ground(*bounds)) is not None:
            return ground.y
        sx, sy, sz = self.start
        tx, ty, tz = self.target
        whole = math.hypot(tx - sx, tz - sz)
        gone = 1.0 if whole <= 0.0 else min(1.0, math.hypot(x - sx, z - sz) / whole)
        return sy + (ty - sy) * gone

    def walking(self) -> bool:
        return self.target is not None

    def died(self, tick: int) -> None:
        """Stop combat and publish death, preserving an airborne retained corpse's fall."""
        from ow174.game import npc_navigation

        if self.attack_mode == "reposition":
            from ow174.game import sniper_reposition

            sniper_reposition.stop(self, tick)
            self.attack_mode = "primary"
        npc_navigation.stop(self, land=self.retain_corpse)
        self.dead = True
        if self.spawner.get('manual_spawn') is True:
            self.firing = False
        self.impulse_mover = None
        if hasattr(self, "wall_mover"):
            del self.wall_mover
        self.attack_target = None
        self.attack_input = False
        self.keys = False
        self.held = (0, 0)
        self.target = None
        self.waypoints.clear()
        self.moved_tick = tick

    def record_due(self, tick: int, every: int = 1) -> bool:
        """Whether this frame carries its state: each frame while it walks and SETTLE_TICKS after, else
        about once a second. `every` is the ticks between the frames sent (match.SEND_EVERY), so that a
        resting bot's turn falls on a tick that sends."""
        if self.records_from is not None and tick < self.records_from:
            return False
        if self.walking() or (self.moved_tick is not None and tick - self.moved_tick < SETTLE_TICKS):
            return True
        period = max(1, SETTLE_TICKS // every)
        return (tick // every) % period == self.entity % period

    @property
    def movement_flags(self):
        """Expose the ordinary body's existing movement output without new state."""
        return self.movement(self.last_tick or 0).flags

    def movement(self, tick: int) -> Movement:
        """Its state for the clients, as a player body's (match.Player.movement). The 3P animation takes
        the throttles as the input direction against the body's yaw and its speed from the velocity:
        while it walks they point the way it walks, seen from its yaw, at full throttle
        (retail sent 126-127 from the first tick of a walk to its last); after, they are 0 and +36
        (`input_frame`) names the last tick with keys held, its throttles in +34/+35."""
        if self.held != (0, 0):
            input_frame = None  # keys held now: the state's own frame
        elif self.input_tick is None:
            input_frame = NO_INPUT
        elif self.input_tick >= tick:
            # +36 must go as the state's own frame (one 0 bit): the client reads +34/+35 only when the
            # decoded +36 differs from +8, and the same frame written out would leave them in the bits,
            # the rest of the record read out of step (the bot jumped away and back; the client crashed
            # in the movement reader).
            input_frame = None
        else:
            input_frame = self.input_tick
        flags = DEAD if self.dead else 0
        air_ticks = 0
        manual = self.spawner.get('manual_spawn') is True
        spring = (0, 0)
        spring_speed = 0.0
        if self.impulse_mover is not None and not self.dead:
            flags |= self.impulse_mover.flags_word
            air_ticks = self.impulse_mover.air_ticks
            if manual:
                spring = self.impulse_mover.movement_spring()
                spring_speed = self.impulse_mover.spring_speed
        elif self.navigation is not None and (not self.dead or self.retain_corpse):
            mover = self.navigation.mover
            airborne = mover.flags & IN_AIR
            flags |= airborne
            air_ticks = mover.air_ticks if airborne else 0
            if manual:
                spring = mover.movement_spring()
                spring_speed = mover.spring_speed
        elif manual:
            # Wire step zero decodes to the bottom of the spring range.
            spring = encode_spring((0.0, 0.0), *(self.movement_data or mover_data(self.body)).spring_range())
        return Movement(
            self.position,
            s16(round(self.yaw)),
            s16(round(self.pitch)),
            self.velocity,
            flags,
            throttles=self.held,
            input_frame=input_frame,
            input_throttles=self.last_held,
            air_ticks=air_ticks,
            gravity=(self.impulse_mover.gravity_scale if manual and self.impulse_mover is not None
                     and not self.dead else GRAVITY_SCALE),
            spring=spring,
            spring_speed=spring_speed,
        )

    def _engine_body(self, collision: World) -> contacts.CharacterBody:
        if self.physics is None or self.physics_world is not collision:
            self.physics = contacts.CharacterBody(
                self.radius, self.capsule_top, world=collision.engine, category=self.category,
            )
            self.physics_world = collision
        return self.physics

    def _body_position(self, feet) -> tuple:
        x, y, z = feet
        return contacts.v3(x, y + self.pogo, z)

    def _plan_key(self, start, goal) -> tuple:
        return tuple(self.route), self.category, self.radius, self.capsule_top, self.pogo, start, goal

    def _leg_detour(self, collision: World) -> tuple:
        start = tuple(self.spawner["position"])
        first_index = (self.spawner.get("vars", {}).get(INDEX, 0) + 1) % len(self.route)
        if self.position != start or self.index != first_index:
            start = self.route[self.index - 1]
            if planar_distance(self.position, start) > ARRIVED or abs(self.position[1] - start[1]) > STEP_UP:
                return ()
        with _PATROL_PLANS_LOCK:
            plans = _PATROL_PLANS.get(collision)
        if plans is not None:
            planned = plans.get(self._plan_key(start, self.target))
            if planned is not None:
                return planned
        return self._detour(collision, start, self.target)

    def _detour(self, collision: World, start, goal) -> tuple:
        """Check authored waypoints before testing a bounded detour to each side."""
        length = planar_distance(start, goal)

        def same_floor(point):
            return abs(point[1] - start[1]) <= ARRIVED

        if length <= ARRIVED or not same_floor(goal):
            return ()
        points = contacts.CharacterBody(self.radius, self.capsule_top).local_points()
        diameter = 2.0 * self.radius

        def clear(a, b):
            return contacts.scene_sweep(
                collision.engine, self._body_position(a), self._body_position(b),
                points, self.radius, self.category,
            ) is None

        def walkable(a, b, tolerance):
            return self._floor_path(collision, a, b, diameter, tolerance) and clear(a, b)

        if clear(start, goal):
            return ()
        candidates = sorted(
            (
                point for point in self.route
                if same_floor(point) and planar_distance(start, point) > ARRIVED
                and planar_distance(point, goal) > ARRIVED
            ),
            key=lambda point: planar_distance(start, point) + planar_distance(point, goal),
        )
        for point in candidates:
            if walkable(start, point, STEP_UP) and walkable(point, goal, STEP_UP):
                return (point,)

        sx, sy, sz = start
        gx, gy, gz = goal
        dx, dz = gx - sx, gz - sz
        scale = diameter / length
        ox, oz = dz * scale, -dx * scale
        for sign in (1, -1):
            near = (sx + sign * ox, sy, sz + sign * oz)
            far = (gx + sign * ox, gy, gz + sign * oz)
            segments = ((start, near), (near, far), (far, goal))
            if all(walkable(a, b, ARRIVED) for a, b in segments):
                return (near, far)
        return ()

    def _floor_path(self, collision: World, start, end, spacing: float, tolerance: float) -> bool:
        dx, dy, dz = (b - a for a, b in zip(start, end, strict=True))
        steps = math.ceil(math.hypot(dx, dz) / spacing)
        if not 0 < steps <= MAX_DETOUR_FLOOR_STEPS:
            return False
        sx, sy, sz = start
        for i in range(steps + 1):
            t = i / steps
            x, y, z = sx + t * dx, sy + t * dy, sz + t * dz
            if self._walkable_floor(collision, x, z, y + tolerance, y - tolerance) is None:
                return False
        return True

    def _walkable_floor(
        self, collision: World, x: float, z: float, top: float, bottom: float,
    ) -> float | None:
        ground = collision.ground(x, z, top, bottom, self.category)
        return ground.y if ground is not None and not ground.surface & NOT_GROUND else None









def respawned(bot: Bot, entity: int, records_from: int) -> Bot:
    """The bot made again at its spawner under a new entity id, its records from `records_from` on."""
    fresh = Bot(entity, bot.spawner, bot.hero_data, bot.hints, mover=bot.movement_data, free_for_all=bot.free_for_all)
    fresh.records_from = records_from
    return fresh


def make(map_guid: int, *, free_for_all: bool = False) -> list[Bot]:
    """The bots of a map: one per AI spawner that needs no mode identifier (the Practice Range's need
    none; the Tutorial's do)."""
    data = table()
    entry = data["maps"].get(f"0x{map_guid:016X}")
    if entry is None:
        return []
    bots = []
    for spawner in entry["spawners"]:
        hero = data["heroes"].get(spawner["hero"])
        if hero is None or spawner["identifier"] is not None:
            continue
        bots.append(Bot(BOT_ENTITY + len(bots), spawner, hero, entry["hints"], free_for_all=free_for_all))
    return bots


def prepare_patrols(map_guid: int, collision: World) -> None:
    """Plan fixed patrol legs on the collision loader before the World becomes available to matches."""
    plans = {}
    for free_for_all in (False, True):
        for bot in make(map_guid, free_for_all=free_for_all):
            route = bot.route
            if not route:
                continue
            legs = [(bot.position, route[(bot.index + 1) % len(route)])]
            legs.extend(zip(route, [*route[1:], route[0]], strict=True))
            for start, goal in legs:
                key = bot._plan_key(start, goal)
                if key not in plans:
                    plans[key] = bot._detour(collision, start, goal)
    with _PATROL_PLANS_LOCK:
        _PATROL_PLANS[collision] = MappingProxyType(plans)
