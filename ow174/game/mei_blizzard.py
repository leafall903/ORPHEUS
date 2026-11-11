"""Scoped 064A snowball and 065E field on the Match host."""

from dataclasses import dataclass, field

from ow174.game import mei_frost
from ow174.game.script import expr

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
# Extracted 104319 065E and 0B77 configuration outside the hero closure.
RADIUS, DAMAGE_RATE = 10.0, 20.0
DELAY_MS, DURATION_MS = 200, 4250
FROST_INITIAL, FROST_STEP, FROST_INTERVAL_MS = expr.f32(0.5), expr.f32(0.040000003), 200


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
    def __init__(self, combat):
        self.combat = combat
        self.areas: dict[int, Area] = {}

    def land(self, rocket, point):
        if rocket.shooter.body != rocket.blizzard_body or rocket.shooter.gone or self.combat.match.ended:
            return
        entity = self.combat.next_bot
        if entity >= 0xD0000000:
            raise ValueError("Blizzard entity allocator exhausted")
        self.combat.next_bot += 1
        starts = self.combat.tick * 16 + DELAY_MS
        self.areas[entity] = Area(entity, rocket.shooter, rocket.blizzard_body, rocket.team,
                                  tuple(point), starts, starts + DURATION_MS, starts)

    def retire(self, body):
        for entity, area in tuple(self.areas.items()):
            if area.body == body:
                del self.areas[entity]

    def charging_blocked(self, body):
        return (any(area.body == body for area in self.areas.values())
                or any(rocket.blizzard_body == body for rocket in self.combat.rockets))

    def update(self, tick):
        if not self.areas:
            return
        now = tick * 16
        for entity, area in tuple(self.areas.items()):
            if area.owner.body != area.body or area.owner.gone or self.combat.match.ended:
                del self.areas[entity]
                continue
            self.affect(area, now, tick)
            if now >= area.ends:
                del self.areas[entity]

    def affect(self, area, now, tick):
        from ow174.game.combat import capsule_visible, segment_distance

        until = min(now, area.ends)
        elapsed = until - area.last_ms
        if elapsed <= 0:
            return
        present = {}
        x, y, z = area.position
        centre = (x, y + 0.5, z)
        for target in self.combat._enemies(area.owner, team=area.team):
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius > RADIUS
                    or not capsule_visible(
                        self.combat._collision(), centre, a, b, target.radius, walls=self.combat.walls,
                    )):
                continue
            dealt = self.combat._damage(area.owner, target, DAMAGE_RATE * elapsed / 1000, False,
                                        LOADOUT, tick, world_tick=tick, damage_over_time=True)
            if not dealt or self.combat.vitals_of(target).dead:
                continue
            due = area.frost_due.get(target.entity, area.last_ms)
            while due < until:
                mei_frost.contact(self.combat, area.owner, target, tick, channel=area.entity,
                                  now_ms=due, initial=FROST_INITIAL, step=FROST_STEP)
                due += FROST_INTERVAL_MS
            present[target.entity] = due
        area.frost_due = present
        area.last_ms = until
