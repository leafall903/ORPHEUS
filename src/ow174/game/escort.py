"""Server-owned progress for a single custom Escort round."""

import bisect
import math
from copy import copy
from dataclasses import dataclass
from enum import IntEnum

from ow174.game import collision, combat, contacts
from ow174.game.content import FIRST_ATTACKING_TEAM, FIRST_DEFENDING_TEAM, EscortMap
from ow174.game.script import expr, graph
from ow174.game.statescript import InstanceWire, StateWire, owner_delta, remote_delta, remote_full_frame
from ow174.game.world import ANGLE_UNITS, Movement

DEFENDERS, ATTACKERS = FIRST_DEFENDING_TEAM, FIRST_ATTACKING_TEAM
SETUP_SECONDS = 45.0  # 0F4A action 294
OVERTIME_SECONDS = 5.0  # 0F4A action 430
PUSH_RADIUS = 5.0
PUSH_HEIGHT = 4.0  # feet on the tall cart or the uphill side can escort it
PUSH_SPEED = (0.0, 1.4, 1.65, 1.9)
ROLLBACK_DELAY = 10.0
ROLLBACK_SPEED = 0.3  # 1225 state 6 with the default payload speed multiplier
SAMPLES_PER_SEGMENT = 32
RAIL_LOOKAHEAD = 0.75
COUNTDOWN = 1303
HUD_CLOCK = 0x0517  # 0F4A state 168 copies the countdown to this member of 59DC
OBJECTIVE_KIND = 10545
CHECKPOINT_INDEX = 0x2934
PAYLOAD_OBJECTIVE = expr.Asset(0x0D80000000002933)
MODE_GRAPH = combat.PVP_PHASE_GRAPH
MODE_MESSAGES = combat.MODE_PHASE_RECEIVER
MODE_HUD_VALUES = 0x1B4A  # 0CBB state 50 registers the native HUD expressions
PAYLOAD_GRAPH = 0x1225
PAYLOAD_DIRECTION = 0x01B3  # entity: forward 1, idle 0, rollback -1
PAYLOAD_DISTANCE = 0x01DF
PAYLOAD_ENABLED = 0x0270  # selects Completed instead of Locked when the cart is inactive
PAYLOAD_ACTIVE = 0x02BB  # 1225 state 119 selects the route bar when this entity flag is set
PAYLOAD_PUSHERS = 0x1A9F  # instance: attacker bodies used for PusherCount and MovementSpeed
PAYLOAD_BLOCKERS = 0x1AA0  # instance: defender bodies used with the attacker list for IsContested
PAYLOAD_CHECKPOINT_FRACTIONS = 0x1D9E  # instance array read by 1225 state 119
PAYLOAD_ROUTE_LENGTH = 0x1D9F
PAYLOAD_FINISH_FRACTION = 0x1EC7  # the full route in a single round
DOOR_GRAPH = 0x0D32
BARRIER_GRAPH = 0x0FA8
ROOM_OVERLAY_GRAPH = 0x0D73
MAP_ENTITY = 0x80000000
ACTIVE_HEADING = (0x0DE00000000000F7, 0x0DE00000000000F5)  # 1FEB: stop / escort the payload
MATCH_LIVE = 18  # 0F4A asserts 0694, replacing the preparation assertion (0701)
ESCORT_HUD = 111  # 0F4A's 08D7 objective message, also consumed by the scoreboard
HUD_PROGRESS = 112  # transfers the cart's 009A into the mode's 349C ForCode value
HUD_PAYLOAD = 160  # native cart-reference stack that registers the objective bindings
HUD_CART_REFERENCE = 0x4F21  # instance value exported as ForCode key 3267 by state 95
HUD_CART_CHANGED = 508  # 0F4A action fired when the cart reference becomes valid
HUD_ROUND_DATA = 0x59DC  # 0F4A state 168 supplies the round context read by 1FEB
HUD_OBJECTIVE_KIND = 171  # publishes the current checkpoint type for 1B4A's progress reader
HUD_PROGRESS_BINDINGS = (105, 110)  # 0F4A ForCode exports 349C (section) and 34F3 (total)

DOOR_OPEN_ANIM, DOOR_CLOSE_ANIM = 0, 1
DOOR_OPEN_ALIAS, DOOR_CLOSE_ALIAS = 5, 8


class Phase(IntEnum):
    # 0F4A branches on mode phase variable 59.
    WAITING = 0
    ASSEMBLE = 1
    SETUP = 2
    ACTIVE = 3
    COMPLETE = 5


class DoorPose(IntEnum):
    CLOSED = 0
    OPENING = 1
    OPEN = 2


@dataclass(frozen=True)
class DoorState:
    pose: DoorPose
    states: frozenset[int] = frozenset()
    starts_at: float = 0.0
    ends_at: float = 0.0


class EscortCollision:
    """The cart and the active spawn-room collision in one match's broadphase."""

    def __init__(self, base: contacts.StaticWorld, geometries: tuple) -> None:
        self.base = base
        self.geometries = geometries
        self.pose = None
        self.frame = None
        self.shapes = ()
        self.cart = contacts.StaticWorld([])
        self.room_shapes = ()

    def place(self, position: tuple[float, float, float], yaw: int, pitch: int) -> None:
        pose = position, yaw, pitch
        if self.pose == pose:
            return
        self.pose = pose
        y, p = yaw / ANGLE_UNITS / 2, pitch / ANGLE_UNITS / 2
        rotation = contacts.quaternion_product(
            (0.0, math.sin(y), 0.0, math.cos(y)), (math.sin(p), 0.0, 0.0, math.cos(p)),
        )
        self.frame = position, rotation
        self.shapes = tuple(contacts.StaticShape(shape, self.frame, len(self.base.shapes) + index)
                            for index, shape in enumerate(self.geometries))
        self.cart = contacts.StaticWorld(self.shapes)

    def ground(self, x, z, top, bottom, category):
        if not self.shapes or top <= bottom:
            return None
        hit = contacts.scene_sweep(self.cart, (x, top, z), (x, bottom, z), ((0, 0, 0),), 0.0, category)
        if hit is None:
            return None
        _, point, normal, shape, triangle = hit
        surface = shape.geometry.surfaces[triangle] if triangle >= 0 else 0
        if normal[1] <= 0.0:
            return None
        y = point[1] + (normal[0] * (point[0] - x) + normal[2] * (point[2] - z)) / normal[1]
        return collision.Ground.at((x, y, z), normal, surface) if bottom <= y <= top else None

    def rider(self, mover):
        if mover.airborne:
            return None
        x, y, z = mover.position
        support = self.ground(x, z, y + 0.12, y - 0.16, mover.category)
        if support is None or support.normal[1] <= mover.slope_cos or support.surface & collision.NOT_GROUND:
            return None
        origin, rotation = self.frame
        return contacts.unrotate(rotation, contacts.sub(mover.position, origin))

    def carry(self, mover, local, dt: float) -> None:
        origin, rotation = self.frame
        target = contacts.add(origin, contacts.rotate(rotation, local))
        mover.carry(contacts.sub(target, mover.position), dt)

    def query(self, low, high) -> list:
        return self.base.query(low, high) + [
            shape for shape in self.shapes + self.room_shapes
            if contacts.overlaps(low, high, shape.fat_low, shape.fat_high)
        ]


@dataclass(frozen=True)
class ModeState:
    entity_vars: dict
    instance_vars: dict
    states: frozenset[int]

    @property
    def phase_states(self) -> tuple[int, ...]:
        # 0CBB registers ForCode value 2123 and its accompanying phase flag.
        phase = self.instance_vars[59][0]
        if phase == Phase.ASSEMBLE and combat.ASSEMBLE_HEROES_STATE not in self.states:
            return ()
        return {
            Phase.WAITING: (12, 51),
            Phase.ASSEMBLE: (1, 52),
            Phase.SETUP: (10, 53),
            Phase.ACTIVE: (2, 54),
            Phase.COMPLETE: (3, 55),
        }[phase]

    def frame(
        self, previous: "ModeState | None", *, cart_changed: int | None = None,
        progress_cmfd: int | None = None,
    ):
        mode, messages = graph.graph(MODE_GRAPH), graph.graph(MODE_MESSAGES)
        waiting = self.instance_vars[59][0] == Phase.WAITING
        graphs = {1: mode, 2: messages}
        if previous is None:
            states = {state: StateWire(mode.state(state).cls, True) for state in self.states}
            # 0CBB translates the mode's persistent phase messages for the client.
            receivers = {state: StateWire(messages.state(state).cls, True)
                         for state in (0, 4, 5, 9, 11, 56, *self.phase_states)}
            instances = [InstanceWire(1, mode, vars=self.instance_vars, states=states),
                         InstanceWire(2, messages, parent=(1, 12), states=receivers)]
            hud_values = graph.graph(MODE_HUD_VALUES)
            instances.append(InstanceWire(5, hud_values, parent=(2, 50), states={
                0: StateWire(hud_values.state(0).cls, True),
            }))
            graphs[5] = hud_values
            # Client-only 0F4A state 11 creates 1FEB and its common HUD descendants. Listing
            # them here would create a second HUD and replace the payload's parent handle.
            if waiting:
                hud = combat.waiting_hud(3)
                instances.append(hud)
                graphs[hud.id] = hud.graph
            return remote_full_frame(instances, self.entity_vars, graphs)
        states = {state: StateWire(mode.state(state).cls, state in self.states)
                  for state in self.states ^ previous.states}
        if progress_cmfd is not None:
            # These authored bindings have m_states entries and no remote-sync indexes.
            states.update({state: StateWire(mode.state(state).cls, True) for state in HUD_PROGRESS_BINDINGS})
        entity_vars = {key: value for key, value in self.entity_vars.items()
                       if previous.entity_vars.get(key) != value}
        instance_vars = {key: value for key, value in self.instance_vars.items()
                         if previous.instance_vars.get(key) != value}
        instances = [InstanceWire(1, mode, vars=instance_vars, states=states)]
        old_phase, new_phase = set(previous.phase_states), set(self.phase_states)
        if old_phase != new_phase:
            receivers = {state: StateWire(messages.state(state).cls, state in new_phase)
                         for state in old_phase ^ new_phase}
            instances.append(InstanceWire(2, messages, states=receivers))
        if previous.instance_vars[59][0] == Phase.WAITING and not waiting:
            instances.append(InstanceWire(3, None, gone=True))
        callout = (cart_changed, 1, HUD_CART_CHANGED) if cart_changed is not None else None
        if progress_cmfd is not None:
            return owner_delta(progress_cmfd, False, instances, entity_vars, graphs, callout=callout)
        return remote_delta(instances, entity_vars, graphs, callout=callout)


@dataclass(frozen=True)
class PayloadState:
    animation: int
    stage: int
    progress: float
    distance: float
    route_length: float
    checkpoint_fractions: tuple[float, ...]
    active: bool
    enabled: bool
    direction: int
    pushers: tuple[expr.Entity, ...]
    blockers: tuple[expr.Entity, ...]

    def frame(self, previous: "PayloadState | None"):
        definition = graph.graph(PAYLOAD_GRAPH)
        if definition is None:
            return None
        animation = definition.state(self.animation)
        states = {}
        if previous is None or self.animation != previous.animation:
            if previous is not None:
                states[previous.animation] = StateWire(definition.state(previous.animation).cls, False)
            states[self.animation] = StateWire(animation.cls, True, {"counter": 1})
        values = {0x009A: (self.progress, ()), PAYLOAD_DISTANCE: (self.distance, ()),
                  CHECKPOINT_INDEX: (self.stage, ())}
        if previous is None or self.active != previous.active:
            values[PAYLOAD_ACTIVE] = (self.active, ())
        if previous is None or self.direction != previous.direction:
            values[PAYLOAD_DIRECTION] = (self.direction, ())
        # 2D8.05E maps 02BB to Active and 2A56 to Locked. The remaining value (60BA) uses Completed.
        instance_vars = {PAYLOAD_ENABLED: (self.enabled, ()), CHECKPOINT_INDEX: (self.stage, ())}
        if previous is None or self.pushers != previous.pushers:
            instance_vars[PAYLOAD_PUSHERS] = (self.pushers, ())
        if previous is None or self.blockers != previous.blockers:
            instance_vars[PAYLOAD_BLOCKERS] = (self.blockers, ())
        if previous is None:
            # The cart's presenter converts each fraction to a distance using the route length.
            values[PAYLOAD_ROUTE_LENGTH] = (self.route_length, ())
            values[PAYLOAD_FINISH_FRACTION] = (1.0, ())
            instance_vars[PAYLOAD_CHECKPOINT_FRACTIONS] = (self.checkpoint_fractions, ())
        instance = InstanceWire(1, definition, vars=instance_vars, states=states)
        write = remote_full_frame if previous is None else remote_delta
        return write([instance], values, {1: definition})


class EscortRound:
    def __init__(self, scene: EscortMap) -> None:
        self.payload = MAP_ENTITY | scene.payload
        self.stage_seconds = scene.stage_seconds
        self.checkpoints = []
        self.positions = []
        self.distances = []
        for point in scene.rail:
            if point.checkpoint is not None:
                self.checkpoints.append((self._append(point.position), MAP_ENTITY | point.checkpoint))
            else:
                self._append(point.position)
            if point.curve:
                a, b, c = point.curve
                for i in range(1, SAMPLES_PER_SEGMENT):
                    t = i / SAMPLES_PER_SEGMENT
                    self._append(tuple(
                        p + t * (linear + t * (quadratic + t * cubic))
                        for p, cubic, quadratic, linear in zip(point.position, a, b, c, strict=True)
                    ))
        self.length = self.distances[-1]
        self.checkpoint_fractions = tuple(distance / self.length for distance, _ in self.checkpoints)
        self.start_offset = contacts.sub(scene.payload_origin or self.positions[0], self.positions[0])
        self.start_angles = None
        if scene.payload_rotation is not None:
            self.start_angles = self._angles(contacts.rotate(scene.payload_rotation, (0.0, 0.0, 1.0)))
        self.phase = Phase.ASSEMBLE
        self.stage = 0
        self.distance = 0.0
        self.deadline = 0.0
        self.last_update = 0.0
        self.last_attacker = 0.0
        self.overtime = False
        self.pushers: tuple[expr.Entity, ...] = ()
        self.blockers: tuple[expr.Entity, ...] = ()
        self.animation = 23  # 1225 state 23: looping idle, from node 37's false branch
        self.speed = 0.0
        self.winner: int | None = None
        self.payload_shapes = tuple(collision.engine_shape(shape, scene.payload_scale)
                                    for shape in scene.payload_collision)
        self.payload_collision: EscortCollision | None = None
        self.doors = {MAP_ENTITY | p.index: p for p in scene.room_placeables if p.door}
        self.barriers = {
            MAP_ENTITY | p.index: p for p in scene.room_placeables if not p.door and not p.viewers
        }
        self.overlays = {MAP_ENTITY | p.index: p for p in scene.room_placeables if p.viewers}
        self.room_collision = {}

    def _append(self, position: tuple[float, float, float]) -> float:
        distance = self.distances[-1] + math.dist(position, self.positions[-1]) if self.positions else 0.0
        if not self.distances or distance > self.distances[-1]:
            self.positions.append(position)
            self.distances.append(distance)
        return distance

    def start_setup(self, now: float) -> None:
        self.phase = Phase.SETUP
        self.deadline = now + SETUP_SECONDS

    def _point(self, distance: float) -> tuple[float, float, float]:
        end = min(bisect.bisect_right(self.distances, distance), len(self.distances) - 1)
        start = end - 1
        origin, destination = self.positions[start], self.positions[end]
        step = tuple(b - a for a, b in zip(origin, destination, strict=True))
        t = (distance - self.distances[start]) / (self.distances[end] - self.distances[start])
        return tuple(p + t * dx for p, dx in zip(origin, step, strict=True))

    @staticmethod
    def _angles(direction) -> tuple[int, int]:
        dx, dy, dz = direction
        yaw = round(math.atan2(dx, dz) * ANGLE_UNITS)
        pitch = round(-math.atan2(dy, math.hypot(dx, dz)) * ANGLE_UNITS)
        return yaw, pitch

    def _pose(self) -> tuple[tuple[float, float, float], int, int]:
        before = self._point(max(0.0, self.distance - RAIL_LOOKAHEAD))
        after = self._point(min(self.length, self.distance + RAIL_LOOKAHEAD))
        yaw, pitch = self._angles(contacts.sub(after, before))
        position = self._point(self.distance)
        # Start at the map entity's pose so the first snapshot agrees with its
        # creation. Pick up the track's tangent as the wheels move onto it.
        blend = max(0.0, 1.0 - self.distance / RAIL_LOOKAHEAD)
        if blend:
            position = contacts.add(position, contacts.scale(self.start_offset, blend))
            if self.start_angles is not None:
                start_yaw, start_pitch = self.start_angles
                yaw += round(((start_yaw - yaw + 32768) % 65536 - 32768) * blend)
                pitch += round((start_pitch - pitch) * blend)
        return position, (yaw + 32768) % 65536 - 32768, pitch

    def movement(self) -> Movement:
        position, yaw, pitch = self._pose()
        heading, incline = yaw / ANGLE_UNITS, -pitch / ANGLE_UNITS
        horizontal = self.speed * math.cos(incline)
        velocity = (
            horizontal * math.sin(heading), self.speed * math.sin(incline), horizontal * math.cos(heading),
        )
        return Movement(position, yaw, velocity=velocity, body_pitch=pitch)

    def collision_world(self, world: collision.World) -> collision.World:
        if self.payload_shapes or self.doors or self.barriers:
            world = copy(world)
            first_room_shape = len(world.engine.shapes) + len(self.payload_shapes)
            for entity, placement in (self.doors | self.barriers).items():
                frame = placement.position, placement.rotation
                shapes = tuple(
                    contacts.StaticShape(
                        collision.engine_shape(shape, placement.scale), frame, first_room_shape + i,
                    )
                    for i, shape in enumerate(placement.collision)
                )
                self.room_collision[entity] = shapes
                first_room_shape += len(shapes)
            self.payload_collision = EscortCollision(world.engine, self.payload_shapes)
            self.payload_collision.place(*self._pose())
            self._update_room_collision()
            world.engine = self.payload_collision
            world.moving_ground = self.payload_collision.ground
        return world

    def room_barriers(self, viewer_team: int) -> frozenset[int]:
        if viewer_team not in (DEFENDERS, ATTACKERS) or self.phase == Phase.COMPLETE:
            return frozenset()
        room = (self.stage, ATTACKERS if viewer_team == DEFENDERS else DEFENDERS)
        return frozenset(entity for entity, p in self.barriers.items() if room in p.rooms)

    def room_visuals(self, viewer_team: int) -> frozenset[int]:
        if viewer_team not in (DEFENDERS, ATTACKERS) or self.phase == Phase.COMPLETE:
            return frozenset()
        return self.room_barriers(viewer_team) | frozenset(
            entity for entity, p in self.overlays.items()
            if viewer_team in p.viewers and any(stage == self.stage for stage, _ in p.rooms)
        )

    def room_visual_frame(self, entity: int, viewer_team: int):
        is_barrier = entity in self.barriers
        definition = graph.graph(BARRIER_GRAPH if is_barrier else ROOM_OVERLAY_GRAPH)
        if definition is None:
            return None
        values = {0x06F1: (viewer_team, ())} if is_barrier else {0x0877: (False, ()), 0x0878: (False, ())}
        control = definition.state(3)  # enabled model branch of 0845 (wall) or 072F (overlay)
        instance = InstanceWire(1, definition, vars=values, states={3: StateWire(control.cls, True)})
        return remote_full_frame([instance], {}, {1: definition})

    def door_open(self, entity: int) -> bool:
        first_stage = min(stage for stage, _ in self.doors[entity].rooms)
        return self.stage > first_stage or (self.stage == first_stage and self.phase >= Phase.ACTIVE)

    def door_movement(self, entity: int) -> Movement | None:
        # Doors and the cart both have the native 496257B5 mirrored component.
        # Prime the door's actor pose with the authored transform before animating its bones.
        placement = self.doors[entity]
        if not placement.has_transform:
            return None
        yaw, pitch = self._angles(contacts.rotate(placement.rotation, (0.0, 0.0, 1.0)))
        yaw = (yaw + 32768) % 65536 - 32768
        return Movement(placement.position, yaw, body_pitch=pitch)

    def _update_room_collision(self) -> None:
        if self.payload_collision is None:
            return
        closed = {entity for entity in self.doors if not self.door_open(entity)}
        barriers = self.room_barriers(ATTACKERS) | self.room_barriers(DEFENDERS)
        self.payload_collision.room_shapes = tuple(
            shape for entity in sorted(closed | barriers) for shape in self.room_collision[entity]
        )

    def checkpoint_frame(self, stage: int):
        # 0F4A reads the placed objective's 0F4C variables through the entity reference.
        return remote_full_frame([], {
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            10548: (stage, ()),
            10552: (self.stage_seconds[stage], ()),
            2190: (expr.Entity(self.payload), ()),
        }, {})

    @property
    def contested(self) -> bool:
        return bool(self.pushers and self.blockers)

    def advance(self, now: float, participants) -> None:
        """Consume elapsed match time and living players as (body, team, position)."""
        previous_room = self.phase, self.stage
        if self.phase == Phase.SETUP and now >= self.deadline:
            self.phase = Phase.ACTIVE
            self.last_attacker = self.last_update = self.deadline
            self.deadline += self.stage_seconds[0]
        if self.phase != Phase.ACTIVE:
            return
        x, y, z = self._pose()[0]
        previous_distance = self.distance
        nearby = {ATTACKERS: [], DEFENDERS: []}
        for body, team, (px, py, pz) in participants:
            if team in nearby and abs(py - y) <= PUSH_HEIGHT and math.hypot(px - x, pz - z) <= PUSH_RADIUS:
                nearby[team].append(expr.Entity(body))
        was_escorted = bool(self.pushers)
        self.pushers = tuple(nearby[ATTACKERS])
        self.blockers = tuple(nearby[DEFENDERS])
        end = self.deadline
        if self.overtime:
            if self.pushers and (was_escorted or now < self.last_attacker + OVERTIME_SECONDS):
                self.last_attacker = now
            end = self.last_attacker + OVERTIME_SECONDS
        elif now >= end and self.pushers:
            self.overtime = True
            self.last_attacker = now
            end = now + OVERTIME_SECONDS
        elapsed = max(0.0, min(now, end) - self.last_update)
        self.last_update = now
        if self.pushers and now < end:
            self.last_attacker = now
            if not self.contested:
                speed_tier = min(len(self.pushers), len(PUSH_SPEED) - 1)
                self._push(elapsed * PUSH_SPEED[speed_tier], now)
        elif now > self.last_attacker + ROLLBACK_DELAY:
            rollback = min(elapsed, now - self.last_attacker - ROLLBACK_DELAY)
            floor = self.checkpoints[self.stage - 1][0] if self.stage else 0.0
            self.distance = max(floor, self.distance - rollback * ROLLBACK_SPEED)
        if self.phase == Phase.ACTIVE and now >= end and self.deadline <= end:
            self._finish(DEFENDERS)
        # 1225 states 17 and 18 are launched by the forward and rollback branches.
        self.animation = 23
        self.speed = 0.0
        if self.phase == Phase.ACTIVE and self.distance != previous_distance:
            self.animation = 17 if self.distance > previous_distance else 18
            self.speed = (self.distance - previous_distance) / elapsed if elapsed else 0.0
        if self.payload_collision is not None and self.distance != previous_distance:
            self.payload_collision.place(*self._pose())
        if previous_room != (self.phase, self.stage):
            self._update_room_collision()

    def _push(self, distance: float, now: float) -> None:
        self.distance = min(self.length, self.distance + distance)
        while self.stage < len(self.checkpoints) and self.distance >= self.checkpoints[self.stage][0]:
            self.stage += 1
            if self.stage == len(self.checkpoints):
                self._finish(ATTACKERS)
                return
            self.deadline = max(self.deadline, now) + self.stage_seconds[self.stage]
            self.overtime = False

    def _finish(self, team: int) -> None:
        self.phase = Phase.COMPLETE
        self.winner = team
        self.overtime = False
        self.pushers = self.blockers = ()

    def mode_state(
        self, now: float, assembly_end: float | None, *, hero_select_ready: bool = True,
        viewer_team: int = DEFENDERS,
    ) -> ModeState:
        phase = Phase.WAITING if self.phase == Phase.ASSEMBLE and assembly_end is None else self.phase
        clock = assembly_end if phase == Phase.ASSEMBLE else self.deadline
        current_goal, progress = self.progress()
        goal = expr.Entity(self.checkpoints[current_goal][1])
        grace = max(0.0, self.last_attacker + OVERTIME_SECONDS - now) if self.overtime else 0.0
        heading = {
            Phase.WAITING: 0x0DE0000000006114,
            Phase.ASSEMBLE: 0x0DE0000000006115,
            Phase.SETUP: 0x0DE0000000006116,
            Phase.ACTIVE: ACTIVE_HEADING[viewer_team == ATTACKERS],
        }.get(phase)
        seconds = 0.0 if phase in (Phase.WAITING, Phase.COMPLETE) else max(0.0, clock - now)
        seconds = math.ceil(seconds * 4) / 4
        phase_flags = {
            Phase.WAITING: 0x4C49, Phase.ASSEMBLE: 0x4C48, Phase.SETUP: 0x2F51,
            Phase.ACTIVE: 0x4C47, Phase.COMPLETE: 0x4C46,
        }
        entity_vars = {
            # 1B4A queries these on the mode entity. 4C21 reads the objective
            # kind and section progress, 4C22 reads the checkpoint/cart refs.
            59: (int(phase), ()),
            2190: (expr.Entity(self.payload), ()),
            0x1EDD: (0, ()),  # first and only round
            1923: (expr.Asset(heading) if heading else None, ()),
            6971: (math.ceil(grace * 4) / 4, ()),
            9544: (False, ()),  # single round, no competitive time bank
            combat.WAITING_FOR_PLAYERS: (phase == Phase.WAITING, ()),
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            CHECKPOINT_INDEX: (current_goal, ()),
            combat.ATTACKING_TEAM_VAR: (ATTACKERS, ()),
            combat.DEFENDING_TEAM_VAR: (DEFENDERS, ()),
            12376: (tuple(expr.Entity(entity) for _, entity in self.checkpoints), ()),
            13468: (round(progress, 3), ()),
            HUD_ROUND_DATA: (expr.Map((
                (combat.ATTACKING_TEAM_VAR, ATTACKERS),
                (HUD_CLOCK, seconds),
                (CHECKPOINT_INDEX, current_goal),
                (0x1EDD, 0),
                (0x2259, True),  # current objective is a payload
                (0x59DA, grace / OVERTIME_SECONDS),
            )), ()),
            **{key: (flag_phase == phase, ()) for flag_phase, key in phase_flags.items()},
        }
        if phase != Phase.WAITING:
            entity_vars[COUNTDOWN] = (seconds, ())
        instance_vars = {
            59: (int(phase), ()),
            2190: (expr.Entity(self.payload), ()),
            2499: (tuple(int(team == self.winner) for team in (DEFENDERS, ATTACKERS)), ()),
            7717: (OVERTIME_SECONDS, ()),
            9901: (goal, ()),
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            10551: (goal, ()),
            7901: (0, ()),  # first and only round
            HUD_CART_REFERENCE: (expr.Entity(self.payload), ()),
            13555: (round(self.distance / self.length, 3), ()),
            7525: (self.winner, ()),
        }
        states = {4, 95, HUD_OBJECTIVE_KIND}
        states.update({
            Phase.WAITING: (combat.WAITING_FOR_PLAYERS_STATE,),
            Phase.ASSEMBLE: (combat.ASSEMBLE_HEROES_STATE, 10) if hero_select_ready else (),
            Phase.SETUP: (14, 93),
            Phase.ACTIVE: (MATCH_LIVE, ESCORT_HUD, HUD_PROGRESS, HUD_PAYLOAD),
            Phase.COMPLETE: (19, 20, 44, ESCORT_HUD, HUD_PROGRESS, HUD_PAYLOAD),
        }[phase])
        if self.overtime:
            states.add(57)
        return ModeState(entity_vars, instance_vars, frozenset(states))

    def progress(self) -> tuple[int, float]:
        goal = min(self.stage, len(self.checkpoints) - 1)
        start = self.checkpoints[goal - 1][0] if goal else 0.0
        return goal, (self.distance - start) / (self.checkpoints[goal][0] - start)

    def payload_state(self) -> PayloadState:
        goal, progress = self.progress()
        direction = int(self.speed > 0) - int(self.speed < 0)
        return PayloadState(self.animation, goal, round(progress, 3), self.distance, self.length,
                            self.checkpoint_fractions, self.phase == Phase.ACTIVE, self.phase >= Phase.ACTIVE,
                            direction, self.pushers, self.blockers)

    def door_state(self, entity: int, now: float, previous: DoorState | None) -> DoorState:
        opened = self.door_open(entity)
        if previous is None:
            return DoorState(DoorPose.OPEN if opened else DoorPose.CLOSED)
        if previous.pose == DoorPose.CLOSED and opened:
            return DoorState(DoorPose.OPENING, previous.states, starts_at=now + self.doors[entity].open_delay)
        if previous.pose == DoorPose.OPENING:
            if DOOR_OPEN_ANIM not in previous.states:
                if now < previous.starts_at:
                    return previous
                return DoorState(DoorPose.OPENING, frozenset({DOOR_OPEN_ANIM}),
                                 ends_at=now + self.doors[entity].open_seconds)
            if now >= previous.ends_at:
                return DoorState(DoorPose.OPEN, frozenset({DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS}))
            return previous
        clip, alias = ((DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS) if opened else (DOOR_CLOSE_ANIM, DOOR_CLOSE_ALIAS))
        if not previous.states:
            return DoorState(previous.pose, frozenset({clip}))
        if alias not in previous.states:
            return DoorState(previous.pose, previous.states | {alias})
        return previous

    def door_frame(self, entity: int, state: DoorState, previous: DoorState | None):
        definition = graph.graph(DOOR_GRAPH)
        if definition is None:
            return None
        placement = self.doors[entity]
        opened = state.pose != DoorPose.CLOSED
        playing = state.pose == DoorPose.OPENING and DOOR_OPEN_ANIM in state.states
        settled_open = state.pose == DoorPose.OPEN
        values = {
            0x22A5: (opened, ()),
            0x02BB: (playing, ()),
            0x3DF1: (settled_open, ()),
            0x3DF2: (not playing and not settled_open, ()),
            0x475B: (settled_open, ()),
        }
        if previous is None:
            # The source/instance store is initialized before the first sampled
            # animation. 0D32 then installs the corresponding persistent alias.
            values.update({
                0x07F0: (placement.open_seconds, ()), 0x07F3: (placement.close_seconds, ()),
                0x09CB: (placement.open_delay, ()), 0x1B7F: (1, ()),
                0x22A4: (opened, ()),
            })
        old_states = previous.states if previous is not None else frozenset()
        states = {index: StateWire(definition.state(index).cls, index in state.states, {"counter": 1})
                  for index in old_states ^ state.states}
        if settled_open and DOOR_OPEN_ANIM in state.states and (
            DOOR_OPEN_ANIM not in old_states or previous.pose == DoorPose.OPENING
        ):
            # Re-sample and retain the terminal pose when the live clip ends. Some
            # placements have no component to retain an alias request on its own.
            counter = 2 if DOOR_OPEN_ANIM in old_states else 1
            states[DOOR_OPEN_ANIM] = StateWire(definition.state(DOOR_OPEN_ANIM).cls, True, {
                "counter": counter, "time": round(placement.open_seconds * 1000),
            })
        instance = InstanceWire(1, definition, vars=values, states=states)
        write = remote_full_frame if previous is None else remote_delta
        # 0D32 distinguishes the requested direction from the open pose.
        # The open alias's begin action publishes 475B once the clip finishes.
        entity_vars = {0x22A5: (opened, ()), 0x475B: (settled_open, ())}
        if previous is None:
            entity_vars[0x0804] = (True, ())  # 0D32 entry publishes this before starting its pose branch
        return write([instance], entity_vars, {1: definition})
