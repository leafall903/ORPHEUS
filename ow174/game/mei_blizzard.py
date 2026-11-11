"""Mei Blizzard authority reconstructed from104319 064A/0946/065E/0B77.

The native projectile/contact/target/status hosts are not implemented by the
permissive VM. Keep this consumer scoped to064A state3. Per-tick damage,
chord sweeps, first-contact anchoring and aggregate frost are reconstructed;
native movement, settings overrides and original-client parity remain open.
"""

import math
from dataclasses import dataclass, field

from ow174.game import mei_frost
from ow174.game.script import expr

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
FRAME_MS = 16
RADIUS, DAMAGE_RATE = 10.0, 20.0
DELAY_MS, DURATION_MS = 200, 4250
FROST_INITIAL, FROST_STEP, FROST_INTERVAL_MS = expr.f32(0.5), expr.f32(0.040000003), 200


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


class Blizzard:
    def __init__(self, combat):
        self.combat = combat
        self.flights: list[Flight] = []
        self.areas: dict[int, Area] = {}

    def launch(self, player, state, origin, direction, definition, speed, lifetime, shot):
        if (getattr(getattr(player, "hero", None), "guid", None) != mei_frost.HERO
                or state.instance.graph.index != 0x064A or state.index != 3
                or definition != DEFINITION or state.instance.component is not player.body_script.component):
            return False
        motion = state.node.field("m_projectileMotions")[0]
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        self.flights.append(Flight(player, player.body, origin, tuple(speed * d for d in direction),
                                     gravity, lifetime, self.combat.tick, shot))
        return True

    def retire(self, body):
        for flight in tuple(self.flights):
            if flight.body == body:
                self.end_flight(flight)
        for area in tuple(self.areas.values()):
            if area.body == body:
                self.areas.pop(area.entity, None)

    def end_flight(self, flight):
        self.flights.remove(flight)
        if flight.shot is not None:
            self.combat.projectiles.flown(flight.shot, flight.flown)

    def land(self, flight, point, tick):
        self.end_flight(flight)
        entity = self.combat.next_bot
        if entity >= 0xD0000000:
            raise ValueError("Blizzard entity allocator exhausted")
        self.combat.next_bot += 1
        starts = tick * FRAME_MS + DELAY_MS
        area = Area(entity, flight.owner, flight.body, point, starts, starts + DURATION_MS, starts)
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

    def chord(self, flight, seconds, at, reflections=0):
        """Sweep one ballistic chord, including reflected remainder in this tick."""
        destination = tuple(
            p + v * seconds - (flight.gravity * seconds**2 / 2 if i == 1 else 0)
            for i, (p, v) in enumerate(zip(flight.position, flight.velocity, strict=True))
        )
        delta = tuple(b - a for a, b in zip(flight.position, destination, strict=True))
        length = math.sqrt(sum(d * d for d in delta))
        direction = tuple(d / length for d in delta) if length else (0.0, -1.0, 0.0)
        from ow174.game.combat import aim, ray_world

        fight = self.combat
        wall = ray_world(fight._collision(), flight.position, direction, length) if length else None
        wall = fight.walls.distance(flight.position, direction, length, wall) if length else None
        distance = wall if wall is not None else math.inf
        reach = min(length, distance)
        reflection = fight._genji_flight_hit(
            flight.owner, flight.position, direction, reach, DEFINITION,
        ) if length and reflections < 8 else None
        absorption = fight._absorption_hit(
            flight.owner, flight.position, direction, reach, DEFINITION,
        ) if length else None
        if (absorption is not None and absorption[0] < distance
                and (reflection is None or absorption[0] <= reflection[0])):
            fight._absorbed(absorption, 0)
            self.flights.remove(flight)
            if flight.shot is not None:
                fight.projectiles.absorbed(flight.shot)
            return True
        if reflection is not None and reflection[0] < distance:
            contact, target = reflection
            owner = target.owner
            facing = aim(owner.mover.yaw, owner.mover.pitch)
            if flight.shot is not None:
                fight.projectiles.absorbed(flight.shot)
                flight.shot = None
            spent = seconds * contact / max(length, 1e-9)
            velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * spent,
                        flight.velocity[2])
            speed = math.sqrt(sum(v * v for v in velocity))
            flight.position = tuple(p + d * contact for p, d in zip(flight.position, direction, strict=True))
            flight.owner, flight.body = owner, owner.body
            flight.velocity = tuple(speed * d for d in facing)
            flight.flown += spent
            flight.left -= spent
            return self.chord(flight, seconds - spent, at, reflections + 1)
        if distance <= length:
            flight.flown += seconds * distance / max(length, 1e-9)
            point = tuple(p + d * distance for p, d in zip(flight.position, direction, strict=True))
            self.land(flight, point, at)
            return True
        flight.position = destination
        flight.velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * seconds,
                           flight.velocity[2])
        flight.flown += seconds
        flight.left -= seconds
        return False

    def exposed(self, area, target):
        from ow174.game.combat import ray_world, segment_distance

        centre = (area.position[0], area.position[1] + 0.5, area.position[2])
        a, b = target.segment()
        if segment_distance(centre, a, b) - target.radius > RADIUS:
            return False
        destination = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(b - a for a, b in zip(centre, destination, strict=True))
        length = math.sqrt(sum(d * d for d in delta))
        if self.combat.walls.blocked(centre, destination):
            return False
        if length > 1e-9:
            hit = ray_world(self.combat._collision(), centre,
                            tuple(d / length for d in delta), length)
            if hit is not None and hit < length - 1e-5:
                return False
        return True

    def affect(self, area, now):
        until = min(now, area.ends)
        elapsed = max(0, until - area.last_ms)
        if not elapsed:
            return
        present = set()
        for target in self.combat._enemies(area.owner):
            if not self.exposed(area, target):
                continue
            present.add(target.entity)
            fight = self.combat
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(area.owner, target, DAMAGE_RATE * elapsed / 1000, False,
                          LOADOUT, fight.tick, world_tick=fight.tick)
            # A shielded or immune interval does not accumulate queued frost.
            due = max(area.last_ms, area.frost_due.get(target.entity, area.last_ms))
            while due < until:
                if vitals.total < before and not vitals.dead:
                    fight.frost.apply(area.owner, target, tick=fight.tick, channel=area.entity,
                                      initial=FROST_INITIAL, step=FROST_STEP, at_ms=due)
                due += FROST_INTERVAL_MS
            area.frost_due[target.entity] = due
        area.frost_due = {entity: due for entity, due in area.frost_due.items() if entity in present}
        area.last_ms = until

    def update(self, tick):
        for flight in tuple(self.flights):
            self.fly(flight, tick)
        for area in tuple(self.areas.values()):
            self.affect(area, tick * FRAME_MS)
            if tick * FRAME_MS >= area.ends:
                self.areas.pop(area.entity, None)
