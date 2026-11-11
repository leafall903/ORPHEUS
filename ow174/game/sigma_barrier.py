"""Selected Sigma 1B55 local barrier actor with authored C015 physics.

The local handle links the owner graph to a host combat surface; it is not
a native network Pet. Authored phase, speed, clearance and recharge feed the
declared placement, collision and world-clock policy.
"""

import hashlib
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, props
from ow174.game.collision import World
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
BARRIER_GRAPH = 0x1B55
BARRIER_DEFINITION = 0x0400000000002D25
GEOMETRY_SHA256 = "797fab967ccfdfbe4e6c6abb738b4280e064e0d29532bcc47c97823cb71abb87"
NOT_HANDLED = NotImplemented


def geometry_bytes():
    path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
    if not path:
        return None
    raw = Path(path).read_bytes()
    geometry(raw)
    return raw


@cache
def geometry(raw):
    if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
        raise ValueError("Sigma barrier model checksum mismatch")
    triangles, masks = [], []
    for shape in props.model_shapes(raw, 0xFFFF, include_dynamic=True):
        if shape[0] != "mesh":
            raise ValueError("unsupported Sigma barrier physics shape")
        _, _, vertices, faces = shape
        for a, b, c, _w0, _w1, _w2, mask, _surface in faces:
            triangles.append((*vertices[a], *vertices[b], *vertices[c]))
            masks.append(mask)
    if not triangles:
        raise ValueError("Sigma barrier model has no physics triangles")
    return World(triangles, masks=masks, name="Sigma C015 dynamic physics mesh")


def view_basis(yaw, pitch):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))


def barrier_recharge_rate(instance):
    """Reconstruct1A2A/node50's owner-body ruleset read, not its native query chain.

    The graph supplies both80HP/s and the591A ruleset identifier. Resolve the
    owning body's declared override/default, as the permissive host does for
    other ruleset inputs. Native3832D36C/CCFAC0 has its own context/fallback path.
    """
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
    health: float
    maximum: float
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
        if component.owner_dead or self.owner.body != self.owner_body or combat.match.ended:
            self.active = False
            return
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
                    recovered = seconds * barrier_recharge_rate(self.instance)
                    self.health = min(self.maximum, self.health + recovered)
        else:
            self.recharge_at = None
        self.last = max(self.last, combat.now)
        self.phase = wanted
        self.active = wanted in (2, 3) and self.health > 0 and self.child_request_seen
        if self.active and wanted == 2 and elapsed:
            # The actual child movement node supplies16.5m/s and its forward
            # wall probe supplies0.9m. Ordering/placement are reconstructed.
            child = graph.graph(0x1A2A)
            speed = expr.to_float(self.instance.evaluate(child.node(22).field("m_speed")))
            clearance = expr.to_float(self.instance.evaluate(child.node(24).field("m_castLength")))
            step = speed * elapsed
            hit = combat._collision().cast_ray(self.position, self.basis[2], step + clearance)
            if hit is not None:
                step = max(0.0, hit.distance - clearance)
            self.position = tuple(a + b * step for a, b in zip(self.position, self.basis[2], strict=True))

    def intersection(self, world, origin, direction, reach):
        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        point = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in self.basis)
        vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in self.basis)
        hit = world.cast_ray(point, vector, reach)
        return hit.distance if hit is not None else None

    def damage(self, amount):
        self.health = max(0.0, self.health - max(0.0, amount))
        if self.health <= 0:
            self.active = False


def barrier_owner_query(world, cfg, instance):
    """Reconstruct only 1B55's self->component43 owner->existence predicate.

    F7262B36/CDBDE0 calls D15520, which reads component43 and BD42D0. The
    server's current Player.body/entity relation supplies that declared body
    possession. This is not a generic namespace/owner-query engine adapter.
    """
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != 0x1B55):
        return NOT_HANDLED
    cls = cfg.get("$")
    source = cfg.get("m_801B298F") if cls == "STU_F7262B36" else cfg.get("m_entity")
    if cls == "STU_E72F6348":
        if not isinstance(source, dict) or source.get("$") != "STU_F7262B36":
            return NOT_HANDLED
        source = source.get("m_801B298F")
    elif cls != "STU_F7262B36":
        return NOT_HANDLED
    if not isinstance(source, dict) or source.get("$") != "STU_B5A0CAF0":
        return NOT_HANDLED
    if instance.component.entity != world.body:
        return NOT_HANDLED
    owner = next((player for player in world.match.players
                  if player.has_body and player.body == world.body), None)
    if cls == "STU_E72F6348":
        return owner is not None and not world.match.ended
    if owner is None or world.match.ended:
        return expr.EVALUATION_FAILED  # native object lookup failure, not a fabricated controller
    return expr.Entity(owner.entity)


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.geometry = geometry_bytes()
        self.actors = {}

    def create(self, world, state):
        instance = state.instance
        if (world.hero != HERO or instance.graph.index != BARRIER_GRAPH or state.index != 4
                or instance.component.entity != world.body):
            return NotImplemented
        definition = state.evaluate(state.node.field("m_entityDefinition"), watch=False)
        if definition != expr.Asset(BARRIER_DEFINITION) or self.geometry is None:
            return NotImplemented
        fight = self.combat
        owner = next((p for p in fight.match.players if p.has_body and p.body == world.body), None)
        if owner is None:
            return NotImplemented
        entity = fight.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("barrier handle allocator exhausted")
        capacity = expr.to_float(instance.vars[587].value())
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError("invalid Sigma barrier capacity")
        fight.next_bot += 1
        basis = view_basis(owner.mover.yaw, owner.mover.pitch)
        self.actors[entity] = Barrier(
            entity, world.body, owner, instance, capacity, capacity, fight._eye(owner), basis, fight.now,
        )
        return entity

    def native(self, world, cfg, instance):
        if instance is None:
            return NotImplemented
        result = barrier_owner_query(world, cfg, instance)
        if result is not NotImplemented:
            return result
        if (world.hero != HERO or instance.graph.index != BARRIER_GRAPH
                or instance.component.entity != world.body or cfg.get("$") != "STU_9B7648C3"):
            return NotImplemented
        selector = cfg.get("m_entity")
        if (not isinstance(selector, dict) or selector.get("$") != "STUConfigVarDynamic"
                or selector.get("m_identifier") != 512 or selector.get("m_60DB8F99") != expr.ENTITY):
            return NotImplemented
        barrier = self.actors.get(expr.to_entity(instance.evaluate(selector)))
        return barrier.health if barrier is not None else expr.EVALUATION_FAILED

    def play(self, body, instance, node):
        if instance.graph.index != BARRIER_GRAPH or node.pos != 8:
            return False
        resource = instance.evaluate(node.field("m_script"))
        target = expr.to_entity(instance.evaluate(node.field("m_targets")))
        barrier = self.actors.get(target)
        if (resource != expr.Asset(0x0580000000001A2A) or barrier is None
                or barrier.owner_body != body or barrier.instance is not instance):
            return False
        barrier.child_request_seen = True
        return True

    def retire_body(self, body):
        for entity, barrier in tuple(self.actors.items()):
            if barrier.owner_body == body:
                del self.actors[entity]

    def update(self):
        for entity, barrier in tuple(self.actors.items()):
            if (barrier.instance.stopped or barrier.instance.component.owner_dead
                    or barrier.owner.body != barrier.owner_body or self.combat.match.ended):
                del self.actors[entity]
                continue
            barrier.update(self.combat)

    def first(self, shooter, origin, direction, reach, *, team=None):
        if not self.actors:
            return None
        side = shooter.team if team is None else team
        best = None
        for barrier in tuple(self.actors.values()):
            # Same-tick owner input can change phase; refresh it without advancing the VM.
            barrier.update(self.combat)
            if (not barrier.active or barrier.owner is shooter
                    or (not self.combat.match.game_map.free_for_all and barrier.owner.team == side)):
                continue
            distance = barrier.intersection(geometry(self.geometry), origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, barrier
        return best
