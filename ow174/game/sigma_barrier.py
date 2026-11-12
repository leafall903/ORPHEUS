"""Scoped Sigma Experimental Barrier host using the verified 104319 C015 mesh."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, contacts, props
from ow174.game.collision import AX, BX, CX, NX, MOVER, World
from ow174.game.script import expr, graph, play, play_queue, runtime

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
    component: runtime.Component | None = None

    @property
    def child(self):
        if self.component is None:
            return None
        return next((instance for instance in self.component.instances.values() if instance.graph.index == 0x1A2A), None)

    def sync_pool(self):
        if self.component is not None:
            self.component.set(253, self.vitals.total)
            self.component.set(478, self.vitals.total <= 0)

    def retire_child(self):
        if self.component is not None:
            for instance in tuple(self.component.instances.values()):
                self.component.destroy(instance)
            self.component = None

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
            offset = 0.9
            if combat._collision().cast_ray(eye, forward, offset) is not None:
                offset = 0.0
            self.position = tuple(a + b * offset for a, b in zip(eye, forward, strict=True))
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
        if self.child is not None:
            if wanted != self.phase:
                self.child.write((expr.INSTANCE, 59), wanted)
            self.sync_pool()
            if combat.tick > self.component.frame:
                self.component.run_frame(combat.tick)
            else:
                self.component.run_now()
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

    def local(self, origin):
        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        return tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in self.basis)

    def intersection(self, origin, direction, reach, radius=0.0):
        point = self.local(origin)
        vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in self.basis)
        if radius <= 0:
            hit = self.geometry.cast_ray(point, vector, reach, MOVER)
            return hit.distance if hit is not None else None
        end = tuple(a + b * reach for a, b in zip(point, vector, strict=True))
        hit = contacts.scene_sweep(self.geometry.engine, point, end, ((0.0, 0.0, 0.0),), radius, MOVER)
        return hit[0] * reach if hit is not None else None

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


class BarrierWorld(runtime.World):
    def __init__(self, mode, barrier):
        super().__init__(HERO, mode)
        self.barrier = barrier

    def position(self, entity):
        if entity == self.barrier.entity:
            return expr.Vec3(*self.barrier.position)
        return super().position(entity)

    def entity_var_slot(self, entity, var, *, create=False):
        source = self.barrier.instance.component
        if entity == source.entity and var in (19777, 2083):
            return source.entity_var(var, create=False)
        return NotImplemented

    def modify_health(self, state, elapsed_ms):
        if (state.instance.graph.index, state.node.pos) == (0x1A2A, 50):
            # The shared actor integrates the authored rate across world clock gaps.
            return True
        return NotImplemented


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
    if barrier.child_request_seen:
        return True
    definition = graph.graph(0x1A2A)
    selected = play.resolve_resource(instance, node)
    if definition is None or selected is None:
        return False
    request = play.resolve_request(instance, node, selected, context_provider=lambda:
        play.InvocationContext(body, body, instance.source_entity()))
    if request is None or request.targets != (barrier.entity,):
        return False
    child = runtime.Component(barrier.entity, instance.component.frame_ms,
                              BarrierWorld(instance.component.world.mode, barrier))
    child.frame, child.now = combat.tick, combat.tick * child.frame_ms
    queue = play_queue.Queue(child, lambda _: play_queue.Load("ready", definition))
    queue.submit(request)
    barrier.component = child
    barrier.child_request_seen = True
    barrier.sync_pool()
    child.run_now()
    if barrier.child is not None:
        barrier.child.write((expr.INSTANCE, 59), barrier.phase)
        child.run_now()
    return True


def update_barriers(combat):
    for entity, barrier in list(combat.sigma_barriers.items()):
        if (barrier.owner.gone or barrier.instance.stopped or barrier.instance.component.owner_dead
                or barrier.owner.body != barrier.owner_body or combat.match.ended):
            del combat.sigma_barriers[entity]
            barrier.retire_child()
            state = barrier.instance.states.get(4)
            if state is not None and state.pet_entity == entity and not barrier.instance.stopped:
                state.pet_entity = None
                barrier.instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(0))
            continue
        barrier.update(combat)


def barrier_intersection(combat, shooter, origin, direction, reach):
    return trace(combat, shooter, origin, direction, reach)


def damage_barrier(combat, barrier, amount):
    dealt = barrier.vitals.damage(amount)
    barrier.sync_pool()
    if barrier.component is not None:
        barrier.component.run_now()
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


def barrier_gap_buttons(script, buttons):
    """Preserve a confirmed continuous deployment across reconstructed command frames."""
    from ow174.game.commands import SECONDARY

    if (script is None or script.hero != HERO or script.component.strict
            or not script.buttons & buttons & SECONDARY):
        return 0
    phase = script.component.entity_var(19777, create=False)
    return SECONDARY if phase is not None and phase.value() == 2 else 0


def enemies(combat, shooter):
    for barrier in combat.sigma_barriers.values():
        barrier.update(combat)
        if (not barrier.active or barrier.owner is shooter
                or (not combat.match.game_map.free_for_all and barrier.owner.team == shooter.team)):
            continue
        yield barrier


def trace(combat, shooter, origin, direction, reach, radius=0.0):
    nearest = None
    for barrier in enemies(combat, shooter):
        distance = barrier.intersection(origin, direction, reach, radius)
        if distance is not None and (nearest is None or distance < nearest[0]):
            nearest = distance, barrier
    return nearest


def melee(fight, shooter, origin, centre, radius, amount, *, contacted=None):
    from ow174.game.combat import capsule_visible

    for barrier in enemies(fight, shooter):
        if contacted is not None and barrier.entity in contacted:
            continue
        local = barrier.local(centre)
        for index, mask in enumerate(barrier.geometry.masks):
            if not mask & MOVER:
                continue
            face = barrier.geometry._triangle(index)
            point = contacts.closest_on_triangle(face[AX:BX], face[BX:CX], face[CX:NX], local)
            if math.dist(point, local) > radius:
                continue
            witness = tuple(value + sum(axis[k] * v for axis, v in zip(barrier.basis, point, strict=True))
                            for k, value in enumerate(barrier.position))
            if capsule_visible(fight._collision(), origin, witness, witness, 0., walls=fight.walls):
                if contacted is not None:
                    contacted.add(barrier.entity)
                damage_barrier(fight, barrier, amount)
                break
