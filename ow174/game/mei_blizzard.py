"""Static consumers of the authored 064A throw and 065E Blizzard area."""

import math
from dataclasses import dataclass, field

from ow174.game import deflection, mei_frost
from ow174.game.script import expr

DEFINITION = deflection.BLIZZARD
LOADOUT = 0x0B900000000000D5
FRAME_MS = 16
RADIUS, DAMAGE_RATE = 10.0, 20.0
DELAY_MS, DURATION_MS = 200, 4250
FROST_INITIAL, FROST_STEP, FROST_INTERVAL_MS = expr.f32(0.5), expr.f32(0.040000003), 200


@dataclass
class Flight:
    owner: object
    body: int
    team: int
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
    team: int
    position: tuple
    starts: int
    ends: int
    last_ms: int
    frost_due: dict[int, int] = field(default_factory=dict)


class Blizzard:
    def __init__(self, fight):
        self.fight = fight
        self.flights: list[Flight] = []
        self.areas: dict[int, Area] = {}

    def fired(self, player, state, frame, origin, direction, *, activation, snapshot):
        if state.instance.graph.index != 0x064A or state.index != 3:
            return False
        speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(state.node.field("m_projectileLifetime"), watch=False))
        motion = state.node.field("m_projectileMotions")[0]
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        shot = self.fight.projectiles.fired(player, state, frame, DEFINITION, lifetime, origin, direction,
                                            activation=activation, snapshot=snapshot)
        velocity = tuple(speed * v for v in direction)
        self.flights.append(Flight(player, player.body, player.team, origin, velocity,
                                   gravity, lifetime, self.fight.tick, shot))
        return True

    def retire(self, body):
        for flight in tuple(self.flights):
            if flight.body == body:
                self.end(flight)
        self.areas = {key: area for key, area in self.areas.items() if area.body != body}

    def end(self, flight):
        self.flights.remove(flight)
        if flight.shot is not None:
            self.fight.projectiles.flown(flight.shot, flight.flown)

    def land(self, flight, tick):
        self.end(flight)
        entity = self.fight.next_bot
        if entity >= 0xD0000000:
            raise ValueError("Blizzard entity allocator exhausted")
        self.fight.next_bot += 1
        starts = tick * FRAME_MS + DELAY_MS
        self.areas[entity] = Area(entity, flight.owner, flight.body, flight.team, flight.position,
                                  starts, starts + DURATION_MS, starts)

    def chord(self, flight, seconds, tick, redirects=0):
        from ow174.game.combat import _along, aim, ray_world

        delta = tuple(v * seconds - (flight.gravity * seconds**2 / 2 if i == 1 else 0)
                      for i, v in enumerate(flight.velocity))
        length = math.hypot(*delta)
        direction = tuple(d / length for d in delta) if length else (0, -1, 0)
        world = self.fight._collision()
        wall = ray_world(world, flight.position, direction, length)
        ice = self.fight.walls.cast(flight.position, direction, length)
        if ice is not None and (wall is None or ice[0] < wall):
            wall = ice[0]
        hit = None
        if redirects < deflection.MAX_REDIRECTIONS:
            hit = deflection.projectile_contact(
                DEFINITION, self.fight._enemies(flight.owner, team=flight.team),
                flight.position, direction, wall if wall is not None else length,
            )
        intercepted = hit is not None and (wall is None or hit[0] < wall)
        distance = hit[0] if intercepted else wall if wall is not None else length
        elapsed = seconds * distance / length if length else seconds
        flight.flown += elapsed
        flight.left = max(0.0, flight.left - elapsed)
        flight.position = _along(flight.position, direction, distance)
        vx, vy, vz = flight.velocity
        flight.velocity = (vx, vy - flight.gravity * elapsed, vz)
        if intercepted:
            _, _, target, reflect, _ = hit
            if flight.shot is not None:
                self.fight.projectiles.intercepted(flight.shot)
                flight.shot = None
            if not reflect:
                self.end(flight)
                return True
            flight.owner, flight.body, flight.team = target.owner, target.entity, target.team
            mover = target.owner.mover
            flight.velocity = tuple(math.hypot(*flight.velocity) * v for v in aim(mover.yaw, mover.pitch))
            remaining = seconds - elapsed
            return remaining > 0 and self.chord(flight, remaining, tick, redirects + 1)
        if wall is not None:
            self.land(flight, tick)
            return True
        return False

    def exposed(self, area, target):
        from ow174.game.combat import ray_world, segment_distance

        centre = (area.position[0], area.position[1] + 0.5, area.position[2])
        a, b = target.segment()
        if segment_distance(centre, a, b) - target.radius > RADIUS:
            return False
        destination = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        if self.fight.walls.blocked(centre, destination):
            return False
        delta = tuple(b - a for a, b in zip(centre, destination, strict=True))
        length = math.hypot(*delta)
        return not length or ray_world(self.fight._collision(), centre,
                                       tuple(v / length for v in delta), length) is None

    def affect(self, area, now):
        from ow174.game.combat import REINHARDT

        until = min(now, area.ends)
        elapsed = until - area.last_ms
        if elapsed <= 0:
            return
        due_next = {}
        for target in self.fight._enemies(area.owner, team=area.team):
            if not self.exposed(area, target):
                continue
            vitals = self.fight.vitals_of(target)
            before = vitals.total
            self.fight._damage(area.owner, target, DAMAGE_RATE * elapsed / 1000, False,
                               LOADOUT, self.fight.tick, world_tick=self.fight.tick)
            hero = getattr(getattr(target.owner, "hero", None), "guid", None)
            if vitals.dead or vitals.total >= before or hero != REINHARDT:
                continue
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                mei_frost.hit(self.fight, area.owner, target, channel=area.entity,
                              initial=FROST_INITIAL, increment=FROST_STEP)
                due += FROST_INTERVAL_MS
            due_next[target.entity] = due
        area.frost_due = due_next
        area.last_ms = until

    def update(self, tick):
        for flight in tuple(self.flights):
            for at in range(flight.last_tick + 1, tick + 1):
                if self.chord(flight, min(FRAME_MS / 1000, flight.left), at):
                    break
                flight.last_tick = at
                if flight.left <= 1e-9:
                    self.end(flight)
                    break
        for area in tuple(self.areas.values()):
            self.affect(area, tick * FRAME_MS)
            if tick * FRAME_MS >= area.ends:
                del self.areas[area.entity]
