"""Explicit static consumer for Zarya0244/025C's authored barrier pets.

DATA: pets13F4/03DA, model10D7 sphere1.5,0256 pool default200/ruleset1D43 and native
graph phase lifetime/counter. RECONSTRUCTED: capsule-centre attachment, private
server identity, whole-hit overkill consumption and break -> StatePet completion.
Native pet/PlayScript hosting, bone pose, energy/credit/filter policy and wire
replication remain open. This is a finite collision target, not body invulnerability.
"""
import math
from dataclasses import dataclass

from ow174.game import assassin, bots
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
    instance: runtime.Instance
    state_id: int
    definition: int
    maximum: float
    health: float
    caster: object
    caster_body: int

    @property
    def phase(self):
        return self.instance.states[self.state_id]

    def centre(self):
        if isinstance(self.owner, bots.Bot):
            data = mover_data(self.owner.body)
            mover = self.owner.control_mover
            height = data.crouch_height if mover is not None and mover.crouched else data.stand_height
            position = self.owner.position
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


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.live = {}
        self.seen = {}
        self.next_entity = 0

    def retire(self, body):
        self.live = {k: b for k, b in self.live.items() if b.body != body and b.caster_body != body}
        self.seen = {k: v for k, v in self.seen.items() if k[0] != body}

    @staticmethod
    def live_source(bubble):
        script = getattr(bubble.caster, 'body_script', None)
        return (script is not None and not script.retired
                and script.component is bubble.phase.instance.component
                and bubble.phase.active and not bubble.phase.ending and bubble.health > 0)

    def query_targets(self, state):
        """025C's two native searches only; centre range/cone/ranking are reconstructed."""
        if (state.instance.graph.index, state.index) not in ((0x25C, 4), (0x25C, 7)):
            return NotImplemented
        from ow174.game.combat import aim, capsule_visible

        component = state.instance.component
        caster = next((p for p in self.fight.match.players if p.body == component.entity), None)
        if (caster is None or caster.hero.guid != HERO or component.owner_dead
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
        if script is None or getattr(script, 'retired', False) or not hasattr(actor, 'mover'):
            # Retired scripts keep historical active-state bits; they no longer own live pets.
            self.live = {k: b for k, b in self.live.items() if b.caster is not actor}
            body = getattr(actor, 'body', None)
            self.seen = {k: stamp for k, stamp in self.seen.items() if k[0] != body}
            return None
        if actor.hero.guid != HERO:
            return None
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
            if phase is None or not phase.active or phase.ending or (dead_caster and index == 0x244):
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
                self.live[key] = Bubble(FIRST + self.next_entity % SPAN, target.owner, target_id,
                                         instance, state_id, 0x0400000000000000 | definition,
                                         maximum, maximum, actor, body)
                self.next_entity += 1
                if maximum > 0:
                    assassin.release_victim(self.fight, target_id)
                    self.fight.cleanse_frost(target_id)
                # Bridge 0C72's #478 cleanse predicate at authored barrier attachment.
                modifiers = self.fight.heal_modifiers.get(target_id)
                if modifiers is not None:
                    modifiers.pop("anti", None)
                    if not modifiers:
                        del self.fight.heal_modifiers[target_id]
            bubble = self.live.get(key)
            target = targets.get(bubble.body) if bubble is not None else None
            if (target is None or target.owner is not bubble.owner or self.fight.vitals_of(target).dead):
                self.live.pop(key, None)
                phase.end(True)
                script.component.run_now()

    def for_target(self, actor):
        entity = actor.entity if isinstance(actor, bots.Bot) else actor.body
        return next((b for b in self.live.values()
                     if b.owner is actor and b.body == entity and self.live_source(b)), None)

    def enemies(self, shooter, *, team=None):
        team = shooter.team if team is None else team
        found = []
        for bubble in self.live.values():
            if (self.live_source(bubble) and bubble.owner is not shooter
                    and (self.fight.match.game_map.free_for_all or bubble.owner.team != team)):
                found.append(bubble)
        return found

    def intercept(self, shooter, origin, direction, length, *, radius=0.0, team=None):
        best = None
        for bubble in self.enemies(shooter, team=team):
            distance = sphere(origin, direction, bubble.centre(), RADIUS + radius, length)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, bubble
        return best

    def blast_targets(self, shooter, origin, reach):
        from ow174.game.combat import ray_world

        for bubble in self.enemies(shooter):
            delta = tuple(a - b for a, b in zip(bubble.centre(), origin, strict=True))
            length = math.sqrt(sum(v * v for v in delta))
            distance = max(0.0, length - RADIUS)
            if distance > reach:
                continue
            if distance > 1e-6:
                direction = tuple(v / length for v in delta)
                limit = distance - 1e-6
                if (ray_world(self.fight._collision(), origin, direction, limit) is not None
                        or self.fight._shield_hit(shooter, origin, direction, limit) is not None):
                    continue
            yield bubble, distance

    def damage(self, bubble, amount, shooter, *, source_entity=None, credit_energy=True):
        dealt = min(bubble.health, max(0.0, amount))
        bubble.health = max(0.0, bubble.health-dealt)
        if dealt and credit_energy:
            #0256/n10 forwards actual pool loss to0242 using0146. Hosting0256 and
            #the upstream0011 filter remain open; retain the authored energy math.
            source = source_entity
            if source is None:
                source = shooter.entity if hasattr(shooter, 'position') else shooter.body
            component = bubble.caster.body_script.component
            runtime.send_message(component, 0x0240000000000146,
                                 {0x2A: expr.f32(dealt), 0x12: expr.Entity(source)},
                                 bubble.entity)
            component.run_now()
        if bubble.health <= 0 and dealt:
            bubble.phase.end(True)
            bubble.phase.instance.component.run_now()
            self.live.pop((bubble.caster_body, bubble.phase.instance.graph.index), None)
        return dealt
