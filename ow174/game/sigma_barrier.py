"""Host Sigma's 1B55 Experimental Barrier and its verified C015 mesh."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import props
from ow174.game.commands import SECONDARY
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.sigma_combat import HERO

BARRIER_GRAPH = 0x1B55
CHILD = 0x1A2A
DEFINITION = 0x0400000000002D25
PHASE = 19777
STORED, TRAVELLING, PARKED = 1, 2, 3


@lru_cache(maxsize=2)
def geometry(path):
    record = json.loads(path.with_name("model-integrity.json").read_text(encoding="utf-8-sig"))
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if (record.get("guid"), record.get("build"), record.get("bytes"), record.get("sha256")) != (
        "00000000C015.00C", 104319, len(raw), digest,
    ):
        raise ValueError("Sigma Barrier model does not match its 104319 integrity record")
    shapes = props.model_shapes(raw, 0xFFFF, include_dynamic=True)
    if not shapes or any(shape[0] != "mesh" for shape in shapes):
        raise ValueError("Sigma Barrier requires the original dynamic collision mesh")
    mesh = tuple(tuple(vertices[index] for index in (a, b, c))
                 for _, _, vertices, faces in shapes for a, b, c, *_ in faces)
    return mesh, digest


def view_basis(yaw, pitch):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return (cy, 0., -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp)


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

@dataclass
class Barrier:
    owner: object
    instance: runtime.Instance
    component: runtime.Component
    vitals: object
    mesh: tuple
    geometry_digest: str
    position: tuple
    basis: tuple
    last: float
    phase: int = STORED
    child_bound: bool = False
    recharge_at: float | None = None

    @property
    def entity(self):
        return self.component.entity

    @property
    def team(self):
        return self.owner.team

    def pose(self):
        return self.position, self.basis

    def current(self, fight):
        source = self.instance.component
        return (not self.owner.gone and self.owner.body == source.entity
                and not self.instance.stopped and not source.owner_dead
                and not fight.match.ended and not fight.is_dead(self.owner))

    def active(self):
        return self.child_bound and self.phase in (TRAVELLING, PARKED) and self.vitals.total > 0

    def damage(self, amount):
        self.vitals.damage(amount)
        self.component.set(253, self.vitals.total)
        if self.vitals.total <= 0:
            self.component.set(478, True)

    def update(self, fight):
        wanted = expr.to_int(self.instance.component.entity_var(PHASE).value())
        elapsed = max(0., fight.now - self.last)
        collision = fight._line_collision()
        if wanted in (TRAVELLING, PARKED) and self.phase == STORED:
            self.basis = view_basis(self.owner.mover.yaw, self.owner.mover.pitch)
            forward = self.basis[2]
            eye = fight._eye(self.owner)
            offset = 0. if collision.cast_ray(eye, forward, .9) is not None else .9
            self.position = tuple(a + b * offset for a, b in zip(eye, forward, strict=True))
            elapsed = 0.
        child = graph.graph(CHILD)
        if wanted == STORED:
            if self.phase != STORED:
                delay = expr.to_float(self.instance.evaluate(child.node(45).field("m_timeout")))
                self.recharge_at = fight.now + delay
            if self.recharge_at is not None and self.child_bound:
                seconds = max(0., fight.now - max(self.last, self.recharge_at))
                if seconds:
                    self.vitals.heal(seconds * barrier_recharge_rate(self.instance))
                    self.component.set(253, self.vitals.total)
                    if self.vitals.total > 0:
                        self.component.set(478, False)
        else:
            self.recharge_at = None
        self.last = max(self.last, fight.now)
        self.phase = wanted
        if self.active() and wanted == TRAVELLING and elapsed:
            speed = expr.to_float(self.instance.evaluate(child.node(22).field("m_speed")))
            clearance = expr.to_float(self.instance.evaluate(child.node(24).field("m_castLength")))
            step = speed * elapsed
            forward = self.basis[2]
            wall = collision.cast_ray(self.position, forward, step + clearance)
            if wall is not None:
                step = max(0., wall.distance - clearance)
            self.position = tuple(a + b * step for a, b in zip(self.position, forward, strict=True))


def create(world, state):
    if (world.match is None or state.instance.component.strict or world.hero != HERO
            or state.instance.graph.index != BARRIER_GRAPH
            or state.index != 4
            or state.evaluate(state.node.field("m_entityDefinition"), watch=False) != expr.Asset(DEFINITION)):
        return NotImplemented
    path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
    if not path or graph.graph(CHILD) is None:
        return NotImplemented
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if owner is None:
        return NotImplemented
    from ow174.game.combat import Vitals

    mesh, digest = geometry(Path(path))
    entity = fight.projectiles.allocate_seen_entity()
    pet_world = type(world)(HERO, world.mode, entity)
    pet_world.match = world.match
    component = runtime.Component(entity, state.instance.component.frame_ms, pet_world)
    component.frame, component.now = state.instance.component.frame, state.instance.component.now
    amount = expr.to_float(state.instance.vars[587].value())
    barrier = Barrier(owner, state.instance, component, Vitals(entity, amount), mesh, digest,
                      fight._eye(owner), view_basis(owner.mover.yaw, owner.mover.pitch), fight.now)
    fight.barriers[entity] = barrier
    component.set(253, amount)
    return entity


def play(world, instance, node):
    if world.match is None or world.hero != HERO or (instance.graph.index, node.pos) != (BARRIER_GRAPH, 8):
        return False
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    barrier = world.match.combat.barriers.get(entity)
    if (isinstance(barrier, Barrier) and barrier.instance is instance
            and instance.evaluate(node.field("m_script")) == expr.Asset(0x0580000000001A2A)):
        barrier.child_bound = True
        barrier.update(world.match.combat)
        return True
    return False


def native(world, cfg, instance):
    if instance is None:
        return NotImplemented
    owner = barrier_owner_query(world, cfg, instance)
    if owner is not NotImplemented:
        return owner
    if (world.match is None or world.hero != HERO or instance.graph.index != BARRIER_GRAPH
            or cfg.get("$") != "STU_9B7648C3"
            or expr.dynamic(cfg.get("m_entity")) != (graph.ENTITY, 512)):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
    barrier = world.match.combat.barriers.get(entity)
    if isinstance(barrier, Barrier) and barrier.instance is instance:
        return expr.f32(barrier.vitals.total)
    return expr.EVALUATION_FAILED


def silence(component):
    # The generic LogicalButton gate also ignores releases while silenced.
    # Release the actual 1B55 continuous-deploy state through its authored branch.
    component.edges([(29, False)])
    for instance in tuple(component.instances.values()):
        if instance.graph.index != BARRIER_GRAPH:
            continue
        button = instance.states.get(10)
        if isinstance(button, nodes.LogicalButton) and button.active and button.down:
            button.go_up()


def gap_buttons(script, buttons):
    if script is None or script.hero != HERO or not script.buttons & buttons & SECONDARY:
        return 0
    phase = script.component.entity_var(PHASE, create=False)
    return SECONDARY if phase is not None and phase.value() == TRAVELLING else 0
