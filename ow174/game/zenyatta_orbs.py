"""Hosted 104319 Zenyatta support effects."""

import math
from dataclasses import dataclass

from ow174.game import body_protection, sigma_hyperspheres, tracer, ultimate, zarya_barrier
from ow174.game.script import expr

HERO = ultimate.ZENYATTA
DISCORD, HARMONY = 0x0176, 0x02F3
DISCORD_BUTTON, HARMONY_BUTTON = 8, 16
TRANSCENDENCE = 0x017B
ORB_TARGET = 1606
DEFAULT_ANGLE = 10.0  # both cast-search expressions use 10 before their optional aim adjustment
DISCORD_SCALE = 1.25
TRANSCENDENCE_RADIUS = 10.0  # base radius in 017B state 0
TRANSCENDENCE_RATE = 300.0  # selected host rate while effect 0067 lacks a resolved binding


@dataclass
class Link:
    owner: object
    target: int
    reach: float
    grace: int
    last_visible: int
    rate: float


@dataclass
class Transcendence:
    owner: object
    start: int
    last: int


class Orbs:
    def __init__(self, fight):
        self.fight = fight
        self.links = {}
        self.buttons = {}
        self.ready = {}
        self.transcendence = {}

    def _allied(self, owner, target):
        return not self.fight.match.game_map.free_for_all and owner.team == target.team

    def _visible(self, origin, target):
        from ow174.game.combat import capsule_visible

        return capsule_visible(
            self.fight._collision(), origin, *target.segment(), target.radius, walls=self.fight.walls,
        )

    @staticmethod
    def _offset(origin, target):
        x, y, z = target.feet
        return x - origin[0], y + target.eye - origin[1], z - origin[2]

    def _select(self, owner, command, healing, reach, angle):
        from ow174.game.combat import _dot, aim

        if reach <= 0 or angle < 0:
            return None
        origin, direction = self.fight._eye(owner), aim(*command.aim())
        threshold = math.cos(math.radians(angle))
        candidates = []
        for target in self.fight._targets():
            if (target.owner is owner or self._allied(owner, target) != healing
                    or self.fight.vitals_of(target).dead):
                continue
            if not healing and (tracer.recalling(target.owner) or body_protection.active(target.owner)
                                or self.protects(target.owner)
                                or zarya_barrier.active(target.owner) is not None):
                continue
            offset = self._offset(origin, target)
            distance = math.hypot(*offset)
            if not 0 < distance <= reach or _dot(offset, direction) < threshold * distance:
                continue
            if not self._visible(origin, target):
                continue
            ray = tuple(axis / distance for axis in offset)
            if not healing and sigma_hyperspheres.barrier_contact(self.fight, owner, origin, ray, distance):
                continue
            candidates.append((-_dot(ray, direction), distance, target.entity))
        return min(candidates)[2] if candidates else None

    @staticmethod
    def _instance(owner, graph):
        script = owner.body_script
        if script is None or script.retired:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == graph), None)

    def _drop(self, key):
        link = self.links.pop(key)
        if link.owner.body == key[0]:
            instance = self._instance(link.owner, key[1])
            if instance is not None:
                instance.write((expr.INSTANCE, ORB_TARGET), 0)

    def retire(self, body):
        self.transcendence.pop(body, None)
        self.buttons.pop(body, None)
        self.ready.pop((body, HARMONY), None)
        self.ready.pop((body, DISCORD), None)
        for key, link in tuple(self.links.items()):
            if key[0] == body or link.target == body:
                self._drop(key)

    def clear_discord(self, body):
        for key, link in tuple(self.links.items()):
            if key[1] == DISCORD and link.target == body:
                self._drop(key)

    def command(self, owner, command):
        from ow174.game.combat import TICK

        if owner.hero.guid != HERO or command is None or self.fight.is_dead(owner):
            return
        self._observe_transcendence(owner)
        pressed = command.buttons & ~self.buttons.get(owner.body, 0)
        self.buttons[owner.body] = command.buttons
        if (not pressed & (DISCORD_BUTTON | HARMONY_BUTTON)
                or self.fight.is_stunned(owner) or self.protects(owner)):
            return
        for button, graph, search, grace_node in (
            (HARMONY_BUTTON, HARMONY, 40, 69),
            (DISCORD_BUTTON, DISCORD, 41, 71),
        ):
            key = owner.body, graph
            if not pressed & button or self.fight.tick < self.ready.get(key, 0):
                continue
            instance = self._instance(owner, graph)
            if instance is None:
                continue
            query = instance.graph.node(search)
            reach = expr.to_float(instance.evaluate(query.field("m_radius")))
            adjust = instance.component.entity_var(9596 if graph == HARMONY else 9597, create=False)
            angle = DEFAULT_ANGLE
            if adjust is not None and adjust.value() is not None:
                angle = expr.to_float(instance.evaluate(query.field("m_A30B383E")))
            target = self._select(owner, command, graph == HARMONY, reach, angle)
            if target is None:
                continue
            grace = expr.to_float(instance.evaluate(instance.graph.node(grace_node).field("m_timeout")))
            recovery = expr.to_float(instance.evaluate(instance.graph.node(43).field("m_timeout")))
            rate = 0.0
            if graph == HARMONY:
                rate = expr.to_float(instance.evaluate(instance.graph.node(11).field("m_value")))
            self.links[key] = Link(owner, target, reach, math.ceil(grace / TICK), self.fight.tick, rate)
            self.ready[key] = self.fight.tick + math.ceil(recovery / TICK)
            instance.write((expr.INSTANCE, ORB_TARGET), expr.Entity(target))
            break

    def _observe_transcendence(self, owner):
        ultimate = self._instance(owner, TRANSCENDENCE)
        active = ultimate.states.get(3) if ultimate is not None else None
        if active is None or not active.active:
            self.transcendence.pop(owner.body, None)
            return
        if owner.body in self.transcendence:
            return
        duration = expr.to_float(active.evaluate(active.node.field("m_EB0FC261"), watch=False))
        rate = expr.to_float(active.evaluate(active.node.field("m_9B0385B4"), watch=False))
        if duration <= 0 or rate <= 0:
            return
        frame_ms = active.instance.component.frame_ms
        last = math.ceil((active.start + duration / rate * 1000) / frame_ms) - 1
        if last < self.fight.tick:
            return
        self.transcendence[owner.body] = Transcendence(owner, self.fight.tick, last)
        self.clear_discord(owner.body)
        target = next((target for target in self.fight._targets() if target.entity == owner.body), None)
        if target is not None:
            self.fight._heal(owner, target, self.fight.vitals_of(target).maximum)

    def protects(self, owner):
        effect = self.transcendence.get(getattr(owner, "body", None))
        return effect is not None and effect.owner is owner and effect.start <= self.fight.tick <= effect.last

    def _heal_transcendence(self, targets, tick):
        from ow174.game.combat import TICK

        for body, effect in tuple(self.transcendence.items()):
            owner = effect.owner
            if tick > effect.last or body not in targets or owner.body != body or self.fight.is_dead(owner):
                del self.transcendence[body]
                continue
            if tick <= effect.start:
                continue
            origin = self.fight._eye(owner)
            for target in targets.values():
                if target.entity == body or not self._allied(owner, target):
                    continue
                offset = self._offset(origin, target)
                distance = math.hypot(*offset)
                if distance > TRANSCENDENCE_RADIUS or not self._visible(origin, target):
                    continue
                if distance > 0 and sigma_hyperspheres.barrier_contact(
                    self.fight, owner, origin, tuple(axis / distance for axis in offset), distance,
                ) is not None:
                    continue
                self.fight._heal(owner, target, TRANSCENDENCE_RATE * TICK)

    def damage_scale(self, target):
        for (body, graph), link in self.links.items():
            if (graph == DISCORD and link.target == target.entity and link.owner.body == body
                    and not self._allied(link.owner, target) and not self.fight.is_dead(link.owner)
                    and self.fight.tick - link.last_visible < link.grace):
                return DISCORD_SCALE
        return 1.0

    def next_skipped_tick(self, targets, tick, stop):
        if self.transcendence:
            return tick + 1
        # With held poses, only Ice Wall expiry changes orb visibility in the gap.
        next_tick = min(
            (piece.expires + 1 for piece in self.fight.walls.pieces.values() if piece.expires >= tick),
            default=stop,
        )
        next_tick = min(next_tick, stop)
        visible = []
        for (_, graph), link in self.links.items():
            if link.last_visible == tick:
                visible.append(link)
            else:
                next_tick = min(next_tick, link.last_visible + link.grace)
            if graph == HARMONY and link.rate > 0:
                vitals = self.fight.vitals_of(targets[link.target])
                if any(vitals.now[kind] < most for kind, most in vitals.most.items()):
                    next_tick = min(next_tick, max(tick + 1, vitals.healing_block_until))
        for link in visible:
            link.last_visible = next_tick - 1
        return next_tick

    def update(self, tick, targets=None):
        from ow174.game.combat import TICK

        if not self.links and not self.transcendence:
            return
        if targets is None:
            targets = {target.entity: target for target in self.fight._targets()}
        self._heal_transcendence(targets, tick)
        for key, link in tuple(self.links.items()):
            body, graph = key
            target = targets.get(link.target)
            if (body not in targets or link.owner.body != body or self.fight.is_dead(link.owner)
                    or target is None or self.fight.vitals_of(target).dead
                    or self._allied(link.owner, target) != (graph == HARMONY)):
                self._drop(key)
                continue
            if graph == DISCORD and (
                tracer.recalling(target.owner) or zarya_barrier.active(target.owner) is not None
            ):
                self._drop(key)
                continue
            origin = self.fight._eye(link.owner)
            if math.hypot(*self._offset(origin, target)) <= link.reach and self._visible(origin, target):
                link.last_visible = tick
            if tick - link.last_visible >= link.grace:
                self._drop(key)
            elif graph == HARMONY:
                given = self.fight._heal(link.owner, target, link.rate * TICK)
                ultimate.credit_healing(link.owner, target, given)
