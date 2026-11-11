"""Selected 104319 Experimental Barrier Pet host and authored mesh collision."""

import math
import os
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.barrier_geometry import barrier_geometry
from ow174.game.collision import World
from ow174.game.deflection import basis as view_basis
from ow174.game.grasp import HERO
from ow174.game.script import expr, graph

BARRIER_GRAPH = 0x1B55
BARRIER_DEFINITION = 0x0400000000002D25


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


def _mesh_intersection(geometry, position, basis, origin, direction, reach):
    delta = tuple(a - b for a, b in zip(origin, position, strict=True))
    point = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
    vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
    hit = geometry.cast_ray(point, vector, reach)
    return hit.distance if hit is not None else None


@dataclass(frozen=True)
class BarrierPose:
    geometry: World
    position: tuple
    basis: tuple

    def intersection(self, origin, direction, reach):
        return _mesh_intersection(self.geometry, self.position, self.basis, origin, direction, reach)


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
        phase = component.entity_var(19777, create=False)
        wanted = expr.to_int(phase.value()) if phase is not None else 1
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
        return _mesh_intersection(self.geometry, self.position, self.basis, origin, direction, reach)


def create_barrier(combat, world, state):
    if (state.instance.component.strict or world.hero != HERO
            or state.instance.graph.index != BARRIER_GRAPH or state.node.state != 4):
        return NotImplemented
    definition = state.evaluate(state.node.field("m_entityDefinition"), watch=False)
    if definition != expr.Asset(BARRIER_DEFINITION):
        return NotImplemented
    path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
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
    combat.barriers[entity] = Barrier(entity, world.body, owner, state.instance, Vitals(entity, amount),
                                     geometry, combat._eye(owner), basis, combat.now)
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
    barrier = world.match.combat.barriers.get(entity)
    return barrier.vitals.total if barrier is not None else expr.EVALUATION_FAILED


def play_barrier_request(combat, body, instance, node, resource):
    """Bind the actual self-owned Pet request to its reconstructed actor service.

    The1A2A graph's native engine execution is not claimed; its health, motion
    constants and owner phase inputs drive this explicit service.
    """
    if instance.component.strict or instance.graph.index != BARRIER_GRAPH or node.pos != 8:
        return False
    target = expr.to_entity(instance.evaluate(node.field("m_targets")))
    barrier = combat.barriers.get(target)
    if (resource.guid != 0x0580000000001A2A or barrier is None
            or barrier.owner_body != body or barrier.instance is not instance):
        return False
    barrier.child_request_seen = True
    return True


def update_barriers(combat):
    for entity, barrier in list(combat.barriers.items()):
        if (barrier.instance.stopped or barrier.instance.component.owner_dead
                or barrier.owner.body != barrier.owner_body or combat.match.ended):
            del combat.barriers[entity]
            state = barrier.instance.states.get(4)
            if state is not None and state.pet_entity == entity and not barrier.instance.stopped:
                state.pet_entity = None
                barrier.instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(0))
            continue
        barrier.update(combat)


def _active_enemy_barriers(combat, shooter):
    for barrier in combat.barriers.values():
        barrier.update(combat)
        if (not barrier.active or barrier.owner is shooter
                or (not combat.match.game_map.free_for_all and barrier.owner.team == shooter.team)):
            continue
        yield barrier


def snapshot_enemy_barriers(combat, shooter) -> tuple[BarrierPose, ...]:
    return tuple(BarrierPose(barrier.geometry, barrier.position, barrier.basis)
                 for barrier in _active_enemy_barriers(combat, shooter))


def barrier_intersection(combat, shooter, origin, direction, reach):
    best = None
    for barrier in _active_enemy_barriers(combat, shooter):
        distance = barrier.intersection(origin, direction, reach)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, barrier
    return best


def damage_barrier(barrier, amount):
    dealt = barrier.vitals.damage(amount)
    if barrier.vitals.total <= 0:
        barrier.active = False
    return dealt


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
