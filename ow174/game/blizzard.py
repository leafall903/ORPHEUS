"""104319 Blizzard flight and area host from 064A, 0946, 065E and 0B77.

Ballistic chord collision, capsule exposure and per-tick health are server
reconstructions. Area values come from the inspected 065E resource.
"""

import math
from dataclasses import dataclass, field

from ow174.game import deflection, frost
from ow174.game.script import expr

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
FRAME_MS = 16
RADIUS = 10.0
DELAY_MS, ACTIVE_MS = 200, 4250
DAMAGE_RATE = 20.0
FROST_INITIAL, FROST_STEP, FROST_INTERVAL_MS = expr.f32(0.5), expr.f32(0.040000003), 200


@dataclass
class Flight:
    owner: object
    body: int
    position: tuple
    velocity: tuple
    gravity: float
    left: float
    shot: object
    last_tick: int
    flown: float = 0.0


@dataclass
class Area:
    entity: int
    owner: object
    body: int
    position: tuple
    starts: int
    ends: int
    last_ms: int
    frost_due: dict[int, int] = field(default_factory=dict)


class Blizzard:
    def __init__(self, combat):
        self.combat = combat
        self.flights: list[Flight] = []
        self.areas: dict[int, Area] = {}

    def launch(self, owner, state, shot_frame, origin, direction, speed, lifetime, *, activation, snapshot):
        motion = state.node.field("m_projectileMotions")[0]
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        shot = self.combat.projectiles.fired(
            owner, state, shot_frame, DEFINITION, lifetime, origin, direction,
            activation=activation, snapshot=snapshot,
        )
        self.flights.append(Flight(owner, owner.body, origin, tuple(speed * axis for axis in direction),
                                   gravity, lifetime, shot, self.combat.tick))

    def retire(self, body):
        self.flights[:] = [flight for flight in self.flights if flight.body != body]
        for entity, area in tuple(self.areas.items()):
            if area.body == body:
                del self.areas[entity]

    def pending_ticks(self, previous, end):
        # Upper bound includes the full lifetime of an area that lands during
        # the gap. Replay stops once the last existing phase has resolved.
        flight_ticks = max((math.ceil(f.left * 1000 / FRAME_MS) for f in self.flights), default=0)
        last = max((math.ceil(a.ends / FRAME_MS) for a in self.areas.values()), default=previous)
        if flight_ticks:
            last = max(last, previous + flight_ticks + math.ceil((DELAY_MS + ACTIVE_MS) / FRAME_MS))
        return range(previous + 1, min(end, last) + 1)

    def update(self, tick):
        for flight in tuple(self.flights):
            if tick > flight.last_tick:
                flight.last_tick = tick
                self._fly(flight, tick)
        now = tick * FRAME_MS
        for area in tuple(self.areas.values()):
            self._affect(area, now)
            if now >= area.ends:
                del self.areas[area.entity]

    def _fly(self, flight, tick, duration=None, reflections=0):
        from ow174.game.combat import _along, _sub, ray_world

        if duration is None:
            duration = min(flight.left, FRAME_MS / 1000)
        x, y, z = _along(flight.position, flight.velocity, duration)
        destination = (x, y - flight.gravity * duration**2 / 2, z)
        delta = _sub(destination, flight.position)
        length = math.hypot(*delta)
        if length > 0:
            direction = tuple(axis / length for axis in delta)
            wall = ray_world(self.combat._collision(), flight.position, direction, length)
            pillar = self.combat.walls.cast(flight.position, direction, length)
            hits_pillar = pillar is not None and (wall is None or pillar[0] < wall)
            if hits_pillar:
                wall = pillar[0]
            limit = length if wall is None else wall
            reflected = (deflection.genji_contact(
                self.combat, flight.owner, DEFINITION, flight.position, direction, limit,
            ) if reflections < 8 else None)
            if reflected is not None and wall is not None and reflected[0] >= wall:
                reflected = None
            defense_limit = reflected[0] if reflected is not None else limit
            absorbed = self.combat._matrix_hit(
                flight.owner, flight.position, direction, defense_limit, DEFINITION,
            )
            if absorbed is not None:
                self.flights.remove(flight)
                if flight.shot is not None:
                    self.combat.projectiles.absorbed(flight.shot)
                return
            if reflected is not None:
                contact, owner, facing = reflected
                spent = duration * contact / length
                vx, vy, vz = flight.velocity
                speed = math.hypot(vx, vy - flight.gravity * spent, vz)
                flight.position = _along(flight.position, direction, contact)
                flight.velocity = tuple(speed * axis for axis in facing)
                flight.owner, flight.body = owner, owner.body
                flight.left -= spent
                flight.flown += spent
                if flight.shot is not None:
                    self.combat.projectiles.absorbed(flight.shot)
                    flight.shot = None
                self._fly(flight, tick, duration - spent, reflections + 1)
                return
            if wall is not None:
                self._end(flight, duration * wall / length)
                # Keep a wall-anchored area on the impact side of the face.
                point = _along(flight.position, direction, max(0., wall - .0001) if hits_pillar else wall)
                entity = self.combat.next_bot
                if entity >= 0xD0000000:
                    raise ValueError("Blizzard entity allocator exhausted")
                self.combat.next_bot += 1
                start = tick * FRAME_MS + DELAY_MS
                self.areas[entity] = Area(entity, flight.owner, flight.body, point,
                                          start, start + ACTIVE_MS, start)
                return
        flight.position = destination
        vx, vy, vz = flight.velocity
        flight.velocity = (vx, vy - flight.gravity * duration, vz)
        flight.left -= duration
        if flight.left <= 1e-9:
            self._end(flight, duration)
        else:
            flight.flown += duration

    def _end(self, flight, duration):
        self.flights.remove(flight)
        if flight.shot is not None:
            self.combat.projectiles.flown(flight.shot, flight.flown + duration)

    def _affect(self, area, now):
        from ow174.game.combat import _sub, ray_world, segment_distance

        until = min(now, area.ends)
        elapsed = until - area.last_ms
        if elapsed <= 0:
            return
        centre = (area.position[0], area.position[1] + .5, area.position[2])
        barriers = self.combat.barriers.snapshot()
        due_next = {}
        for target in self.combat._enemies(area.owner):
            if frost.immune(target.owner, (6604, 8440)):
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > RADIUS:
                continue
            x, y, z = target.feet
            delta = _sub((x, y + target.eye, z), centre)
            distance = math.hypot(*delta)
            if distance > 0:
                ray = tuple(axis / distance for axis in delta)
                wall = ray_world(self.combat._collision(), centre, ray, distance)
                if wall is not None and wall < distance - 1e-5:
                    continue
                if self.combat.walls.cast(centre, ray, distance - 1e-5) is not None:
                    continue
                # 065E's auxiliary filter includes tag 003A on Winston's
                # 01C3 sphere. Project it as a shell crossing, separate from
                # the area's body-health target list.
                if self.combat.barriers.first(area.owner, centre, ray, distance, snapshot=barriers):
                    continue
            if not self.combat._damage(area.owner, target, DAMAGE_RATE * elapsed / 1000, False,
                                       LOADOUT, self.combat.tick, world_tick=self.combat.tick):
                continue
            vitals = self.combat.vitals_of(target)
            if vitals.dead:
                continue
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                self.combat._frost_hit(area.entity, target, vitals,
                                       initial=FROST_INITIAL, step=FROST_STEP, at=due)
                due += FROST_INTERVAL_MS
            due_next[target.entity] = due
        area.frost_due = due_next
        area.last_ms = until
