"""Reconstructed Ana0267 ->0C71 ->0B99/0C72 healing-status host.

Manifest-verified104319 branches supply human healing100/damage60, lifetime4,
received-healing factors1.5/0, delayed ally modifier0.05 and immunity flags.
This routes the human branch instead of adding its damage to carrier50.
General PlayScript/CombatModFilter, AI branch tuning, native projectile gravity
and status presentation remain unimplemented; faction/routing/refresh are hosts.
"""

import math
from dataclasses import dataclass
from itertools import pairwise

from ow174.game import bots, stats
from ow174.game.script import expr, runtime

GRENADE = 0x04A0000000000267
TICK = .016
DURATION = 4.0
IMMUNITY_FLAGS = (6604, 8440, 478, 33551)  # actual0C72/node8 abort condition


@dataclass
class Modifier:
    source: int
    start: int
    end: int


class HealingEffects:
    def __init__(self, combat):
        self.combat = combat
        self.anti = {}
        self.amp = {}

    def retire(self, body):
        anti = self.anti.pop(body, None)
        amp = self.amp.pop(body, None)
        if anti is not None or amp is not None:
            target = next((t for t in self.combat._targets() if t.entity == body), None)
            if target is not None:
                self.sync(target)

    def install(self, table, body, source, start, end):
        previous = table.get(body)
        if previous is not None and previous.start <= self.combat.tick < previous.end:
            start = previous.start  # refresh keeps the active interval continuous, never stacks factors
        table[body] = Modifier(source, start, end)

    @staticmethod
    def immune(target):
        script = getattr(target.owner, 'body_script', None)
        component = getattr(script, 'component', None)
        if not isinstance(component, runtime.Component):
            return False
        return any(expr.truthy(var.value()) for key in IMMUNITY_FLAGS
                   if (var := component.entity_var(key, create=False)) is not None)

    def cleanse_negative(self, target):
        """Only the recognized grenade anti-heal, not a generic cleanse service."""
        self.anti.pop(target.entity, None)
        self.sync(target)

    def factor(self, target, seconds=0.0, *, end_tick=None):
        anti = self.anti.get(target.entity)
        amp = self.amp.get(target.entity)
        if anti is None and amp is None:
            return 1.0
        if anti is not None and self.immune(target):
            anti = None
        end = self.combat.tick if end_tick is None else end_tick

        def at(tick):
            if anti is not None and anti.start <= tick < anti.end:
                return 0.0
            return 1.5 if amp is not None and amp.start <= tick < amp.end else 1.0

        if seconds <= 0:
            return at(end)
        start = end-seconds/TICK
        points = {start, float(end)}
        for effect in (anti, amp):
            if effect is not None:
                points.update(value for value in (effect.start, effect.end) if start < value < end)
        ordered = sorted(points)
        total = sum((b-a)*at((a+b)/2) for a, b in pairwise(ordered))
        return total/(end-start)

    def sync(self, target):
        component = getattr(getattr(target.owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component):
            return
        tick = self.combat.tick
        anti, amp = self.anti.get(target.entity), self.amp.get(target.entity)
        blocked = anti is not None and anti.start <= tick < anti.end and not self.immune(target)
        boosted = amp is not None and amp.start <= tick < amp.end and not blocked
        component.set(8202, bool(blocked))  # actual0C72 status output
        component.set(8203, bool(boosted))  # actual0B99 modifier output
        component.run_now()

    def update(self):
        targets = {t.entity: t for t in self.combat._targets()}
        for body in set(self.anti) | set(self.amp):
            target = targets.get(body)
            if target is None or self.combat.vitals_of(target).dead:
                self.retire(body)
                if target is not None:
                    self.sync(target)
                continue
            anti = self.anti.get(body)
            if anti is not None and (anti.end <= self.combat.tick or self.immune(target)):
                del self.anti[body]
            amp = self.amp.get(body)
            if amp is not None and amp.end <= self.combat.tick:
                del self.amp[body]
            self.sync(target)

    def grenade(self, rocket, point, visible_from, extra_barrier=None):
        from ow174.game.combat import _source_entity, segment_distance

        fight = self.combat
        source = _source_entity(rocket.shooter)
        for target in fight._targets():
            vitals = fight.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b)-target.radius)
            scalar = rocket.blast.splash(distance)
            if scalar <= 0 or not fight.area_visible(rocket.shooter, visible_from, target, extra_barrier):
                continue
            allied = (target.owner is rocket.shooter or (not fight.match.game_map.free_for_all
                                                        and target.team == rocket.shooter.team))
            end = fight.tick+math.ceil(DURATION/TICK)
            if allied:
                #0B99 immediate ModifyHealth precedes its0.05s modifier wait.
                given = vitals.heal(100.0*scalar*self.factor(target))
                if given > 0:
                    if not isinstance(rocket.shooter, bots.Bot):
                        rocket.shooter.stats.add(stats.HEALING, given)
                    fight.zenyatta.charge(rocket.shooter, given)
                    fight._sync_body_health(target.owner, vitals)
                self.install(self.amp, target.entity, source, fight.tick+math.ceil(.05/TICK), end)
            elif not self.immune(target):
                #0C72 human branch is60, replacing carrier50 rather than adding
                #110 damage. AI mode/difficulty overrides are not inferred.
                fight._damage(rocket.shooter, target, 60.0*scalar, False, rocket.blast.loadout, fight.tick,
                              world_tick=fight.tick)
                if not vitals.dead:
                    self.install(self.anti, target.entity, source, fight.tick, end)
            self.sync(target)
