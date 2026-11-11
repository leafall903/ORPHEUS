"""World host for the 064A Blizzard throw and the landed 065E field."""

import math
from dataclasses import dataclass, field

from ow174.game import mei, phase_defenses, reinhardt_barrier, shot_guards, world
from ow174.game.script import expr

CAST, VOLLEY = 0x064A, 9
DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
DAMAGE_TAGS = tuple(0x0D80000000000000 | tag for tag in (0xF20, 0x25A, 0x1732, 0x2D43, 0x4D2A, 0x4D60))
FRAME_MS = mei.FRAME_MS
RADIUS, DAMAGE_RATE = 10.0, 20.0
DELAY_MS, DURATION_MS = 200, 4250
FROST_INITIAL, FROST_STEP, FROST_INTERVAL_MS = expr.f32(0.5), expr.f32(0.040000003), 200
SURFACE_OFFSET = 0.0001


def cancel_cast(component):
    instance = next((i for i in component.instances.values() if i.graph.index == CAST), None)
    pending = instance.states.get(2) if instance is not None else None
    if pending is not None and pending.active:
        with component.operation():
            pending.end(False)
            instance.state(1).stop()


@dataclass
class Flight:
    shooter: object
    body: int
    team: int
    position: tuple
    velocity: tuple
    direction: tuple
    gravity: float
    left: float
    last_tick: int
    damage_scale: float
    shot: object = None
    flown: float = 0.0


@dataclass
class Area:
    entity: int
    shooter: object
    body: int
    team: int
    position: tuple
    normal: tuple
    starts: int
    ends: int
    last_ms: int
    damage_scale: float
    frost_due: dict = field(default_factory=dict)


class Blizzard:
    def __init__(self, fight):
        self.fight = fight
        self.flights = []
        self.areas = {}

    def launch(self, player, state, frame, origin, direction, *, activation, snapshot):
        node = state.node
        if expr.guid_of(node.field("m_projectileEntity.m_entityDef.m_entityDef")) != DEFINITION:
            return
        speed = expr.to_float(state.evaluate(node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(node.field("m_projectileLifetime"), watch=False))
        motion, = node.field("m_projectileMotions")
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        if not all(math.isfinite(value) and value > 0 for value in (speed, lifetime, gravity)):
            return
        fight = self.fight
        shot = fight.projectiles.fired(
            player, state, frame, DEFINITION, lifetime, origin, direction,
            activation=activation, snapshot=snapshot,
        )
        self.flights.append(Flight(
            player, player.body, player.team, origin, tuple(speed * v for v in direction),
            direction, gravity, lifetime, fight.tick, fight.outgoing_scale(player.body), shot,
        ))

    def retire(self, body):
        for flight in tuple(self.flights):
            if flight.body == body:
                self.end_flight(flight)
        for area in tuple(self.areas.values()):
            if area.body == body:
                self.remove(area)

    def replay_until(self, previous):
        ends = [math.ceil(area.ends / FRAME_MS) for area in self.areas.values()]
        tail = (DELAY_MS + DURATION_MS) / FRAME_MS
        ends.extend(f.last_tick + math.ceil(f.left * 1000 / FRAME_MS + tail) for f in self.flights)
        return max(ends, default=previous)

    def end_flight(self, flight):
        self.flights.remove(flight)
        if flight.shot is not None:
            self.fight.projectiles.flown(flight.shot, flight.flown)

    def land(self, flight, hit, tick):
        self.end_flight(flight)
        entity = self.fight.projectiles.allocate_seen_entity()
        starts = tick * FRAME_MS + DELAY_MS
        self.areas[entity] = Area(
            entity, flight.shooter, flight.body, flight.team, hit.point, hit.normal,
            starts, starts + DURATION_MS, starts, flight.damage_scale,
        )

    def fly(self, flight, seconds, tick, returns=0):
        from ow174.game.combat import _along

        x, y, z = flight.velocity
        delta = x * seconds, (y - flight.gravity * seconds / 2) * seconds, z * seconds
        length = math.hypot(*delta)
        if length <= 1e-9:
            flight.velocity = x, y - flight.gravity * seconds, z
            flight.left -= seconds
            if flight.left <= 1e-9:
                self.end_flight(flight)
            return
        direction = flight.direction = tuple(d / length for d in delta)
        fight = self.fight
        hit = fight._line_collision().cast_ray(flight.position, direction, length)
        reach = hit.distance if hit is not None else length
        gate = fight._projectile_guard(flight, reach, returns < shot_guards.MAX_RETURNS)
        if gate is not None:
            distance, _, target, heading = gate
            spent = seconds * distance / length
            flight.flown += spent
            if shot_guards.guard_hero(target.owner) != shot_guards.GENJI:
                self.end_flight(flight)
                return
            if flight.shot is not None:
                fight.projectiles._end(flight.shot, tick, explode=False)
                flight.shot = None
            speed = math.hypot(x, y - flight.gravity * spent, z)
            contact = _along(flight.position, direction, distance)
            flight.position = _along(contact, heading, SURFACE_OFFSET)
            flight.shooter, flight.body, flight.team = target.owner, target.entity, target.team
            flight.damage_scale = fight.outgoing_scale(target.entity)
            flight.velocity = tuple(speed * d for d in heading)
            flight.left -= spent
            self.fly(flight, seconds - spent, tick, returns + 1)
            return
        if hit is not None:
            flight.flown += seconds * hit.distance / length
            self.land(flight, hit, tick)
            return
        flight.position = _along(flight.position, direction, length)
        flight.velocity = (x, y - flight.gravity * seconds, z)
        flight.flown += seconds
        flight.left -= seconds
        if flight.left <= 1e-9:
            self.end_flight(flight)

    def exposed(self, area, target):
        from ow174.game.combat import capsule_visible, segment_distance

        if target.is_device or mei.protected(target.owner) or phase_defenses.active(target.owner):
            return False
        x, y, z = area.position
        centre = x, y + 0.5, z
        if segment_distance(centre, *target.segment()) - target.radius > RADIUS:
            return False
        # Sample from the approach side of the surface where the drone landed.
        offset = max(0, -0.5 * area.normal[1]) + SURFACE_OFFSET
        origin = tuple(a + b * offset for a, b in zip(centre, area.normal, strict=True))
        px, py, pz = target.feet
        point = px, py + target.eye, pz
        return capsule_visible(self.fight._line_collision(), origin, point, point, 0)

    def affect(self, area, now):
        until = min(now, area.ends)
        elapsed = until - area.last_ms
        if elapsed <= 0:
            return
        fight = self.fight
        present = set()
        damage = area.damage_scale * DAMAGE_RATE * elapsed / 1000
        for target in fight._enemies(area.shooter, team=area.team):
            if not self.exposed(area, target):
                continue
            if reinhardt_barrier.protect_body(fight, target, damage, attacker=area.body):
                continue
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(area.shooter, target, damage, False, LOADOUT, fight.tick,
                          world_tick=fight.tick, tags=DAMAGE_TAGS, source_body=area.body, source_scaled=True)
            if vitals.dead or vitals.total >= before:
                continue
            present.add(target.entity)
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                fight.mei.add_frost(target, area.shooter, area.body, channel=area.entity,
                                    now_ms=due, initial=FROST_INITIAL, step=FROST_STEP)
                due += FROST_INTERVAL_MS
            area.frost_due[target.entity] = due
        area.frost_due = {entity: due for entity, due in area.frost_due.items() if entity in present}
        area.last_ms = until

    def update(self, tick):
        for flight in tuple(self.flights):
            for at in range(flight.last_tick + 1, tick + 1):
                flight.last_tick = at
                self.fly(flight, min(flight.left, FRAME_MS / 1000), at)
                if flight not in self.flights:
                    break
        for area in tuple(self.areas.values()):
            self.affect(area, tick * FRAME_MS)
            if tick * FRAME_MS >= area.ends:
                self.remove(area)
        self.show()

    def show(self):
        if not self.areas:
            return
        for viewer in self.fight.match.players:
            if not viewer.spawned or viewer.client is None:
                continue
            for area in self.areas.values():
                if area.entity in viewer.seen:
                    continue
                viewer.client.queue_entities([world.EntityUpdate(
                    area.entity, world.OP_CREATE,
                    lambda origin, p=area.position, team=area.team: world.create(
                        origin, DEFINITION, {26: [0x800000 << team]},
                        flags=world.WITH_TRANSFORM | world.WITH_SCALE, position=(p[0], p[1] + 2, p[2]),
                    ),
                )])
                viewer.seen[area.entity] = True

    def remove(self, area):
        self.areas.pop(area.entity, None)
        for viewer in self.fight.match.players:
            if area.entity in viewer.seen:
                del viewer.seen[area.entity]
                if viewer.client is not None:
                    viewer.client.queue_entities([world.EntityUpdate(area.entity, world.OP_DESTROY)])
