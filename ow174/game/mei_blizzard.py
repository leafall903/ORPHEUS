"""Static combat host for Mei's authored Blizzard volley and area."""

import math
from dataclasses import dataclass, field

from ow174.game import deflection, matrix, projectile_tags
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
    frost_due: dict[int, int] = field(default_factory=dict)


class Blizzard:
    def __init__(self, combat):
        self.combat = combat
        self.flights: list[Flight] = []
        self.areas: dict[int, Area] = {}

    def fired(self, player, state, shot_frame, origin, direction, *, activation, snapshot):
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
                                     gravity, lifetime, self.combat.tick, shot))
        return True

    def retire(self, body):
        for flight in tuple(self.flights):
            if flight.body == body:
                self.end_flight(flight)
        for area in tuple(self.areas.values()):
            if area.body == body:
                self.areas.pop(area.entity)

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

    def chord(self, flight, seconds, tick, reflections=0):
        destination = tuple(
            p + v * seconds - (flight.gravity * seconds**2 / 2 if axis == 1 else 0)
            for axis, (p, v) in enumerate(zip(flight.position, flight.velocity, strict=True))
        )
        delta = tuple(b - a for a, b in zip(flight.position, destination, strict=True))
        length = math.sqrt(sum(value * value for value in delta))
        direction = tuple(value / length for value in delta) if length else (0.0, -1.0, 0.0)
        distance = self.combat.cover_ray(flight.position, direction, length)
        limit = length if distance is None else distance
        absorbed = matrix.trace(self.combat, flight.owner, DEFINITION, flight.position, direction, limit)
        grasp = self.combat.grasp.trace(flight.owner, DEFINITION, flight.position, direction, limit)
        grasp_state = None
        if grasp is not None and (absorbed is None or grasp[0] < absorbed[0]):
            contact, target, grasp_state = grasp
            absorbed = contact, target
        reflected = None
        if reflections < deflection.MAX_SEGMENTS and projectile_tags.absorbable(DEFINITION):
            reflected = deflection.trace(self.combat, flight.owner, flight.position, direction, limit)
        if (absorbed is not None and (distance is None or absorbed[0] < distance)
                and (reflected is None or absorbed[0] <= reflected[0])):
            flight.flown += seconds * absorbed[0] / max(length, 1e-9)
            if grasp_state is not None:
                # The thrown carrier has no direct or explosive impact amount.
                self.combat.grasp.absorb(grasp_state, flight.owner, None, None, absorbed[0])
            self.end_flight(flight)
            return True
        if reflected is not None and (distance is None or reflected[0] < distance):
            contact, target = reflected
            spent = seconds * contact / max(length, 1e-9)
            impact_velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * spent,
                               flight.velocity[2])
            speed = math.sqrt(sum(value * value for value in impact_velocity))
            from ow174.game.combat import aim

            owner = target.owner
            facing = aim(owner.mover.yaw, owner.mover.pitch)
            shift = min(deflection.OFFSET / max(speed, 1e-9), seconds - spent)
            flight.position = tuple(
                p + incoming * contact + outgoing * speed * shift
                for p, incoming, outgoing in zip(flight.position, direction, facing, strict=True)
            )
            flight.velocity = tuple(speed * value for value in facing)
            flight.owner, flight.body = owner, owner.body
            flight.flown += spent + shift
            flight.left -= spent + shift
            if flight.shot is not None:
                self.combat.projectiles.flown(flight.shot, flight.flown)
                flight.shot = None
            remaining = seconds - spent - shift
            return self.chord(flight, remaining, tick, reflections + 1) if remaining > 0 else False
        if distance is not None and distance <= length:
            flight.flown += seconds * distance / max(length, 1e-9)
            point = tuple(p + d * distance for p, d in zip(flight.position, direction, strict=True))
            self.land(flight, point, tick)
            return True
        flight.position = destination
        flight.velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * seconds,
                           flight.velocity[2])
        flight.flown += seconds
        flight.left -= seconds
        return False

    def exposed(self, area, target):
        from ow174.game.combat import segment_distance

        centre = (area.position[0], area.position[1] + 0.5, area.position[2])
        a, b = target.segment()
        return (segment_distance(centre, a, b) - target.radius <= RADIUS
                and self.combat.visible_target(centre, target))

    def affect(self, area, now):
        until = min(now, area.ends)
        elapsed = max(0, until - area.last_ms)
        if not elapsed:
            return
        present = set()
        for target in self.combat._enemies(area.owner):
            if not self.exposed(area, target):
                continue
            dealt = self.combat._damage(area.owner, target, DAMAGE_RATE * elapsed / 1000, False,
                                        LOADOUT, self.combat.tick, world_tick=self.combat.tick)
            if not dealt or self.combat.vitals_of(target).dead:
                continue
            present.add(target.entity)
            # The native073C host refreshes its own0.2s loop. Reconstruct
            # exposure deadlines separately from damage; discard deadlines
            # across barrier-blocked ticks so their frost is never replayed.
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                self.combat.apply_mei_frost(
                    area.owner, target, channel=area.entity, now_ms=due,
                    initial=FROST_INITIAL, step=FROST_STEP,
                )
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
                self.areas.pop(area.entity)
