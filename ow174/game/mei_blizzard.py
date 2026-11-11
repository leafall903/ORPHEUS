"""Build 104319 Blizzard flight and area reconstructed from 064A, 0946 and 065E."""

import math
from dataclasses import dataclass, field

from ow174.game import mei_frost
from ow174.game.script import expr

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
FRAME_MS = mei_frost.TICK_MS
RADIUS, DAMAGE_RATE = 10.0, 20.0
DELAY_MS, DURATION_MS = 200, 4250
FROST_INITIAL, FROST_STEP, FROST_INTERVAL_MS = expr.f32(0.5), expr.f32(0.040000003), 200
LOS_SURFACE_OFFSET = 0.0001


@dataclass
class Flight:
    owner: object
    body: int
    position: tuple
    velocity: tuple
    gravity: float
    left: float
    last_tick: int
    shot: object = None
    flown: float = 0.0
    reflections: int = 0


@dataclass
class Area:
    entity: int
    owner: object
    body: int
    position: tuple
    starts: int
    ends: int
    last_ms: int
    frost_due: dict = field(default_factory=dict)
    static_impact_normal: tuple | None = None


class Blizzard:
    def __init__(self, combat):
        self.combat = combat
        self.flights: list[Flight] = []
        self.areas: dict[int, Area] = {}
        self.next_id = 1

    def fired(self, player, state, shot_frame, origin, direction, *, activation, snapshot):
        if state.instance.graph.index != 0x064A or state.index != 3:
            return False
        definition = state.evaluate(state.node.field("m_projectileEntity")["m_entityDef"], watch=False)
        if not isinstance(definition, expr.Asset) or definition.guid != DEFINITION:
            return False
        speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(state.node.field("m_projectileLifetime"), watch=False))
        motion = state.node.field("m_projectileMotions")[0]
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        shot = self.combat.projectiles.fired(
            player, state, shot_frame, DEFINITION, lifetime, origin, direction,
            activation=activation, snapshot=snapshot,
        )
        self.flights.append(Flight(player, player.body, origin, tuple(speed * d for d in direction),
                                   gravity, lifetime, self.combat.tick, shot))
        return True

    def retire(self, body):
        for flight in tuple(self.flights):
            if flight.body == body:
                self.end_flight(flight)
        for area in tuple(self.areas.values()):
            if area.body == body:
                self.remove(area)
            else:
                area.frost_due.pop(body, None)

    def end_flight(self, flight):
        self.flights.remove(flight)
        if flight.shot is not None:
            self.combat.projectiles.flown(flight.shot, flight.flown)

    def land(self, flight, point, tick, *, static_impact_normal=None):
        self.end_flight(flight)
        entity = self.next_id
        self.next_id += 1
        starts = tick * FRAME_MS + DELAY_MS
        area = Area(entity, flight.owner, flight.body, point, starts, starts + DURATION_MS, starts,
                    static_impact_normal=static_impact_normal)
        self.areas[entity] = area

    def fly(self, flight, tick):
        # Fixed16ms chords preserve the same path through scheduler gaps.
        for at in range(flight.last_tick + 1, tick + 1):
            seconds = min(FRAME_MS / 1000, flight.left)
            if self.chord(flight, seconds, at):
                return
            flight.last_tick = at
            if flight.left <= 1e-9:
                self.end_flight(flight)  # Lifetime expiry in empty space creates no floor area.
                return

    def chord(self, flight, seconds, at):
        """Sweep one ballistic chord, including reflected remainder in this tick."""
        from ow174.game.combat import _along, aim

        destination = tuple(
            p + v * seconds - (flight.gravity * seconds**2 / 2 if i == 1 else 0)
            for i, (p, v) in enumerate(zip(flight.position, flight.velocity, strict=True))
        )
        delta = tuple(b - a for a, b in zip(flight.position, destination, strict=True))
        length = math.sqrt(sum(d * d for d in delta))
        direction = tuple(d / length for d in delta) if length else (0.0, -1.0, 0.0)
        hit = self.combat._collision().cast_ray(flight.position, direction, length)
        wall = self.combat.ice_walls.cast(flight.position, direction, length)
        distance, normal = (hit.distance, hit.normal) if hit is not None else (math.inf, None)
        if wall is not None and wall[0] < distance:
            distance, normal = wall[0], wall[2]
        guard = self.guard(flight, direction, min(distance, length))
        if guard is not None and guard[0] < distance:
            contact, owner, absorber = guard
            spent = seconds * contact / length if length else 0.0
            flight.flown += spent
            flight.left -= spent
            if absorber is not None:
                if flight.shot is not None:
                    self.combat.projectiles.cancel(flight.shot)
                    flight.shot = None
                self.end_flight(flight)
                return True
            speed = math.hypot(flight.velocity[0], flight.velocity[1] - flight.gravity * spent,
                               flight.velocity[2])
            facing = aim(owner.mover.yaw, owner.mover.pitch)
            point = _along(flight.position, direction, contact)
            flight.position = _along(point, facing, 0.0001)
            flight.owner, flight.body = owner, owner.body
            flight.velocity = tuple(speed * axis for axis in facing)
            flight.reflections += 1
            if flight.shot is not None:
                self.combat.projectiles.flown(flight.shot, flight.flown)
                flight.shot = None
            return self.chord(flight, seconds - spent, at)
        if distance <= length:
            flight.flown += seconds * distance / max(length, 1e-9)
            point = tuple(p + d * distance for p, d in zip(flight.position, direction, strict=True))
            self.land(flight, point, at, static_impact_normal=normal)
            return True
        flight.position = destination
        flight.velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * seconds,
                           flight.velocity[2])
        flight.flown += seconds
        flight.left -= seconds
        return False

    def guard(self, flight, direction, reach):
        nearest = None
        for target in self.combat._enemies(flight.owner):
            contact = self.combat._projectile_guard(target, flight.position, direction, reach)
            if contact is None or (contact[1] is None and flight.reflections >= 8):
                continue
            if nearest is None or contact[0] < nearest[0]:
                nearest = contact[0], target.owner, contact[1]
        return nearest

    def remove(self, area):
        self.areas.pop(area.entity, None)

    def exposed(self, area, target):
        from ow174.game.combat import ray_world, segment_distance

        sample_height = .5
        centre = (area.position[0], area.position[1] + sample_height, area.position[2])
        a, b = target.segment()
        if segment_distance(centre, a, b) - target.radius > RADIUS:
            return False
        destination = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        # Static rays skip zero-distance intersections. Start on the impact
        # side of the face while retaining the exact area centre for range.
        origin = centre
        if area.static_impact_normal is not None:
            normal = area.static_impact_normal
            offset = max(0., -sample_height * normal[1]) + LOS_SURFACE_OFFSET
            origin = tuple(p + n * offset for p, n in zip(centre, normal, strict=True))
        delta = tuple(b - a for a, b in zip(origin, destination, strict=True))
        length = math.sqrt(sum(d * d for d in delta))
        if length > 1e-9:
            hit = ray_world(self.combat._collision(), origin, tuple(d / length for d in delta),
                            length, ice_walls=self.combat.ice_walls)
            if hit is not None and hit < length - 1e-5:
                return False
        return True

    def affect(self, area, now):
        until = min(now, area.ends)
        elapsed = max(0, until - area.last_ms)
        if not elapsed:
            return
        due_next = {}
        damage = DAMAGE_RATE * elapsed / 1000
        for target in self.combat._enemies(area.owner):
            if not self.exposed(area, target):
                continue
            phase = self.combat.barrier_phase(target)
            if phase is not None:
                self.combat.damage_personal_barrier(target.entity, phase, area.owner, damage)
                continue
            dealt = self.combat._damage(area.owner, target, damage, False,
                                        LOADOUT, self.combat.tick, world_tick=self.combat.tick)
            if not dealt or self.combat.vitals_of(target).dead or mei_frost.immune(target.owner):
                continue
            # The native073C host refreshes its own0.2s loop. Reconstruct
            # exposure deadlines separately from damage; discard deadlines
            # across barrier-blocked or frost-immune ticks so their frost is never replayed.
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                self.combat._frost_hit(area.owner, target, self.combat.tick, channel=area.entity,
                                       initial=FROST_INITIAL, step=FROST_STEP)
                due += FROST_INTERVAL_MS
            due_next[target.entity] = due
        area.frost_due = due_next
        area.last_ms = until

    def update(self, tick):
        for flight in tuple(self.flights):
            self.fly(flight, tick)
        for area in tuple(self.areas.values()):
            self.affect(area, tick * FRAME_MS)
            if tick * FRAME_MS >= area.ends:
                self.remove(area)
