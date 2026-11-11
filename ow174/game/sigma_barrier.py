"""Sigma's authored 1B55 phases with reconstructed C015 collision and 1A2A shield services."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import bots, props
from ow174.game.collision import RayWorld
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
BARRIER_GRAPH = 0x1B55
BARRIER_DEFINITION = 0x0400000000002D25


def configured_geometry():
    path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
    return barrier_geometry(path) if path else None


@lru_cache(maxsize=4)
def surface(geometry):
    triangles, masks = geometry
    return RayWorld(triangles, masks=masks, name="Sigma C015 dynamic physics mesh")


@lru_cache(maxsize=4)
def barrier_geometry(path: str):
    """Load the verified external C015 physics mesh, including its dynamic holder.

    No asset data is bundled in source and no rectangle fallback is fabricated.
    The sidecar is produced by the manifest-verifying local export step.
    """
    source = Path(path)
    record = json.loads(source.with_name("model-integrity.json").read_text(encoding="utf-8-sig"))
    raw = source.read_bytes()
    if (record.get("guid") != "00000000C015.00C" or record.get("build") != 104319
            or record.get("bytes") != len(raw)
            or record.get("sha256") != hashlib.sha256(raw).hexdigest()):
        raise ValueError("Barrier model does not match its verified 104319 input record")
    shapes = props.model_shapes(raw, 0xFFFF, include_dynamic=True)
    triangles, masks = [], []
    for shape in shapes:
        if shape[0] != "mesh":
            raise ValueError("unsupported Barrier physics shape")
        _kind, _mask, vertices, faces = shape
        for a, b, c, _w0, _w1, _w2, mask, _surface in faces:
            triangles.append((*vertices[a], *vertices[b], *vertices[c]))
            masks.append(mask)
    if not triangles:
        raise ValueError("Barrier model has no physics triangles")
    return tuple(triangles), tuple(masks)


def view_basis(yaw, pitch):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))


def barrier_recharge_rate(instance):
    """Resolve the 1A2A node 50 recharge rate from the owner's ruleset."""
    config = graph.graph(0x1A2A).node(50).field("m_modifyHealth")["m_amount"]
    key = config["m_configVars"][0]["m_value"]["m_expression"]["m_dynamicVars"][0]["m_identifier"]
    override = instance.component.entity_var(key, create=False)
    scale = override.value() if override is not None else None
    if scale is None:
        scale = instance.component.world.ruleset(key)
    base = config["m_expression"]["m_D99EF254"][0]
    rate = expr.to_float(base) * expr.to_float(scale)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError("invalid Barrier recharge rate")
    return rate


@dataclass
class Barrier:
    entity: int
    owner_body: int
    owner: object
    instance: object
    vitals: object
    position: tuple
    basis: tuple
    last: float
    phase: int = 1
    active: bool = False
    child_request_seen: bool = False
    recharge_at: float | None = None

    def update(self, combat):
        component = self.instance.component
        wanted = expr.to_int(component.entity_var(19777).value())
        if wanted in (2, 3) and self.phase not in (2, 3):
            self.basis = view_basis(self.owner.mover.yaw, self.owner.mover.pitch)
            forward = self.basis[2]
            eye = combat._eye(self.owner)
            self.position = tuple(a + b * 0.9 for a, b in zip(eye, forward, strict=True))
            self.last = combat.now
        elapsed = max(0.0, combat.now - self.last)
        if wanted == 1:
            if self.phase != 1:
                wait = graph.graph(0x1A2A).node(45).field("m_timeout")
                delay = expr.to_float(self.instance.evaluate(wait))
                self.recharge_at = combat.now + delay
            if self.recharge_at is not None and self.child_request_seen:
                seconds = max(0.0, combat.now - max(self.last, self.recharge_at))
                if seconds:
                    self.vitals.heal(seconds * barrier_recharge_rate(self.instance))
        else:
            self.recharge_at = None
        self.last = max(self.last, combat.now)
        self.phase = wanted
        self.active = wanted in (2, 3) and self.vitals.total > 0 and self.child_request_seen
        if self.active and wanted == 2 and elapsed:
            # The child graph supplies 16.5 m/s and 0.9 m of wall clearance.
            child = graph.graph(0x1A2A)
            speed = expr.to_float(self.instance.evaluate(child.node(22).field("m_speed")))
            clearance = expr.to_float(self.instance.evaluate(child.node(24).field("m_castLength")))
            step = speed * elapsed
            hit = combat._collision().cast_ray(self.position, self.basis[2], step + clearance)
            if hit is not None:
                step = max(0.0, hit.distance - clearance)
            self.position = tuple(a + b * step for a, b in zip(self.position, self.basis[2], strict=True))

    def damage(self, amount):
        self.vitals.damage(amount)
        if self.vitals.total <= 0:
            self.active = False

    def ray(self, origin, direction, reach):
        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        point = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in self.basis)
        vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in self.basis)
        hit = surface(self.owner.match.combat.sigma_geometry).cast_ray(point, vector, reach)
        return hit.distance if hit is not None else None


def create_barrier(combat, world, state):
    if (state.instance.component.strict or world.hero != HERO
            or state.instance.graph.index != BARRIER_GRAPH or state.node.state != 4):
        return NotImplemented
    definition = state.evaluate(state.node.field("m_entityDefinition"), watch=False)
    if definition != expr.Asset(BARRIER_DEFINITION):
        return NotImplemented
    if combat.sigma_geometry is None or graph.graph(0x1A2A) is None:
        return NotImplemented
    owner = next((p for p in combat.match.players
                  if p.has_body and not p.gone and p.body == world.body), None)
    if owner is None:
        return NotImplemented
    from ow174.game.combat import Vitals

    entity = combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Barrier entity allocator exhausted")
    combat.next_bot += 1
    amount = expr.to_float(state.instance.vars[587].value())
    basis = view_basis(owner.mover.yaw, owner.mover.pitch)
    combat.sigma_barriers[entity] = Barrier(
        entity, world.body, owner, state.instance, Vitals(entity, amount),
        combat._eye(owner), basis, combat.now)
    return entity


def barrier_health_query(world, cfg, instance):
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != BARRIER_GRAPH or cfg.get("$") != "STU_9B7648C3"):
        return NotImplemented
    selector = cfg.get("m_entity")
    if not isinstance(selector, dict) or selector.get("$") != "STUConfigVarDynamic":
        return NotImplemented
    if selector.get("m_identifier") != 512 or selector.get("m_60DB8F99") != graph.ENTITY:
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(selector))
    barrier = world.match.combat.sigma_barriers.get(entity)
    return barrier.vitals.total if barrier is not None else expr.EVALUATION_FAILED


def play_barrier_request(combat, body, instance, node):
    """Bind the authored 1A2A PlayScript request to the reconstructed shield."""
    if instance.component.strict or instance.graph.index != BARRIER_GRAPH or node.pos != 8:
        return False
    resource = instance.evaluate(node.field("m_script"))
    target = expr.to_entity(instance.evaluate(node.field("m_targets")))
    barrier = combat.sigma_barriers.get(target)
    if (resource != expr.Asset(0x0580000000001A2A) or barrier is None
            or barrier.owner_body != body or barrier.instance is not instance):
        return False
    barrier.child_request_seen = True
    return True


def update_barriers(combat):
    for entity, barrier in tuple(combat.sigma_barriers.items()):
        if (barrier.instance.stopped or barrier.instance.component.owner_dead
                or barrier.owner.gone or barrier.owner.body != barrier.owner_body or combat.match.ended):
            remove(combat, entity)
            continue
        barrier.update(combat)


def barrier_owner_query(world, cfg, instance):
    """Resolve 1B55's owner predicate from the player's current possession."""
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != BARRIER_GRAPH):
        return NotImplemented
    cls = cfg.get("$")
    source = cfg.get("m_801B298F") if cls == "STU_F7262B36" else cfg.get("m_entity")
    if cls == "STU_E72F6348":
        if not isinstance(source, dict) or source.get("$") != "STU_F7262B36":
            return NotImplemented
        source = source.get("m_801B298F")
    elif cls != "STU_F7262B36":
        return NotImplemented
    if not isinstance(source, dict) or source.get("$") != "STU_B5A0CAF0":
        return NotImplemented
    if instance.component.entity != world.body:
        return NotImplemented
    owner = next((player for player in world.match.players
                  if player.has_body and not player.gone and player.body == world.body), None)
    if cls == "STU_E72F6348":
        return owner is not None and not world.match.ended
    if owner is None or world.match.ended:
        return expr.EVALUATION_FAILED
    return expr.Entity(owner.entity)


def colliders(combat, body, team):
    return tuple(barrier for barrier in combat.sigma_barriers.values()
                 if barrier.active and barrier.owner_body != body
                 and (combat.match.game_map.free_for_all or barrier.owner.team != team))


def begin(state):
    component = state.instance.component
    world = component.world
    match = getattr(world, "match", None)
    if match is None:
        return
    entity = create_barrier(match.combat, world, state)
    if entity is not NotImplemented:
        state.instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(entity))


def end(state):
    instance = state.instance
    world = instance.component.world
    match = getattr(world, "match", None)
    if match is None or state.index != 4:
        return
    target = expr.lvalue(state.node.field("m_B3019338"))
    if target is None:
        return
    entity = expr.to_entity(instance.find_var(*target).value())
    actor = match.combat.sigma_barriers.get(entity)
    if actor is not None and actor.instance is instance:
        remove(match.combat, entity)


def remove(combat, entity):
    barrier = combat.sigma_barriers.pop(entity)
    instance = barrier.instance
    state = instance.states.get(4)
    if state is not None and not instance.stopped:
        output = expr.lvalue(state.node.field("m_B3019338"))
        if output is not None and expr.to_entity(instance.find_var(*output).value()) == entity:
            instance.write(output, expr.Entity(0))


def retire(combat, body):
    for entity, barrier in tuple(combat.sigma_barriers.items()):
        if barrier.owner_body == body:
            remove(combat, entity)
