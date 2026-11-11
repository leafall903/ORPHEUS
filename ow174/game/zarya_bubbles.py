"""Host the authored Zarya pet phases as finite, attached server barriers."""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000068
RADIUS = 1.5  # 10D7.00C MSTU/m_CB4D298D/STU_4614D0E5.m_radius
FIRST, SPAN = 0xA0100000, 0x00040000  # private static identities; no native wire acceptance


@dataclass
class Bubble:
    entity: int
    owner: object
    body: int
    graph_index: int
    instance_id: int
    counter: int
    start: int
    definition: int
    maximum: float
    health: float
    caster: object
    caster_body: int

    def centre(self):
        if isinstance(self.owner, bots.Bot):
            data = mover_data(self.owner.body)
            height, position = data.stand_height, self.owner.position
        else:
            data = mover_data(self.owner.hero.body)
            height = data.crouch_height if self.owner.mover.crouched else data.stand_height
            position = self.owner.mover.position
        x, y, z = position
        return x, y + height * .5, z


def sphere(origin, direction, centre, radius, length):
    delta = tuple(p-c for p, c in zip(origin, centre, strict=True))
    c = sum(p*p for p in delta) - radius*radius
    if c <= 0:
        return 0.0
    b = sum(p*d for p, d in zip(delta, direction, strict=True))
    discriminant = b*b-c
    if discriminant < 0:
        return None
    distance = -b-math.sqrt(discriminant)
    return distance if 0 <= distance <= length else None


class ZaryaBubbles:
    def __init__(self, fight):
        self.fight = fight
        self.live = {}
        self.seen = {}
        self.next_entity = 0

    def retire(self, body):
        self.live = {k: b for k, b in self.live.items() if b.body != body and b.caster_body != body}
        self.seen = {k: v for k, v in self.seen.items() if k[0] != body}

    @staticmethod
    def phase(bubble):
        script = getattr(bubble.caster, 'body_script', None)
        if script is None or script.retired or script.component.entity != bubble.caster_body:
            return None
        instance = script.component.instances.get(bubble.instance_id)
        if instance is None or instance.graph.index != bubble.graph_index:
            return None
        state = instance.states.get(2 if bubble.graph_index == 0x244 else 10)
        if (state is not None and state.active
                and (state.counter, state.start) == (bubble.counter, bubble.start)):
            return state
        return None

    def live_source(self, bubble):
        return bubble.health > 0 and self.phase(bubble) is not None

    def query_targets(self, state):
        """025C's two native searches only; centre range/cone/ranking are reconstructed."""
        component = state.instance.component
        if (component.strict or component.world.hero != HERO
                or (state.instance.graph.index, state.index) not in ((0x25C, 4), (0x25C, 7))):
            return NotImplemented
        from ow174.game.combat import aim, capsule_visible

        caster = next((p for p in self.fight.match.players if p.body == component.entity), None)
        if (caster is None or caster.body_script is None or caster.body_script.component is not component
                or caster.gone or not caster.spawned or component.owner_dead
                or self.fight.match.game_map.free_for_all):
            return ()
        origin = self.fight._eye(caster)
        forward = aim(caster.mover.yaw, caster.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field('m_radius'), watch=False))
        factor = component.entity_var(9595, create=False)
        factor = factor.value() if factor is not None else None
        # Native angle input is10*entity9595. Its absent engine default1 is explicit reconstruction.
        angle = 10.0 if factor is None else expr.to_float(state.evaluate(
            state.node.field('m_A30B383E'), watch=False))
        candidates = []
        protected = {b.body for b in self.live.values() if self.live_source(b)}
        for target in self.fight._targets():
            if (target.owner is caster or target.team != caster.team or target.entity in protected
                    or self.fight.vitals_of(target).dead):
                continue
            a, b = target.segment()
            centre = tuple((x+y)*.5 for x, y in zip(a, b, strict=True))
            distance = math.dist(origin, centre)
            if not 0 < distance <= reach:
                continue
            direction = tuple((x-y)/distance for x, y in zip(centre, origin, strict=True))
            alignment = sum(x*y for x, y in zip(direction, forward, strict=True))
            if alignment < math.cos(math.radians(angle)):
                continue
            if not capsule_visible(self.fight._collision(), origin, a, b, target.radius):
                continue
            candidates.append(((-alignment, distance, target.entity), target.entity))
        if not candidates:
            return ()
        return (expr.Entity(min(candidates)[1]),)

    def sync(self, actor):
        script = getattr(actor, 'body_script', None)
        if script is None or script.hero != HERO or script.retired or script.component.strict:
            return
        body = script.component.entity
        if not actor.has_body or not actor.spawned:
            self.retire(body)
            return None
        dead_caster = script.component.owner_dead or self.fight.is_dead(actor)
        targets = {t.entity: t for t in self.fight._targets()}
        for index, state_id, definition in ((0x244, 2, 0x13F4), (0x25C, 10, 0x3DA)):
            key = body, index
            instance = next((i for i in script.component.instances.values() if i.graph.index == index), None)
            phase = instance.states.get(state_id) if instance is not None else None
            # Death prevents new casts, but a live script's already-running025C
            # pet timer is independent of Ability.stop. Retire only with its phase.
            if phase is None or not phase.active or (dead_caster and index == 0x244):
                self.live.pop(key, None)
                continue
            stamp = instance.id, phase.counter, phase.start
            if self.seen.get(key) != stamp:
                self.seen[key] = stamp
                target_id = body if index == 0x244 else expr.to_entity(instance.vars[536].value())
                target = targets.get(target_id)
                if target is None or self.fight.vitals_of(target).dead:
                    continue
                pool = graph.graph(0x256).states[0].field('m_amount')
                default = pool['m_configVars'][1]['m_A53100D5']['m_value']
                configured = script.world.ruleset(0x1D43)
                maximum = expr.f32(default if configured is None else configured)
                self.live[key] = Bubble(
                    FIRST + self.next_entity % SPAN, target.owner, target_id, index, instance.id,
                    phase.counter, phase.start, 0x0400000000000000 | definition,
                    maximum, maximum, actor, body,
                )
                self.next_entity += 1
                self.fight.cleanse_antiheal(target_id)
            bubble = self.live.get(key)
            target = targets.get(bubble.body) if bubble is not None else None
            if (target is None or target.owner is not bubble.owner or self.fight.vitals_of(target).dead
                    or (index == 0x25C and target.team != actor.team)):
                self.live.pop(key, None)
                phase.end(True)
                script.component.run_now()

    def for_target(self, actor):
        for caster in self.fight.match.players:
            self.sync(caster)
        entity = actor.entity if isinstance(actor, bots.Bot) else actor.body
        return next((b for b in self.live.values()
                     if b.owner is actor and b.body == entity and self.live_source(b)), None)

    def enemies(self, shooter):
        found = []
        owner = getattr(shooter, "combat_owner", None) or shooter
        for caster in self.fight.match.players:
            self.sync(caster)
        for bubble in self.live.values():
            if (self.live_source(bubble) and bubble.owner is not owner
                    and (self.fight.match.game_map.free_for_all or bubble.owner.team != shooter.team)):
                found.append(bubble)
        return found

    def trace(self, shooter, origin, direction, length, *, radius=0.0):
        best = None
        for bubble in self.enemies(shooter):
            distance = sphere(origin, direction, bubble.centre(), RADIUS + radius, length)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, bubble
        return best

    def damage(self, bubble, amount, shooter):
        dealt = min(bubble.health, max(0.0, amount))
        bubble.health = max(0.0, bubble.health-dealt)
        if dealt:
            #0256/n10 forwards actual pool loss to0242 using0146. Hosting0256 and
            #the upstream0011 filter remain open; retain the authored energy math.
            source = shooter.entity if isinstance(shooter, bots.Bot) else shooter.body
            component = bubble.caster.body_script.component
            runtime.send_message(component, 0x0240000000000146,
                                 {0x2A: expr.f32(dealt), 0x12: expr.Entity(source)},
                                 bubble.entity)
            component.run_now()
        if bubble.health <= 0 and dealt:
            phase = self.phase(bubble)
            if phase is not None:
                phase.end(True)
                phase.instance.component.run_now()
            self.live.pop((bubble.caster_body, bubble.graph_index), None)
        return dealt

    def area(self, shooter, centre, radius, amount, *, contacted=None):
        """One pool hit per area event; no same-hit overflow onto its protected body."""
        from ow174.game.combat import capsule_visible

        protected = set()
        for bubble in self.enemies(shooter):
            point = bubble.centre()
            distance = math.dist(centre, point)
            if distance > radius + RADIUS:
                continue
            if not capsule_visible(self.fight._collision(), self.fight._eye(shooter), point, point, RADIUS):
                continue
            protected.add(bubble.body)
            if contacted is None or bubble.entity not in contacted:
                self.damage(bubble, amount, shooter)
                if contacted is not None:
                    contacted.add(bubble.entity)
        return protected
