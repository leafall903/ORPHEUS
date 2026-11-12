"""Selected Ana0267 received-healing host, using prepared104319 graph values.

Ordinary player-source branch, world clock, faction/LOS, refresh and nonstacking
are explicit reconstruction policies. Native PlayScript/filter ownership, AI
sources, effect replication and presentation remain unaccepted.
"""

import math
from dataclasses import dataclass
from itertools import pairwise

from ow174.game import actors, bots
from ow174.game.script import expr, graph, healing_status, runtime

GRENADE = 0x04A0000000000267
TICK = 0.016


class _HumanSource(expr.Context):
    def native(self, cfg):
        if cfg.get("$") == "STU_D3EB8D87":
            return False  # declared ordinary player-source branch, not a native predicate
        raise ValueError(f"unbound healing-profile value {cfg.get('$')}")


@dataclass(frozen=True)
class Spec:
    heal: float
    damage: float
    duration: float
    delay: float
    amp: float
    anti: float
    excluded: tuple
    immunity: tuple

    @classmethod
    def load(cls):
        ally, enemy = graph.graph(0x0B99), graph.graph(0x0C72)
        if ally is None or enemy is None or graph.graph(0x0C71) is None:
            return None
        context = _HumanSource()
        def value(cfg):
            return expr.to_float(expr.evaluate(cfg, context))
        amp, anti = ally.node(8), enemy.node(17)
        if any(expr.guid_of(n.field("m_60703B1E")) & 0xFFFF != 0x031A for n in (amp, anti)):
            raise ValueError("unexpected received-healing modifier")
        duration = value(ally.node(1).field("m_timeout"))
        if duration != value(enemy.node(1).field("m_timeout")):
            raise ValueError("different selected status lifetimes")
        return cls(value(ally.node(7).field("m_value")), value(enemy.node(6).field("m_value")),
                   duration, value(ally.node(17).field("m_timeout")),
                   value(amp.field("m_50530EAB")), value(anti.field("m_50530EAB")),
                   tuple(expr.guid_of(t) for t in anti.field("m_5CFFCBAE") or ()),
                   tuple(v["m_identifier"] for v in
                         enemy.node(8).field("m_condition")["m_expression"]["m_dynamicVars"]))


@dataclass
class Modifier:
    source: int
    start: int
    end: int


class HealingEffects(healing_status.Statuses):
    def __init__(self, combat):
        super().__init__(combat.match)
        self.combat = combat
        self.spec = None
        self.anti = {}
        self.amp = {}

    def retire(self, entity):
        self.combat.record_cryo_status(entity)
        super().retire(entity)
        self.anti.pop(entity, None)
        self.amp.pop(entity, None)
        self.combat.record_cryo_status(entity)

    def immune(self, target):
        component = getattr(getattr(target.owner, "body_script", None), "component", None)
        return self.spec is not None and isinstance(component, runtime.Component) and any(
            expr.truthy(var.value()) for key in self.spec.immunity
            if (var := component.entity_var(key, create=False)) is not None)

    def record_regeneration(self, entity):
        """Retain delivered Mercy passive scale changes at status timer boundaries."""
        from ow174.game.script import regeneration

        target = next((t for t in self.combat._targets() if t.entity == entity), None)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if target is not None and getattr(script, "hero", None) == 0x02E0000000000068:
            # Settle the existing shield ledger before this actual status interval
            # is installed, refreshed, removed or pruned; never discard its history.
            vitals = self.combat.vitals.get(entity)
            if vitals is not None:
                self.combat._recover_shields(target.owner, vitals, self.combat.now)
        if (getattr(script, "hero", None) != regeneration.MERCY or script.entity != entity
                or script.retired or script.component.owner_dead):
            return
        tick = self.combat.tick
        history = getattr(script.world, "passive_healing_scales", ())
        previous = history[-1][0] if history else script.frame
        anti, amp = self.anti.get(entity), self.amp.get(entity)
        boundaries = {effect.end for effect in (anti, amp)
                      if effect is not None and previous < effect.end <= tick}
        for boundary in sorted(boundaries):
            scale = (1.0 if self.spec is None else
                     self.spec.anti if anti is not None and anti.start <= boundary < anti.end else
                     self.spec.amp if amp is not None and amp.start <= boundary < amp.end else 1.0)
            regeneration.record_healing_scale(script, boundary, scale)
        regeneration.record_healing_scale(script, tick, self.factor(target))

    def prepare(self):
        """Clean dead/retired recipients, retaining expired intervals for current HOT integration."""
        self.update(self.combat.tick)
        targets = {t.entity: t for t in self.combat._targets()}
        for entity in set(self.anti) | set(self.amp):
            self.record_regeneration(entity)
            target = targets.get(entity)
            if target is None or self.combat.vitals_of(target).dead:
                self.retire(entity)
            elif self.immune(target):
                self.combat.record_cryo_status(entity)
                self.anti.pop(entity, None)
                self.record_regeneration(entity)
                self.combat.record_cryo_status(entity)

    def prune(self):
        # Settle the existing shield ledger while authored filter transitions
        # still cover this sparse interval, before discarding old VM history.
        for entity in tuple(self.filter_history):
            self.record_regeneration(entity)
        self.prune_filters(self.combat.tick)
        for entity in set(self.anti) | set(self.amp):
            self.record_regeneration(entity)
            self.combat.record_cryo_status(entity)
        for table in (self.anti, self.amp):
            for entity, effect in list(table.items()):
                if effect.end <= self.combat.tick:
                    del table[entity]

    def factor(self, target, *, tags=(), seconds=0.0, end_tick=None):
        anti, amp = self.anti.get(target.entity), self.amp.get(target.entity)
        dynamic = target.entity in self.filter_history or any(
            item.target == target.entity for item in self.applied.values())
        if not dynamic and (self.spec is None or (anti is None and amp is None)):
            return 1.0
        if anti is not None and (self.immune(target) or set(tags).intersection(self.spec.excluded)):
            anti = None

        def at(tick):
            ordinary = (self.spec.anti if anti is not None and anti.start <= tick < anti.end else
                        self.spec.amp if amp is not None and amp.start <= tick < amp.end else 1.0)
            authored = self.scale(target.entity, tags, frame=tick) if dynamic else 1.0
            return 0.0 if min(ordinary, authored) <= 0.0 else max(ordinary, authored)

        end = self.combat.tick if end_tick is None else end_tick
        if seconds <= 0:
            return at(end)
        start = end - seconds / TICK
        points = {start, float(end)}
        points.update(tick for tick, _ in self.filter_history.get(target.entity, ()) if start < tick < end)
        for effect in (anti, amp):
            if effect is not None:
                points.update(v for v in (effect.start, effect.end) if start < v < end)
        return sum((b - a) * at((a + b) / 2) for a, b in pairwise(sorted(points))) / (end - start)

    def _install(self, table, entity, source, start, end):
        self.combat.record_cryo_status(entity)
        self.record_regeneration(entity)
        previous = table.get(entity)
        if previous is not None and previous.start <= self.combat.tick < previous.end:
            start = previous.start
        table[entity] = Modifier(source, start, end)
        self.record_regeneration(entity)
        self.combat.record_cryo_status(entity)

    def accepts(self, rocket):
        if rocket.blast is None or rocket.blast.effect != GRENADE or isinstance(rocket.shooter, bots.Bot):
            return False
        if self.spec is None:
            self.spec = Spec.load()
        return self.spec is not None

    def grenade(self, rocket, point, *, blockers=(), victims=None, broken_particle_barriers=None):
        if not self.accepts(rocket):
            return False  # missing profile is not status support
        from ow174.game.combat import capsule_visible, segment_distance

        fight, source = self.combat, rocket.source_entity
        selected = (fight._targets() if victims is None else
                    (target for target, _ in victims))
        scales = None if victims is None else {target.entity: amount / rocket.blast.amount for target, amount in victims}
        for target in selected:
            vitals = fight.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if scales is None:
                scalar = rocket.blast.splash(max(0.0, segment_distance(point, a, b) - target.radius))
                other_pillars = (pillar for pillar in blockers if pillar.entity != target.entity)
                if scalar <= 0 or not capsule_visible(
                    fight._collision(), point, a, b, target.radius, blockers=other_pillars,
                ):
                    continue
            else:
                scalar = scales[target.entity]
            allied = target.owner is rocket.shooter or (
                not fight.match.game_map.free_for_all and target.team == rocket.team)
            if isinstance(target.owner, actors.Deployable):
                if not allied:
                    fight._damage(rocket.shooter, target, rocket.blast.amount * scalar, False,
                                  rocket.blast.loadout, fight.tick, source_body=source)
                continue
            end = fight.tick + math.ceil(self.spec.duration / TICK)
            # Same declared one-boost Matrix policy used by the weapon flight.
            strength = scalar * (2.0 if rocket.amplified else 1.0)
            if allied:
                fight._heal_target(rocket.shooter, target, self.spec.heal * strength, source_entity=source)
                self._install(self.amp, target.entity, source,
                              fight.tick + math.ceil(self.spec.delay / TICK), end)
            else:
                # C72's ModifyHealth and its negative-status teardown are distinct.
                # These cleanup flags do not establish a general damage-immunity rule.
                landed = fight._damage(
                    rocket.shooter, target, self.spec.damage * strength, False, rocket.blast.loadout,
                    fight.tick, world_tick=fight.tick, source_body=source,
                    broken_particle_barriers=broken_particle_barriers,
                )
                if landed and not vitals.dead and not self.immune(target):
                    self._install(self.anti, target.entity, source, fight.tick, end)
        return True
