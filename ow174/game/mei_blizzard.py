"""Mei Blizzard authority reconstructed from104319 064A/0946/065E/0B77.

The native projectile/contact/target/status hosts are not implemented by the
permissive VM. Keep this consumer scoped to064A state3. Per-tick damage,
chord sweeps, first-contact anchoring and aggregate frost are reconstructed;
native movement, settings overrides and original-client parity remain open.
"""

import math
from dataclasses import dataclass, field

from ow174.game import defenses, deflections, immunity, mei, world
from ow174.game.script import expr

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
FRAME_MS = 16
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
    damage_scale: float = 1.


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
    damage_scale: float = 1.


class Blizzard:
    def __init__(self, combat):
        self.combat = combat
        self.flights: list[Flight] = []
        self.areas: dict[int, Area] = {}

    def fired(self, player, state, shot_frame, origin, direction, *, activation, snapshot):
        from ow174.game import nano
        if state.instance.graph.index != 0x064A or state.index != 3:
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
                                   gravity, lifetime, self.combat.tick, shot,
                                   damage_scale=nano.offense(self.combat, player.body) * self.combat.orisa.superchargers.scale(player)))
        return True

    def retire(self, body):
        for flight in tuple(self.flights):
            if flight.body == body:
                self.end_flight(flight)
        for area in tuple(self.areas.values()):
            if area.body == body:
                self.remove(area)

    def end_flight(self, flight):
        self.flights.remove(flight)
        if flight.shot is not None:
            self.combat.projectiles.flown(flight.shot, flight.flown)

    def land(self, flight, point, tick, *, static_impact_normal=None):
        self.end_flight(flight)
        entity = self.combat.next_bot
        if entity >= 0xD0000000:
            raise ValueError("Blizzard entity allocator exhausted")
        self.combat.next_bot += 1
        starts = tick * FRAME_MS + DELAY_MS
        area = Area(entity, flight.owner, flight.body, point, starts, starts + DURATION_MS, starts,
                    static_impact_normal=static_impact_normal, damage_scale=flight.damage_scale)
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
        hit = self.combat.match.collision_world.cast_ray(flight.position, direction, length)
        wall = self.combat.walls.cast(flight.position, direction, length)
        distance = hit.distance if hit is not None else math.inf
        if wall is not None:
            distance = min(distance, wall[0])
        defense = self.combat._deflector(flight.owner, DEFINITION, flight.position, direction, length)
        reflection = ((defense[0], defense[1].owner, defense[2])
                      if defense is not None and not defense[3] and reflections < 8 else None)
        if defense is not None and defense[3] and defense[0] < distance:
            guard = defense[4]
            if guard is not None and guard.instance.graph.index == 0x1D02:
                deflections.absorb(guard, 0.0)
            flight.flown += seconds * defense[0] / max(length, 1e-9)
            self.end_flight(flight)
            return True
        if reflection is not None and reflection[0] < distance:
            from ow174.game import nano
            contact, owner, facing = reflection
            spent = seconds * contact / max(length, 1e-9)
            velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * spent,
                        flight.velocity[2])
            speed = math.sqrt(sum(v * v for v in velocity))
            flight.position = tuple(p + d * contact for p, d in zip(flight.position, direction, strict=True))
            flight.owner, flight.body = owner, owner.body
            flight.damage_scale = nano.offense(self.combat, owner.body) * self.combat.orisa.superchargers.scale(owner)
            flight.velocity = tuple(speed * d for d in facing)
            flight.flown += spent
            flight.left -= spent
            return self.chord(flight, seconds - spent, at, reflections + 1)
        if distance <= length:
            flight.flown += seconds * distance / max(length, 1e-9)
            point = tuple(p + d * distance for p, d in zip(flight.position, direction, strict=True))
            normal = hit.normal if hit is not None and hit.distance == distance else None
            self.land(flight, point, at, static_impact_normal=normal)
            return True
        flight.position = destination
        flight.velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * seconds,
                           flight.velocity[2])
        flight.flown += seconds
        flight.left -= seconds
        return False

    def show(self):
        for viewer in self.combat.match.players:
            if not viewer.spawned or viewer.client is None:
                continue
            for area in self.areas.values():
                if area.entity in viewer.seen:
                    continue
                viewer.client.queue_entities([world.EntityUpdate(
                    area.entity, world.OP_CREATE,
                    lambda origin, p=area.position, t=area.owner.team: world.create(
                        origin, DEFINITION, {26: [0x800000 << t]},
                        flags=world.WITH_TRANSFORM | world.WITH_SCALE,
                        position=(p[0], p[1] + 2.0, p[2]),
                    ),
                )])
                viewer.seen[area.entity] = True

    def remove(self, area):
        if self.areas.pop(area.entity, None) is None:
            return
        for viewer in self.combat.match.players:
            if area.entity in viewer.seen:
                del viewer.seen[area.entity]
                if viewer.client is not None:
                    viewer.client.queue_entities([world.EntityUpdate(area.entity, world.OP_DESTROY)])

    def exposed(self, area, target):
        from ow174.game.combat import ray_world, segment_distance

        if (defenses.cryo_protected(target.owner) or immunity.phased_out(target.owner)
                or self.combat.vitals_of(target).dead):
            return False
        sample_height = .5
        centre = (area.position[0], area.position[1] + sample_height, area.position[2])
        a, b = target.segment()
        if segment_distance(centre, a, b) - target.radius > RADIUS:
            return False
        destination = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        if self.combat.walls.blocked(centre, destination):
            return False
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
            hit = ray_world(self.combat.match.collision_world, origin,
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
            damage = area.damage_scale * DAMAGE_RATE * elapsed / 1000
            dealt = self.combat._damage(area.owner, target, damage, False,
                                        LOADOUT, self.combat.tick, world_tick=self.combat.tick,
                                        source_scaled=True, source_body=area.body)
            if not dealt or self.combat.vitals_of(target).dead or mei.frost_immune(target.owner):
                continue
            present.add(target.entity)
            # The native073C host refreshes its own0.2s loop. Reconstruct
            # exposure deadlines separately from damage; discard deadlines
            # across barrier-blocked or frost-immune ticks so their frost is never replayed.
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                self.combat._frost_hit(target, source=area.owner, channel=area.entity,
                                       now_ms=due, initial=FROST_INITIAL, step=FROST_STEP)
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
                self.remove(area)
        self.show()
