"""Sigma's build 104319 Experimental Barrier with hosted child behavior."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import barrier_mesh, bots, props
from ow174.game.collision import ALL_CATEGORIES, MOVER, World
from ow174.game.script import expr, graph


HERO = 0x02E000000000023B
DEPLOY_GRAPH = 0x1B55
CHILD_GRAPH = 0x1A2A
GEOMETRY_SHA256 = "797fab967ccfdfbe4e6c6abb738b4280e064e0d29532bcc47c97823cb71abb87"


@dataclass(frozen=True)
class Geometry:
    triangles: tuple
    masks: tuple
    fingerprint: str


def load_geometry(path: Path) -> Geometry:
    """Load the exact prepared C015 model, including its dynamic physics holder."""
    raw = path.read_bytes()
    record = json.loads(path.with_name("model-integrity.json").read_text(encoding="utf-8-sig"))
    fingerprint = hashlib.sha256(raw).hexdigest()
    if (record.get("guid") != "00000000C015.00C" or record.get("build") != 104319
            or record.get("bytes") != len(raw) or record.get("sha256") != fingerprint
            or fingerprint != GEOMETRY_SHA256):
        raise ValueError("Sigma barrier model does not match its verified 104319 input record")
    shapes = props.model_shapes(raw, ALL_CATEGORIES, include_dynamic=True)
    triangles, masks = [], []
    for shape in shapes:
        if shape[0] != "mesh":
            raise ValueError("unsupported Sigma barrier physics shape")
        _, _, vertices, faces = shape
        for a, b, c, _, _, _, mask, _ in faces:
            triangles.append((*vertices[a], *vertices[b], *vertices[c]))
            masks.append(mask)
    if not triangles:
        raise ValueError("Sigma barrier model has no physics triangles")
    return Geometry(tuple(triangles), tuple(masks), fingerprint)


@lru_cache(maxsize=4)
def mesh(geometry: Geometry) -> World:
    return World(geometry.triangles, masks=geometry.masks, name="Sigma C015 dynamic physics mesh")

def configured_geometry():
    path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
    return load_geometry(Path(path)) if path else None


def view_basis(yaw, pitch):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))


@dataclass
class Barrier:
    entity: int
    owner: object
    instance: object
    vitals: object
    position: tuple
    axes: tuple
    last: float
    phase: int = 1
    child_requested: bool = False
    recharge_at: float | None = None

    def active(self):
        return self.child_requested and self.phase in (2, 3) and self.vitals.total > 0

    def advance(self, fight, sample_phase):
        # On scheduler catch-up, existing motion advances historically. A phase
        # just observed from the player VM takes effect at the current endpoint.
        now = fight.now
        if self.child_requested and self.phase == 1 and self.recharge_at is not None:
            seconds = max(0.0, now - max(self.last, self.recharge_at))
            if seconds:
                config = graph.graph(CHILD_GRAPH).node(50).field("m_modifyHealth")["m_amount"]
                key = config["m_configVars"][0]["m_value"]["m_expression"]["m_dynamicVars"][0]["m_identifier"]
                override = self.instance.component.entity_var(key, create=False)
                scale = override.value() if override is not None else None
                if scale is None:
                    scale = self.instance.component.world.ruleset(key)
                base = config["m_expression"]["m_D99EF254"][0]
                self.vitals.heal(seconds * expr.to_float(base) * expr.to_float(scale))
        elif self.active() and self.phase == 2 and now > self.last:
            child = graph.graph(CHILD_GRAPH)
            speed = expr.to_float(self.instance.evaluate(child.node(22).field("m_speed")))
            clearance = expr.to_float(self.instance.evaluate(child.node(24).field("m_castLength")))
            step = speed * (now - self.last)
            hit = fight._collision().cast_ray(self.position, self.axes[2], step + clearance, MOVER)
            if hit is not None:
                step = min(step, max(0.0, hit.distance - clearance))
            self.position = tuple(a + b * step for a, b in zip(self.position, self.axes[2], strict=True))
        self.last = max(now, self.last)
        if sample_phase:
            phase = expr.to_int(self.instance.component.entity_var(19777).value())
            if phase != self.phase:
                if phase in (2, 3) and self.phase not in (2, 3):
                    self.axes = view_basis(self.owner.mover.yaw, self.owner.mover.pitch)
                    self.position = tuple(a + b * .9 for a, b in zip(
                        fight._eye(self.owner), self.axes[2], strict=True,
                    ))
                self.recharge_at = (now + expr.to_float(self.instance.evaluate(graph.graph(CHILD_GRAPH).node(45).field("m_timeout")))
                                    if phase == 1 else None)
                self.phase = phase


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.geometry = configured_geometry()
        self.actors = {}

    def owner(self, world, instance):
        if (self.geometry is None or world.hero != HERO or instance.component.strict
                or instance.graph.index != DEPLOY_GRAPH or instance.component.entity != world.body
                or self.fight.match.ended):
            return None
        return next((p for p in self.fight.match.players
                     if p.has_body and not p.gone and p.body == world.body), None)

    def native(self, world, cfg, instance):
        cls = cfg.get("$")
        if cls not in ("STU_E72F6348", "STU_9B7648C3"):
            return NotImplemented
        owner = self.owner(world, instance)
        if owner is None:
            return NotImplemented
        selector = cfg.get("m_entity") or {}
        if (cls == "STU_E72F6348" and selector.get("$") == "STU_F7262B36"
                and (selector.get("m_801B298F") or {}).get("$") == "STU_B5A0CAF0"):
            controller = instance.evaluate(selector)
            return controller == expr.Entity(owner.entity)
        if (cls == "STU_9B7648C3" and selector.get("$") == "STUConfigVarDynamic"
                and selector.get("m_identifier") == 512 and selector.get("m_60DB8F99") == expr.ENTITY):
            actor = self.actors.get(expr.to_entity(instance.evaluate(selector)))
            return actor.vitals.total if actor is not None and actor.instance is instance else expr.EVALUATION_FAILED
        return NotImplemented

    def create(self, world, state):
        owner = self.owner(world, state.instance)
        if (owner is None or state.index != 4
                or state.evaluate(state.node.field("m_entityDefinition"), watch=False)
                != expr.Asset(0x0400000000002D25)):
            return NotImplemented
        from ow174.game.combat import Vitals

        entity = self.fight.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("Sigma barrier entity allocator exhausted")
        amount = expr.to_float(state.instance.vars[587].value())
        self.fight.next_bot += 1
        self.actors[entity] = Barrier(entity, owner, state.instance, Vitals(entity, amount),
                                      self.fight._eye(owner), view_basis(owner.mover.yaw, owner.mover.pitch),
                                      self.fight.now)
        return entity

    def play_script(self, body, instance, node):
        if instance.graph.index != DEPLOY_GRAPH or node.pos != 8 or not self.actors:
            return
        resource = instance.evaluate(node.field("m_script"))
        target = expr.to_entity(instance.evaluate(node.field("m_targets")))
        actor = self.actors.get(target)
        if (resource == expr.Asset(0x0580000000001A2A) and actor is not None
                and actor.instance is instance and instance.component.entity == body):
            actor.child_requested = True

    def retire(self, body):
        for entity, actor in tuple(self.actors.items()):
            if actor.instance.component.entity == body:
                del self.actors[entity]

    def update(self, sample_phase):
        for entity, actor in tuple(self.actors.items()):
            component = actor.instance.component
            if (actor.instance.stopped or component.owner_dead or actor.owner.gone
                    or actor.owner.body != component.entity or self.fight.match.ended):
                del self.actors[entity]
            else:
                actor.advance(self.fight, sample_phase)

    def enemies(self, shooter):
        for actor in self.actors.values():
            if actor.active() and actor.owner is not shooter and (
                self.fight.match.game_map.free_for_all or actor.owner.team != shooter.team
            ):
                yield actor

    def intersect(self, actor, origin, direction, reach):
        delta = tuple(a - b for a, b in zip(origin, actor.position, strict=True))
        point = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in actor.axes)
        vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in actor.axes)
        hit = mesh(self.geometry).cast_ray(point, vector, reach, MOVER)
        return hit.distance if hit is not None else None

    def trace(self, shooter, origin, direction, reach):
        return min(((distance, actor) for actor in self.enemies(shooter)
                    if (distance := self.intersect(actor, origin, direction, reach)) is not None),
                   key=lambda contact: contact[0], default=None)

    def damage(self, actor, amount):
        if actor.active() and amount > 0:
            actor.vitals.damage(amount)

    def melee(self, shooter, centre, radius, amount, *, origin=None):
        from ow174.game.combat import capsule_visible

        if amount <= 0 or not self.actors:
            return
        if origin is None:
            origin = self.fight._eye(shooter)

        def visible(point):
            return capsule_visible(self.fight._collision(), origin, point, point, 0)

        for actor in self.enemies(shooter):
            if barrier_mesh.sphere_contact_at(
                mover_triangles(self.geometry), actor.position, actor.axes, centre, radius, visible,
            ):
                self.damage(actor, amount)

    def splash(self, shooter, point, origin, blast, impact, direct_amount):
        from ow174.game.combat import capsule_visible

        cover = tuple(self.enemies(shooter))
        for actor in cover:
            if actor is impact:
                self.damage(actor, direct_amount + blast.amount * blast.splash(0))
                continue
            distance, surface = barrier_mesh.nearest_surface(
                mover_triangles(self.geometry), actor.position, actor.axes, point,
            )
            scalar = blast.splash(distance)
            if scalar > 0 and capsule_visible(self.fight._collision(), origin, surface, surface, 0):
                self.damage(actor, blast.amount * scalar)
        return cover

    def covers(self, cover, origin, a, b):
        return barrier_mesh.covers(self.intersect, cover, origin, a, b)


@lru_cache(maxsize=4)
def mover_triangles(geometry):
    return tuple(face for face, mask in zip(geometry.triangles, geometry.masks, strict=True) if mask & MOVER)
