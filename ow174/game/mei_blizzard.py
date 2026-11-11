"""Reconstructed Mei Blizzard host for 104319 064A/0946/065E/0B77.

The native projectile/contact/target/status hosts are not implemented by the
permissive VM. Keep this consumer scoped to 064A state 3. Per-tick damage,
chord sweeps, first-contact anchoring and aggregate frost are reconstructed;
adapted from the Mei lane to the Roadhog combat and control hosts.
"""

import math
from dataclasses import dataclass, field

from ow174.game import defenses, world
from ow174.game.area_projectile import FRAME_MS, BallisticFlights

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
RADIUS, DAMAGE_RATE = 10.0, 20.0
DELAY_MS, DURATION_MS = 200, 4250
FROST_INTERVAL_MS = 200


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


class Blizzard(BallisticFlights):
    projectile = DEFINITION

    def __init__(self, combat):
        super().__init__(combat)
        self.areas: dict[int, Area] = {}

    def fired(self, player, state, shot_frame, origin, direction, *, activation, snapshot):
        if state.instance.graph.index != 0x064A or state.index != 3:
            return False
        self.launch(player, state, shot_frame, origin, direction, activation=activation, snapshot=snapshot)
        return True

    def retire(self, body):
        super().retire(body)
        for area in tuple(self.areas.values()):
            if area.body == body:
                self.remove(area)

    def land(self, flight, point, tick, victim=None):
        self.end_flight(flight)
        entity = self.combat.next_bot
        if entity >= 0xD0000000:
            raise ValueError("Blizzard entity allocator exhausted")
        self.combat.next_bot += 1
        starts = tick * FRAME_MS + DELAY_MS
        area = Area(entity, flight.owner, flight.body, point, starts, starts + DURATION_MS, starts)
        self.areas[entity] = area

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

        if self.combat.vitals_of(target).dead or defenses.protection(target.owner)[0]:
            return False
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
            fight = self.combat
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(area.owner, target, DAMAGE_RATE * elapsed / 1000, False,
                          LOADOUT, fight.tick, world_tick=fight.tick)
            if vitals.total >= before or vitals.dead:
                continue
            present.add(target.entity)
            # 073C starts the authored 0.2-second loop. Shielded and occluded
            # intervals do not bank frost pulses for later exposure.
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                fight._mei_frost_contact(area.owner, target, channel=area.entity, floor=5000, increment=400)
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
