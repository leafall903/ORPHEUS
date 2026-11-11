"""Selected 0176 Discord target, cooldown and retained-orb combat host."""

import math
from dataclasses import dataclass

from ow174.game import barriers, defenses
from ow174.game.script import expr

HERO = 0x02E0000000000020
GRAPH = 0x0176
SCALE = 1.25  # selected reconstruction; the native modifier path is unresolved
CONE = 10


def native(fight, world, instance, cfg):
    if world.hero != HERO or instance.graph.index != GRAPH:
        return NotImplemented
    if cfg is instance.graph.node(23).field("m_logicalButton")["m_configVars"][0]:
        return True  # ordinary binding chooses the authored logical35 branch
    if cfg is instance.graph.node(29).field("m_condition")["m_configVars"][0]:
        # The native root predicate is unavailable. Scope the ordinary live
        # caster gate to this exact owner query.
        caster = next((p for p in fight.match.players if p.body == world.body and p.has_body), None)
        return 1 if caster is not None and not fight.is_dead(caster) else 0
    return NotImplemented


def visible(fight, caster, target, *, acquisition=False):
    from ow174.game.combat import ray_world

    origin = fight._eye(caster)
    x, y, z = target.feet
    delta = (x - origin[0], y + target.eye * 0.5 - origin[1], z - origin[2])
    length = math.hypot(*delta)
    if not length:
        return True
    direction = tuple(d / length for d in delta)
    wall = ray_world(fight._collision(), origin, direction, length)
    return ((wall is None or wall >= length)
            and (not acquisition or barriers.intercept(fight, caster, origin, direction, length) is None))


def query(fight, state):
    if state.instance.graph.index != GRAPH or state.index not in (0, 16):
        return NotImplemented
    body = state.instance.component.entity
    caster = next((p for p in fight.match.players if p.has_body and p.body == body), None)
    if caster is None or caster.hero.guid != HERO or fight.is_dead(caster) or fight.is_stunned(caster):
        return ()
    from ow174.game.combat import aim

    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin = fight._eye(caster)
    forward = aim(caster.mover.yaw, caster.mover.pitch)
    threshold = math.cos(math.radians(CONE))
    found = []
    for target in fight._enemies(caster):
        if defenses.invulnerable(target.owner) or fight.zarya_bubbles.for_target(target.owner) is not None:
            continue
        x, y, z = target.feet
        delta = (x - origin[0], y + target.eye - origin[1], z - origin[2])
        length = math.hypot(*delta)
        if not 0 < length <= radius:
            continue
        alignment = sum(d * f for d, f in zip(delta, forward, strict=True)) / length
        if alignment >= threshold and visible(fight, caster, target, acquisition=True):
            found.append((-alignment, length, target.entity))
    return (expr.Entity(min(found)[2]),) if found else ()


@dataclass
class Orb:
    caster: object
    instance: object
    target: int
    last_visible: float
    grace: float


class Orbs:
    def __init__(self, fight):
        self.fight = fight
        self.active = {}
        self.seen = {}
        self.ready = {}

    def remove(self, source):
        orb = self.active.pop(source, None)
        script = orb.caster.body_script if orb is not None else None
        if script is not None and not script.retired and orb.caster.body == source:
            orb.instance.write((expr.INSTANCE, 1606), 0)

    def retire(self, body):
        self.remove(body)
        self.seen.pop(body, None)
        self.ready.pop(body, None)
        self.cleanse(body)

    def cleanse(self, body):
        for source, orb in tuple(self.active.items()):
            if orb.target == body:
                self.remove(source)

    def command(self, caster, command):
        if command is None or caster.hero.guid != HERO or defenses.invulnerable(caster):
            return
        instances = caster.body_script.component.instances.values()
        instance = next((i for i in instances if i.graph.index == GRAPH), None)
        state = instance.states.get(16) if instance is not None else None
        if state is None or not state.counter or self.seen.get(caster.body) == state.counter:
            return
        self.seen[caster.body] = state.counter
        if self.fight.now < self.ready.get(caster.body, 0):
            prior = self.active.get(caster.body)
            instance.write((expr.INSTANCE, 1606), expr.Entity(prior.target) if prior is not None else 0)
            return
        selected = instance.find_var(expr.INSTANCE, 1606).value()
        if selected not in query(self.fight, state):
            return
        target = selected.id
        grace = expr.to_float(state.evaluate(instance.graph.node(71).field("m_timeout"), watch=False))
        recovery = expr.to_float(state.evaluate(instance.graph.node(43).field("m_timeout"), watch=False))
        self.active[caster.body] = Orb(caster, instance, target, self.fight.now, grace)
        self.ready[caster.body] = self.fight.now + recovery

    def scale(self, entity):
        return SCALE if any(orb.target == entity for orb in self.active.values()) else 1.0

    def update(self):
        if not self.active:
            return
        fight = self.fight
        targets = {t.entity: t for t in fight._targets()}
        for source, orb in tuple(self.active.items()):
            target = targets.get(orb.target)
            if (target is None or fight.vitals_of(target).dead or fight.is_dead(orb.caster)
                    or (not fight.match.game_map.free_for_all and target.team == orb.caster.team)
                    or fight.zarya_bubbles.for_target(target.owner) is not None):
                self.remove(source)
            elif visible(fight, orb.caster, target):
                orb.last_visible = fight.now
            elif fight.now - orb.last_visible >= orb.grace:
                self.remove(source)
