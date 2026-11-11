"""Scoped 064A Snowball landing and reconstructed 065E storm contacts."""

import math
from dataclasses import dataclass, field

from ow174.game import hero_torbjorn, phase_defenses
from ow174.game.script import expr

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
RADIUS, DAMAGE_RATE = 10.0, 20.0
DELAY_MS, DURATION_MS = 200, 4250
FROST_INITIAL, FROST_STEP, FROST_INTERVAL_MS = expr.f32(.5), expr.f32(.040000003), 200


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
        self.areas = {}

    def land(self, owner, point):
        fight = self.combat
        entity = fight.next_bot
        fight.next_bot += 1
        starts = fight.tick * 16 + DELAY_MS
        self.areas[entity] = Area(entity, owner, owner.body, point, starts, starts + DURATION_MS, starts)

    def retire(self, body):
        for entity, area in tuple(self.areas.items()):
            if area.body == body:
                del self.areas[entity]

    def update(self, tick):
        now = tick * 16
        for entity, area in tuple(self.areas.items()):
            if area.owner.body != area.body or area.owner.gone:
                del self.areas[entity]
                continue
            self.affect(area, now)
            if now >= area.ends:
                del self.areas[entity]

    def exposed(self, area, target):
        from ow174.game.combat import ray_world, segment_distance

        centre = (area.position[0], area.position[1] + .5, area.position[2])
        a, b = target.segment()
        if segment_distance(centre, a, b) - target.radius > RADIUS:
            return False
        destination = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        reach = math.dist(centre, destination)
        if reach <= 1e-9:
            return True
        ray = tuple((b - a) / reach for a, b in zip(centre, destination, strict=True))
        fight = self.combat
        return (ray_world(fight._collision(), centre, ray, reach - 1e-6) is None
                and fight._shield_trace(area.owner, centre, ray, reach, include_attached=False) is None)

    def affect(self, area, now):
        until = min(now, area.ends)
        elapsed = until - area.last_ms
        if elapsed <= 0:
            return
        fight = self.combat
        admitted = {}
        for target in fight._enemies(area.owner):
            if phase_defenses.phase(target.owner)[0] or not self.exposed(area, target):
                continue
            bubble = fight.bubbles.for_target(target.owner)
            amount = DAMAGE_RATE * elapsed / 1000
            if bubble is not None:
                fight.bubbles.damage(bubble, amount, area.owner)
                continue
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(area.owner, target, amount, False, LOADOUT, fight.tick,
                          world_tick=fight.tick, armour_reduction=False)
            if vitals.total >= before or vitals.dead:
                continue
            due = max(area.last_ms, area.frost_due.get(target.entity, area.last_ms))
            if getattr(getattr(target.owner, "hero", None), "guid", None) == hero_torbjorn.HERO:
                while due < until:
                    fight.frost.apply(area.owner, target, tick=fight.tick, channel=area.entity,
                                      initial=FROST_INITIAL, step=FROST_STEP, at_ms=due)
                    due += FROST_INTERVAL_MS
                admitted[target.entity] = due
        area.frost_due = admitted
        area.last_ms = until
