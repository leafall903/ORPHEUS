"""Static collision for Winston's authored Barrier Projector deployment."""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.script import expr

HERO = 0x02E0000000000009
GRAPH = 0x00FA
DEFINITION = 0x04000000000001C3
RADIUS = 5.0  # D02.00C MSTU sphere ED61D926, referenced by 01C3.


@dataclass
class Bubble:
    persists_after_death = True

    entity: int
    owner_body: int
    owner: object
    instance: object
    vitals: object
    centre: tuple
    expires: float
    active: bool = True

    def update(self, combat):
        self.active = combat.now < self.expires and self.vitals.total > 0

    def break_at(self, now):
        self.active = False

    def retire(self):
        self.active = False
        if not self.instance.stopped and self.instance.find_var(0, 38).value() == expr.Entity(self.entity):
            self.instance.write((0, 38), expr.Entity(0))

    def intersection(self, origin, direction, reach):
        offset = tuple(a - b for a, b in zip(origin, self.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        c = sum(value * value for value in offset) - RADIUS * RADIUS
        discriminant = projection * projection - c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        if c < -1e-8:
            distance = -projection + root
        else:
            distance = -projection - root
            if distance < -1e-8 or (distance <= 1e-8 and projection >= 0):
                return None
        return max(0.0, distance) if distance <= reach else None

    def melee_contact(self, centre, radius, visible):
        distance = math.dist(centre, self.centre)
        if distance <= 0 or abs(distance - RADIUS) > radius:
            return False
        point = tuple(a + (b - a) * RADIUS / distance for a, b in zip(self.centre, centre, strict=True))
        return visible(point)


def deploy(instance, node):
    """Host 00FA's 01C3 creation request after the graph's own wait."""
    if instance.component.strict or instance.graph.index != GRAPH or node.pos != 26:
        return NotImplemented
    world = instance.component.world
    if getattr(world, "hero", None) != HERO or getattr(world, "match", None) is None:
        return NotImplemented
    if instance.evaluate(node.field("m_entityDefinition")) != expr.Asset(DEFINITION):
        return NotImplemented

    from ow174.game.combat import Vitals

    fight = world.match.combat
    owner = next((p for p in fight.match.players if p.has_body and p.body == world.body), None)
    if fight.match.ended or owner is None or owner.gone or fight.is_dead(owner) or fight.is_stunned(owner):
        return True
    health = expr.to_float(instance.find_var(0, 587).value())
    duration = expr.to_float(instance.component.entity_var(567).value())
    if health <= 0 or duration <= 0:
        return True
    entity = fight.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Barrier Projector entity allocator exhausted")
    fight.next_bot += 1
    x, y, z = owner.mover.position
    centre = (x, y + 0.1, z)
    fight.barriers[entity] = Bubble(
        entity, owner.body, owner, instance, Vitals(entity, health), centre, fight.now + duration,
    )
    instance.write(expr.lvalue(node.field("m_B3019338")), expr.Entity(entity))
    return True
