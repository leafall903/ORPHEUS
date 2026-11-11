"""Explicit104319 Zarya shield consumer for cross-hero combat verification.

244/25C keep their authored input/timers/cooldowns.256 supplies baseline200
health, A2/25D damage forwarding and cleanse. Target selection, sphere sweeps,
whole-last-hit absorption and network entity hosting are reconstructed, not
generic native Pet/TrackTargets/status implementations. Absorbed damage
forwards0146 to the authored242 energy graph. Ruleset overrides and full
native collision/presentation remain open.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, mei, world
from ow174.game.script import expr, runtime

HERO = 0x02E0000000000068
SPECS = {0x0244: (2, 0x04000000000013F4), 0x025C: (10, 0x04000000000003DA)}
CAPACITY, RADIUS = 200.0, 1.5  # 256 baseline;13F4 model10D7 bounds about+/-1.5


def identity(actor):
    return actor.entity if isinstance(actor, bots.Bot) else actor.body


def feet(actor):
    return tuple(actor.position if isinstance(actor, bots.Bot) else actor.mover.position)


@dataclass
class Bubble:
    entity: int
    owner: object
    body: int
    target: object
    target_body: int
    graph: int
    token: tuple
    definition: int
    position: tuple
    health: float = CAPACITY


class Shields:
    def __init__(self, combat):
        self.combat = combat
        self.bubbles: dict[tuple[int, int], Bubble] = {}
        self.flags: dict[int, tuple] = {}

    def instance(self, owner, index):
        script = getattr(owner, 'body_script', None)
        if script is None or getattr(script, 'hero', None) != HERO:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == index), None)

    def dead(self, actor):
        if isinstance(actor, bots.Bot):
            vitals = self.combat.vitals.get(actor.entity)
            return actor.dead or (vitals is not None and vitals.dead)
        return self.combat.is_dead(actor)

    def valid(self, bubble):
        instance = self.instance(bubble.owner, bubble.graph)
        if (instance is None or bubble.owner.body != bubble.body
                or identity(bubble.target) != bubble.target_body):
            return False
        pet = instance.states.get(SPECS[bubble.graph][0])
        return (pet is not None and pet.active and (instance.id, pet.counter) == bubble.token
                and not self.dead(bubble.owner) and not self.dead(bubble.target))

    def shielded(self, actor):
        entity = identity(actor)
        return next((b for b in self.bubbles.values() if b.target_body == entity and self.valid(b)), None)

    def enemy(self, bubble, source):
        if identity(source) == bubble.target_body:
            return False
        return self.combat.match.game_map.free_for_all or source.team != bubble.target.team

    def prepare(self, owner, command):
        """Reconstruct25C's two target outputs before its authored button frame."""
        instance = self.instance(owner, 0x025C)
        if instance is None:
            return
        from ow174.game.combat import aim, capsule_visible

        candidates = []
        fight = self.combat
        if not (fight.is_dead(owner) or fight.is_stunned(owner) or fight.frozen(owner)):
            origin, direction = fight._eye(owner), aim(*command.aim())
            for target in fight._targets():
                if (target.owner is owner or fight.match.game_map.free_for_all or target.team != owner.team
                        or fight.vitals_of(target).dead or self.shielded(target.owner)):
                    continue
                point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                delta = tuple(b - a for a, b in zip(origin, point, strict=True))
                distance = math.sqrt(sum(x * x for x in delta))
                if not 0 < distance <= 30.0:
                    continue
                angle = math.degrees(math.acos(max(-1., min(1., sum(
                    a * b / distance for a, b in zip(delta, direction, strict=True))))))
                # The native weighted selector is not hosted; reconstruct a
                # ten-degree aim cone plus capsule extent, then angle/distance.
                if angle > 10.0 + math.degrees(math.atan2(target.radius, distance)):
                    continue
                a, b = target.segment()
                if (capsule_visible(fight.match.collision_world, origin, a, b, target.radius)
                        and not fight.walls.blocked(origin, point)):
                    candidates.append((angle, distance, target.entity))
        chosen = (expr.Entity(min(candidates)[2]),) if candidates else ()
        instance.write((0, 1691), chosen)
        instance.write((0, 375), chosen)

    def cleanse(self, actor):
        fight = self.combat
        entity = identity(actor)
        fight._clear_frost(actor)
        fight.stuns.pop(entity, None)
        vitals = fight.vitals.get(entity)
        if vitals is None:
            return
        vitals.clear_sleep()
        if vitals.healing_block_until <= fight.now:
            return
        cryo = mei.protected(actor)
        if cryo:
            # Settle the already-sampled portion under its old healing block.
            fight._cryo_healing(actor)
        vitals.healing_block_until = 0.
        if cryo and (history := fight.cryo_samples.get(entity)) is not None:
            history.note(fight.now, vitals.healing_block_until, vitals.healing_boost_until)

    def status(self, actor):
        entity = identity(actor)
        if entity in self.flags:
            return
        script = getattr(actor, 'body_script', None)
        if script is not None:
            component = script.component
            before = tuple(component.entity_var(key).base for key in (8440, 7350))
            self.flags[entity] = (component, before)
            for key in (8440, 7350):
                component.set(key, True)
            component.run_now()

    def unstatus(self, entity):
        if any(b.target_body == entity for b in self.bubbles.values()):
            return
        stored = self.flags.pop(entity, None)
        if stored is not None:
            component, before = stored
            for key, value in zip((8440, 7350), before, strict=True):
                component.set(key, value)
            component.run_now()

    def sync(self, owner):
        for index, (pet_index, definition) in SPECS.items():
            instance = self.instance(owner, index)
            if instance is None:
                continue
            pet = instance.states.get(pet_index)
            key = owner.body, index
            previous = self.bubbles.get(key)
            if pet is None or not pet.active or self.combat.is_dead(owner):
                if previous is not None:
                    self.remove(previous)
                continue
            entity = owner.body if index == 0x0244 else expr.to_entity(instance.find_var(0, 536).value())
            target = next((t for t in self.combat._targets() if t.entity == entity
                           and not self.combat.vitals_of(t).dead), None)
            if target is None:
                if previous is not None:
                    self.remove(previous, stop=True)
                continue
            token = instance.id, pet.counter
            if previous is not None and previous.token == token and previous.target_body == entity:
                continue
            if previous is not None:
                self.remove(previous)
            number = self.combat.next_bot
            if number >= 0xD0000000:
                raise ValueError('shield entity allocator exhausted')
            self.combat.next_bot += 1
            bubble = Bubble(number, owner, owner.body, target.owner, entity, index, token,
                            definition, feet(target.owner))
            self.bubbles[key] = bubble
            instance.write((0, 512), expr.Entity(number))
            self.cleanse(target.owner)
            self.status(target.owner)

    def remove(self, bubble, *, stop=False):
        if self.bubbles.pop((bubble.body, bubble.graph), None) is not bubble:
            return
        instance = self.instance(bubble.owner, bubble.graph)
        if instance is not None and instance.id == bubble.token[0]:
            pet = instance.states.get(SPECS[bubble.graph][0])
            if pet is not None and pet.counter == bubble.token[1]:
                instance.write((0, 512), None)
                if stop and pet.active:
                    pet.request(runtime.ABORT)
                    instance.component.run_now()
        for viewer in self.combat.match.players:
            if bubble.entity in viewer.seen:
                del viewer.seen[bubble.entity]
                if viewer.client is not None:
                    viewer.client.queue_entities([world.EntityUpdate(bubble.entity, world.OP_DESTROY)])
        self.unstatus(bubble.target_body)

    def retire(self, entity):
        for bubble in tuple(self.bubbles.values()):
            if entity in (bubble.body, bubble.target_body):
                self.remove(bubble, stop=True)

    def update(self):
        for player in self.combat.match.players:
            self.sync(player)
        for bubble in tuple(self.bubbles.values()):
            if not self.valid(bubble):
                self.remove(bubble, stop=True)
        self.show()

    def show(self):
        for bubble in self.bubbles.values():
            position = feet(bubble.target)
            moved = position != bubble.position
            bubble.position = position
            for viewer in self.combat.match.players:
                if not viewer.spawned or viewer.client is None:
                    continue
                if bubble.entity not in viewer.seen:
                    viewer.client.queue_entities([world.EntityUpdate(
                        bubble.entity, world.OP_CREATE,
                        lambda origin, d=bubble.definition, p=position, h=bubble.health, t=bubble.target.team:
                        world.create(origin, d, {**world.health(h, CAPACITY), 26: [0x800000 << t]},
                                     flags=world.WITH_TRANSFORM | world.WITH_SCALE, position=p),
                    )])
                    viewer.seen[bubble.entity] = True
                elif moved:
                    viewer.client.queue_entities([world.EntityUpdate(
                        bubble.entity, movement=world.Movement(position),
                    )])

    def cast(self, shooter, origin, direction, length):
        best = None
        for bubble in self.bubbles.values():
            if not self.valid(bubble) or not self.enemy(bubble, shooter):
                continue
            p = feet(bubble.target)
            centre = (p[0], p[1] + 1.0, p[2])
            offset = tuple(a - b for a, b in zip(origin, centre, strict=True))
            along = sum(a * b for a, b in zip(offset, direction, strict=True))
            square = sum(x * x for x in offset) - RADIUS**2
            discriminant = along * along - square
            if discriminant < 0:
                continue
            at = 0.0 if square <= 0 else -along - math.sqrt(discriminant)
            if 0 <= at <= length and (best is None or at < best[0]):
                best = at, bubble
        return best

    def damage(self, bubble, shooter, amount, frame):
        if not self.enemy(bubble, shooter) or amount <= 0 or not self.valid(bubble):
            return
        dealt = min(amount, bubble.health)
        bubble.health -= dealt
        component = bubble.owner.body_script.component
        runtime.send_message(component, 0x0240000000000146,
                             {0x2A: dealt, 0x12: expr.Entity(identity(shooter))}, bubble.entity)
        component.run_now()
        client = getattr(shooter, 'client', None)
        if client is not None:
            client.queue_combat_notice(bubble.entity, 25001, {
                '+0x78': {'+0x0': {'+0x0': bubble.entity}, '+0x4': max(1, round(dealt)), '+0x8': 0},
                '+0x84': frame & 0xFFFFFFFF,
            })
        if bubble.health <= 0:
            self.remove(bubble, stop=True)
        else:
            for viewer in self.combat.match.players:
                if viewer.client is not None and bubble.entity in viewer.seen:
                    viewer.client.queue_body_health(bubble.entity, world.health(bubble.health, CAPACITY))

    def absorb(self, target, shooter, amount, frame):
        bubble = self.shielded(target)
        if bubble is None or not self.enemy(bubble, shooter):
            return False
        self.damage(bubble, shooter, amount, frame)
        return True  # Last hit is blocked in full; remaining capacity is not hero health.

    def splash(self, shooter, blast, point, frame):
        for bubble, amount in self.splash_contacts(shooter, blast, point):
            self.damage(bubble, shooter, amount, frame)

    def splash_contacts(self, shooter, blast, point, *, blocked=None):
        from ow174.game.combat import capsule_visible

        hits = []
        for bubble in self.bubbles.values():
            if not self.valid(bubble) or not self.enemy(bubble, shooter):
                continue
            p = feet(bubble.target)
            centre = (p[0], p[1] + 1., p[2])
            distance = max(0., math.dist(point, centre) - RADIUS)
            scalar = blast.splash(distance)
            if scalar <= 0. or (blocked is not None and blocked(centre)):
                continue
            if (capsule_visible(self.combat.match.collision_world, point, centre, centre, RADIUS)
                    and not self.combat.walls.blocked(point, centre)):
                hits.append((bubble, blast.amount * scalar))
        return hits
