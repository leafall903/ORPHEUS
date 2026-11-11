"""Attached Zarya shield pools gated by the original personal/projected Pet scopes."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000068
PERSONAL, PROJECTED = 0x244, 0x25C
RADIUS = 1.5  # 10D7.00C sphere.
PHASES = ((PERSONAL, 2), (PROJECTED, 10))


@dataclass
class Bubble:
    entity: int
    caster: object
    target: object
    body: int
    caster_body: int
    graph_index: int
    stamp: tuple
    health: float

    def phase(self):
        script = self.caster.body_script
        if script is None or script.retired or script.component.entity != self.caster_body:
            return None
        instance = script.component.instances.get(self.stamp[0])
        if instance is None:
            return None
        phase = instance.states.get(2 if self.graph_index == PERSONAL else 10)
        if phase is not None and phase.active and (phase.counter, phase.start) == self.stamp[1:]:
            return phase
        return None

    def centre(self):
        from ow174.game.mover import mover_data

        data = mover_data(self.target.hero.body)
        mover = self.target.mover
        height = data.crouch_height if mover.crouched else data.stand_height
        x, y, z = mover.position
        return x, y + height / 2, z

    def ray(self, origin, direction, reach):
        offset = tuple(a - b for a, b in zip(origin, self.centre(), strict=True))
        radial = sum(axis * axis for axis in offset) - RADIUS * RADIUS
        if radial <= 0:
            return 0.0
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        discriminant = projection * projection - radial
        if discriminant < 0:
            return None
        distance = -projection - math.sqrt(discriminant)
        return distance if 0 <= distance <= reach else None

    def damage(self, amount, source):
        lost = min(self.health, max(0.0, amount))
        if not lost:
            return
        self.health = expr.f32(self.health - lost)
        phase = self.phase()
        if phase is None:
            return
        component = phase.instance.component
        runtime.send_message(component, 0x0240000000000146,
                             {0x2A: expr.f32(lost), 0x12: expr.Entity(source)}, self.entity)
        if self.health <= 0:
            phase.end(True)
        component.run_now()


def query(state):
    """Local target service for the two actual projected-barrier searches."""
    from ow174.game.combat import aim, capsule_visible

    instance = state.instance
    component = instance.component
    match = getattr(component.world, "match", None)
    if match is None or match.game_map.free_for_all or component.owner_dead:
        return ()
    caster = next((p for p in match.players if p.body == component.entity and not p.gone), None)
    if caster is None:
        return ()
    fight = match.combat
    origin = fight._eye(caster)
    forward = aim(caster.mover.yaw, caster.mover.pitch)
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    factor = component.entity_var(9595, create=False)
    factor = factor.value() if factor is not None else None
    angle = 10.0 if factor is None else expr.to_float(
        state.evaluate(state.node.field("m_A30B383E"), watch=False))
    protected = {bubble.body for bubble in active(fight)}
    candidates = []
    for target in fight._targets():
        if (target.owner is caster or target.team != caster.team or target.entity in protected
                or not hasattr(target.owner, "mover") or fight.vitals_of(target).dead):
            continue
        a, b = target.segment()
        centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
        distance = math.dist(origin, centre)
        if not 0 < distance <= reach:
            continue
        alignment = sum((x - y) * axis for x, y, axis in zip(centre, origin, forward, strict=True)) / distance
        if alignment >= math.cos(math.radians(angle)) and capsule_visible(
                fight._collision(), origin, a, b, target.radius):
            candidates.append((-alignment, distance, target.entity))
    return (expr.Entity(min(candidates)[2]),) if candidates else ()


def sync(combat, caster):
    script = caster.body_script
    if caster.hero.guid != HERO or script is None or script.retired:
        return
    targets = {target.entity: target for target in combat._targets()}
    for index, state_id in PHASES:
        instance = next((i for i in script.component.instances.values() if i.graph.index == index), None)
        phase = instance.states.get(state_id) if instance is not None else None
        if phase is None or not phase.active:
            continue
        key = caster.body, index
        stamp = instance.id, phase.counter, phase.start
        old = combat.zarya_bubbles.get(key)
        if old is not None and old.stamp == stamp:
            continue
        body = caster.body if index == PERSONAL else expr.to_entity(instance.find_var(0, 536).value())
        target = targets.get(body)
        if target is None or combat.vitals_of(target).dead or not hasattr(target.owner, "mover"):
            continue
        shield_graph = graph.graph(0x256)
        if shield_graph is None:
            continue
        pool = shield_graph.states[0].field("m_amount")
        default = pool["m_configVars"][1]["m_A53100D5"]["m_value"]
        configured = script.world.ruleset(0x1D43)
        health = expr.f32(default if configured is None else configured)
        if not math.isfinite(health) or health <= 0:
            continue
        entity = combat.next_bot
        if entity >= 0xD0000000:
            raise ValueError("Zarya bubble entity allocator exhausted")
        combat.next_bot += 1
        combat.zarya_bubbles[key] = Bubble(
            entity, caster, target.owner, body, caster.body, index, stamp, health)


def active(combat):
    return tuple(b for b in combat.zarya_bubbles.values()
                 if b.health > 0 and b.target.has_body and b.target.body == b.body
                 and not b.target.gone and not combat.is_dead(b.target) and b.phase() is not None)


def attached_to(combat, body):
    return next((bubble for bubble in active(combat) if bubble.body == body), None)


def colliders(combat, body, team):
    free_for_all = combat.match.game_map.free_for_all
    return tuple(bubble for bubble in active(combat)
                 if bubble.body != body and (free_for_all or bubble.target.team != team))


def retire(combat, body):
    for key, bubble in tuple(combat.zarya_bubbles.items()):
        if body in (bubble.body, bubble.caster_body):
            del combat.zarya_bubbles[key]
