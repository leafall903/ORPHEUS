"""Host the authored Photon Barrier as a static vertical shield plane."""

import math
from dataclasses import dataclass

from ow174.game import bots, pools, sigma, symmetra_beam
from ow174.game.script import expr, graph, runtime

GRAPH, ACTOR_GRAPH = 0x156B, 0x156C
BODY = 0x04000000000026B0


@dataclass
class Wall:
    persists_after_death = True
    persists_after_respawn = True

    entity: int
    owner_body: int
    owner: object
    instance: object
    vitals: object
    centre: tuple
    normal: tuple
    expires: float
    active: bool = True

    def update(self, fight):
        self.active = (self.vitals.total > 0 and fight.now < self.expires
                       and not self.owner.gone and self.owner.hero.guid == symmetra_beam.HERO)

    def break_at(self, now):
        self.active = False

    def retire(self):
        self.active = False
        instance = self.instance
        if instance.stopped:
            return
        pet = instance.states.get(instance.graph.node(17).state)
        if pet is not None and pet.pet_entity == self.entity:
            pet.pet_entity = None
            instance.write(expr.lvalue(pet.node.field("m_B3019338")), expr.Entity(0))
            if pet.active:
                pet.request(runtime.ABORT)
                instance.component.run_now()

    def intersection(self, origin, direction, reach):
        projection = sum(n * d for n, d in zip(self.normal, direction, strict=True))
        if abs(projection) < 1e-8:
            return None
        distance = self._offset(origin) / projection
        return distance if 0 <= distance <= reach else None

    def _offset(self, point):
        return sum((a - b) * n for a, b, n in zip(self.centre, point, self.normal, strict=True))

    def melee_contact(self, centre, radius, visible):
        distance = self._offset(centre)
        return abs(distance) <= radius and visible(
            tuple(c + distance * n for c, n in zip(centre, self.normal, strict=True))
        )


def splash_contacts(fight, shooter, point, source, blast, stopped_by):
    """Sample exposed points on hostile Photon planes before applying damage."""
    from ow174.game.combat import ray_world

    if blast.amount <= 0 or not any(isinstance(barrier, Wall) for barrier in fight.barriers.values()):
        return
    for wall in sigma.hostile_barriers(fight, shooter):
        if not isinstance(wall, Wall) or wall is stopped_by:
            continue
        offset = wall._offset(point)
        amount = blast.amount * blast.splash(abs(offset))
        if amount <= 0:
            continue
        contact = tuple(p + offset * n for p, n in zip(point, wall.normal, strict=True))
        distance = math.dist(source, contact)
        if distance > 1e-6:
            direction = tuple((p - o) / distance for p, o in zip(contact, source, strict=True))
            reach = distance - 1e-6
            impact_blocks = (
                stopped_by is not None and stopped_by.intersection(source, direction, reach) is not None
            )
            if (ray_world(fight._collision(), source, direction, reach) is not None
                    or impact_blocks
                    or sigma.barrier_intersection(fight, shooter, source, direction, reach) is not None):
                continue
        yield wall, amount


def _applies(world, instance):
    return (not instance.component.strict and world.hero == symmetra_beam.HERO and world.match is not None
            and instance.component.entity == world.body and instance.graph.index == GRAPH)


def _owner(world):
    return next((p for p in world.match.players if p.has_body and p.body == world.body), None)


def placement(world, state, *, confirm):
    if (not _applies(world, state.instance) or state.node.pos != 57
            or graph.entity_body(BODY) is None or graph.graph(ACTOR_GRAPH) is None):
        return NotImplemented
    owner = _owner(world)
    fight = world.match.combat
    if owner is None or owner.gone or not owner.spawned or fight.is_dead(owner) or fight.is_stunned(owner):
        return True
    return sigma.place_on_static_ground(world, state, confirm=confirm)


def native(world, cfg, instance):
    if not _applies(world, instance):
        return NotImplemented
    kind = cfg.get("$")
    pet_position = instance.graph.node(17).field("m_position")["m_configVars"]
    if cfg == pet_position[1] and instance.evaluate(cfg.get("m_entity")) == expr.Entity(0):
        return False
    if (kind == "STU_C5148EFE" and cfg == pet_position[0]
            and instance.evaluate(cfg.get("m_entity")) == expr.Entity(0)):
        return instance.evaluate(cfg.get("m_BD631144"))
    if kind not in ("STU_66F3B89E", "STU_021D01CD"):
        return NotImplemented
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    owner = _owner(world)
    if owner is None or owner.gone or not owner.spawned:
        return NotImplemented
    right, _, forward = sigma.view_basis(owner.mover.yaw, 0)
    return expr.Vec3(*(right if kind == "STU_66F3B89E" else forward))


def interrupt(script):
    """Abort 156B's confirmed windup before its finished plug can create the wall."""
    if script.hero != symmetra_beam.HERO or script.component.strict:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            wait = instance.states.get(instance.graph.node(0).state)
            if wait is not None and wait.active:
                wait.request(runtime.ABORT)


def create(world, state):
    instance = state.instance
    if not _applies(world, instance) or state.node.pos != 17:
        return NotImplemented
    definition = graph.entity_body(BODY)
    actor = graph.graph(ACTOR_GRAPH)
    if (definition is None or actor is None
            or state.evaluate(state.node.field("m_entityDefinition")) != expr.Asset(BODY)):
        return NotImplemented
    owner = _owner(world)
    fight = world.match.combat
    if owner is None or owner.gone or fight.match.ended or fight.is_dead(owner) or fight.is_stunned(owner):
        return NotImplemented
    position = state.evaluate(state.node.field("m_position"), watch=False)
    axis = state.evaluate(state.node.field("m_AD8E614B"), watch=False)
    if not isinstance(position, expr.Vec3) or not isinstance(axis, expr.Vec3):
        return NotImplemented
    norm = math.hypot(axis.x, axis.z)
    if not norm or not all(math.isfinite(value) for value in (*position, *axis)):
        return NotImplemented
    row = next(row for row in definition["graphs"] if row["graph"] == pools.BODY_GRAPH)
    health = pools._numbers(row["m_1EB5A024"])[pools.HEALTH_VAR]
    duration = expr.to_float(instance.evaluate(instance.graph.node(18).field("m_EB0FC261")))
    watchdog = expr.to_float(instance.evaluate(actor.node(42).field("m_timeout")))
    if health <= 0 or duration <= 0 or watchdog <= 0:
        return NotImplemented

    from ow174.game.combat import Vitals

    entity = fight.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Photon Barrier entity allocator exhausted")
    for wall in tuple(fight.barriers.values()):
        if isinstance(wall, Wall) and wall.owner is owner:
            sigma.retire_barrier(fight, wall)
    fight.next_bot += 1
    fight.barriers[entity] = Wall(entity, world.body, owner, instance, Vitals(entity, health),
                                  tuple(position), (axis.x / norm, 0, axis.z / norm),
                                  fight.now + min(duration, watchdog))
    return entity
