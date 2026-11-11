"""Scoped Sigma Experimental Barrier host using the verified 104319 C015 mesh."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, contacts, props
from ow174.game.collision import MOVER, World
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
BARRIER_GRAPH = 0x1B55
BARRIER_DEFINITION = 0x0400000000002D25
NOT_HANDLED = NotImplemented
MODEL_PATH = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
MODEL_SHA256 = hashlib.sha256(Path(MODEL_PATH).read_bytes()).hexdigest() if MODEL_PATH else None


@cache
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
    vitals: object
    geometry: World
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
                    self.vitals.heal(seconds * barrier_recharge_rate(self.instance))
        else:
            self.recharge_at = None
        self.last = max(self.last, combat.now)
        self.phase = wanted
        self.active = wanted in (2, 3) and self.vitals.total > 0 and self.child_request_seen
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

    def intersection(self, origin, direction, reach):
        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        point = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in self.basis)
        vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in self.basis)
        hit = self.geometry.cast_ray(point, vector, reach)
        return hit.distance if hit is not None else None

    def closest_visible_distance(self, combat, shooter, origin, reach):
        from ow174.game.combat import ray_world

        delta = contacts.sub(origin, self.position)
        local = tuple(contacts.dot(delta, axis) for axis in self.basis)
        nearest = None
        limit_sq = reach * reach
        for index, mask in enumerate(self.geometry.masks):
            if not mask & MOVER:
                continue
            base = index * 13
            a, b, c = (tuple(self.geometry.data[base + k:base + k + 3]) for k in (0, 3, 6))
            offset = contacts.sub(contacts.closest_on_triangle(a, b, c, local), local)
            squared = contacts.dot(offset, offset)
            if squared > limit_sq:
                continue
            distance = math.sqrt(squared)
            if distance > 1e-9:
                direction = tuple(sum(axis[i] * value for axis, value in zip(self.basis, offset, strict=True))
                                  / distance for i in range(3))
                wall = ray_world(combat._collision(), origin, direction, distance)
                if wall is not None and wall < distance - 1e-6:
                    continue
                if combat.brigitte.barrier_ray(shooter, origin, direction, max(0.0, distance - 1e-6)):
                    continue
                nearer = barrier_intersection(combat, shooter, origin, direction, max(0.0, distance - 1e-6))
                if nearer is not None and nearer[1] is not self:
                    continue
            nearest, limit_sq = distance, squared
        return nearest


def create_barrier(combat, world, state):
    if (state.instance.component.strict or world.hero != HERO
            or state.instance.graph.index != BARRIER_GRAPH or state.node.state != 4):
        return NotImplemented
    definition = state.evaluate(state.node.field("m_entityDefinition"), watch=False)
    if definition != expr.Asset(BARRIER_DEFINITION):
        return NotImplemented
    path = MODEL_PATH
    if not path:
        return NotImplemented  # explicit missing external input, never a placeholder shape
    owner = next((p for p in combat.match.players if p.has_body and p.body == world.body), None)
    if owner is None:
        return NotImplemented
    from ow174.game.combat import Vitals

    geometry = barrier_geometry(path)
    entity = combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Barrier entity allocator exhausted")
    combat.next_bot += 1
    amount = expr.to_float(state.instance.vars[587].value())
    basis = view_basis(owner.mover.yaw, owner.mover.pitch)
    combat.sigma_barriers[entity] = Barrier(entity, world.body, owner, state.instance, Vitals(entity, amount),
                                     geometry, combat._eye(owner), basis, combat.now)
    return entity


def barrier_health_query(world, cfg, instance):
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != BARRIER_GRAPH or cfg.get("$") != "STU_9B7648C3"):
        return NOT_HANDLED
    selector = cfg.get("m_entity")
    if not isinstance(selector, dict) or selector.get("$") != "STUConfigVarDynamic":
        return NOT_HANDLED
    if selector.get("m_identifier") != 512 or selector.get("m_60DB8F99") != graph.ENTITY:
        return NOT_HANDLED
    entity = expr.to_entity(instance.evaluate(selector))
    barrier = world.match.combat.sigma_barriers.get(entity)
    return barrier.vitals.total if barrier is not None else expr.EVALUATION_FAILED


def play_barrier_request(combat, body, instance, node):
    """Bind the actual self-owned Pet request to its reconstructed actor service.

    The1A2A graph's native engine execution is not claimed; its health, motion
    constants and owner phase inputs drive this explicit service.
    """
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
    for entity, barrier in list(combat.sigma_barriers.items()):
        if (barrier.instance.stopped or barrier.instance.component.owner_dead
                or barrier.owner.body != barrier.owner_body or combat.match.ended):
            del combat.sigma_barriers[entity]
            state = barrier.instance.states.get(4)
            if state is not None and state.pet_entity == entity and not barrier.instance.stopped:
                state.pet_entity = None
                barrier.instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(0))
            continue
        barrier.update(combat)


def barrier_intersection(combat, shooter, origin, direction, reach):
    best = None
    for barrier in combat.sigma_barriers.values():
        barrier.update(combat)
        if (not barrier.active or barrier.owner is shooter
                or (not combat.match.game_map.free_for_all and barrier.owner.team == shooter.team)):
            continue
        distance = barrier.intersection(origin, direction, reach)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, barrier
    return best


def damage_barrier(combat, barrier, amount):
    dealt = barrier.vitals.damage(amount)
    if barrier.vitals.total <= 0:
        barrier.active = False
    return dealt


def blast_contacts(combat, shooter, origin, blast, *, struck=None):
    """Snapshot splash on visible mesh surfaces before any impact damage is applied."""
    if not blast.rings:
        return []
    found = []
    for barrier in combat.sigma_barriers.values():
        if (barrier is struck or not barrier.active or barrier.owner is shooter
                or (not combat.match.game_map.free_for_all and barrier.owner.team == shooter.team)):
            continue
        distance = barrier.closest_visible_distance(combat, shooter, origin, blast.rings[-1][0])
        if distance is not None:
            amount = blast.amount * blast.splash(distance)
            if amount > 0:
                found.append((barrier, amount))
    return found
