"""Sigma's authored Barrier parent with a reconstructed child actor and C015 mesh."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, props
from ow174.game.collision import World
from ow174.game.contacts import closest_on_triangle
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
BARRIER_GRAPH = 0x1B55
BARRIER_DEFINITION = 0x0400000000002D25


def configured_geometry():
    path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
    return barrier_geometry(path) if path else None


@cache
def mesh(geometry):
    _, triangles, masks = geometry
    return World(triangles, masks=masks, name="Sigma C015 dynamic physics mesh")


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
    return record["sha256"], tuple(triangles), tuple(masks)


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
    geometry: tuple
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
            offset = 0.9
            if combat._collision().cast_ray(eye, forward, offset) is not None:
                # Start on the owner's side so the forward probe sees the wall.
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

    def retire(self):
        self.active = False
        if self.instance.graph.index != BARRIER_GRAPH:
            return
        state = self.instance.states.get(4)
        if state is not None and state.pet_entity == self.entity and not self.instance.stopped:
            state.pet_entity = None
            self.instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(0))

    def melee_contact(self, centre, radius, visible):
        delta = tuple(a - b for a, b in zip(centre, self.position, strict=True))
        local = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in self.basis)
        witness = min((closest_on_triangle(face[:3], face[3:6], face[6:], local)
                       for face in self.geometry[1]), key=lambda point: math.dist(point, local))
        if math.dist(witness, local) > radius:
            return False
        surface = tuple(origin + sum(coordinate * axis[index]
                                     for coordinate, axis in zip(witness, self.basis, strict=True))
                        for index, origin in enumerate(self.position))
        return visible(surface)

    def intersection(self, origin, direction, reach):
        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        point = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in self.basis)
        vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in self.basis)
        hit = mesh(self.geometry).cast_ray(point, vector, reach)
        return hit.distance if hit is not None else None

def barrier_gap_buttons(script):
    """Preserve a confirmed continuous deployment across reconstructed command frames."""
    from ow174.game.commands import SECONDARY

    if (script is None or script.hero != HERO or script.component.strict
            or not script.buttons & SECONDARY):
        return 0
    phase = script.component.entity_var(19777, create=False)
    return SECONDARY if phase is not None and phase.value() == 2 else 0

def create_barrier(combat, world, state):
    if (state.instance.component.strict or world.hero != HERO
            or state.instance.graph.index != BARRIER_GRAPH or state.node.state != 4):
        return NotImplemented
    definition = state.evaluate(state.node.field("m_entityDefinition"), watch=False)
    if definition != expr.Asset(BARRIER_DEFINITION):
        return NotImplemented
    geometry = configured_geometry()
    if geometry is None:
        return NotImplemented  # explicit missing external input, never a placeholder shape
    owner = next((p for p in combat.match.players if p.has_body and p.body == world.body), None)
    if owner is None:
        return NotImplemented
    from ow174.game.combat import Vitals

    entity = combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Barrier entity allocator exhausted")
    combat.next_bot += 1
    amount = expr.to_float(state.instance.vars[587].value())
    basis = view_basis(owner.mover.yaw, owner.mover.pitch)
    combat.sigma_barriers.actors[entity] = Barrier(
        entity, world.body, owner, state.instance, Vitals(entity, amount),
        geometry, combat._eye(owner), basis, combat.now,
    )
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
    barrier = world.match.combat.sigma_barriers.actors.get(entity)
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
    barrier = combat.sigma_barriers.actors.get(target)
    if (resource != expr.Asset(0x0580000000001A2A) or barrier is None
            or barrier.owner_body != body or barrier.instance is not instance):
        return False
    barrier.child_request_seen = True
    return True

def barrier_owner_query(world, cfg, instance):
    """Reconstruct only 1B55's self->component43 owner->existence predicate.

    F7262B36/CDBDE0 calls D15520, which reads component43 and BD42D0. The
    server's current Player.body/entity relation supplies that declared body
    possession. This is not a generic namespace/owner-query engine adapter.
    """
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != 0x1B55):
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
                  if player.has_body and player.body == world.body), None)
    if cls == "STU_E72F6348":
        return owner is not None and not world.match.ended
    if owner is None or world.match.ended:
        return expr.EVALUATION_FAILED  # native object lookup failure, not a fabricated controller
    return expr.Entity(owner.entity)


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.actors = {}

    def retire(self, body):
        for entity, actor in tuple(self.actors.items()):
            if actor.owner_body == body:
                del self.actors[entity]
                actor.retire()

    def update(self):
        for actor in tuple(self.actors.values()):
            if (self.fight.match.ended or actor.owner.body != actor.owner_body
                    or actor.instance.stopped or actor.instance.component.owner_dead):
                self.retire(actor.owner_body)
            else:
                actor.update(self.fight)

    def enemies(self, shooter):
        for actor in self.actors.values():
            if actor.active and actor.owner is not shooter and (
                self.fight.match.game_map.free_for_all or actor.owner.team != shooter.team
            ):
                yield actor

    def trace(self, shooter, origin, direction, reach):
        return min(((distance, actor) for actor in self.enemies(shooter)
                    if (distance := actor.intersection(origin, direction, reach)) is not None),
                   key=lambda hit: hit[0], default=None)

    def melee(self, shooter, centre, radius, amount, visible):
        for actor in self.enemies(shooter):
            if actor.melee_contact(centre, radius, visible):
                self.damage(actor, amount)

    def damage(self, actor, amount):
        actor.vitals.damage(amount)
        if actor.vitals.total <= 0:
            actor.active = False
