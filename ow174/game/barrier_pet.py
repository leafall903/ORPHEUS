"""Scoped server host for Reinhardt's authored shield Pet and health pool."""

import json
import math
import os
from functools import lru_cache
from pathlib import Path

from ow174.game import world
from ow174.game.collision import RayWorld
from ow174.game.script import expr, graph, play, runtime
from ow174.game.script.health_queries import pool_sum

HERO = 0x02E0000000000007
DEFINITION = 0x040000000000011A
OWNER_GRAPH = 0x95
POOL_GRAPH = 0x96


def configured_geometry():
    path = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "orpheus-reinhardt-barrier-v1" or data.get("build") != 104319:
        raise ValueError("invalid Reinhardt barrier descriptor version")
    faces = tuple(tuple(face) for face in data["triangles"])
    anchors = tuple(data["owner_anchor"]), tuple(data["child_anchor"])
    offset = data["forward_offset"]
    if (not faces or any(len(face) != 9 or not all(map(math.isfinite, face)) for face in faces)
            or any(len(anchor) != 3 or not all(map(math.isfinite, anchor)) for anchor in anchors)
            or not math.isfinite(offset)):
        raise ValueError("invalid Reinhardt barrier geometry")
    return faces, *anchors, offset


@lru_cache(maxsize=4)
def mesh(faces):
    return RayWorld(faces, name="Reinhardt shield")


def pose(geometry, target):
    faces, anchor, child_anchor, offset = geometry
    mover = target.owner.mover
    yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
    sy, cy = math.sin(yaw), math.cos(yaw)
    sp, cp = math.sin(pitch), math.cos(pitch)
    right, up, forward = (cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp)
    axes = (right, up, forward)
    local = (right, (0, 1, 0), (sy, 0, cy))
    base = tuple(sum(axis[i] * coord for axis, coord in zip(local, anchor, strict=True))
                 - sum(axis[i] * coord for axis, coord in zip(axes, child_anchor, strict=True))
                 for i in range(3))
    radius = target.radius
    support = max(forward[1] * radius, forward[1] * (target.height - radius)) + radius
    rear = min(face[i] for face in faces for i in (2, 5, 8))
    depth = sum(a * b for a, b in zip(base, forward, strict=True))
    shift = max(offset, support - rear + offset - depth)
    origin = tuple(target.feet[i] + base[i] + forward[i] * shift for i in range(3))
    return origin, axes


def active_colliders(combat, targets):
    if combat.barrier_geometry is None or not combat.barrier_pets:
        return ()
    geometry = combat.barrier_geometry
    surface = mesh(geometry[0])
    colliders = []
    for target in targets:
        pet = combat.barrier_pets.get(target.entity)
        if pet is not None and pet.world.health_pools and pool_sum(pet.world.health_pools) > 0:
            colliders.append((target, pet, surface, *pose(geometry, target)))
    return tuple(colliders)


def contact(colliders, origin, direction, reach):
    nearest = None
    for target, pet, surface, center, axes in colliders:
        delta = tuple(a - b for a, b in zip(origin, center, strict=True))
        local = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in axes)
        toward = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in axes)
        hit = surface.cast_ray(local, toward, reach)
        if hit is not None and (nearest is None or hit.distance < nearest[0]):
            nearest = hit.distance, target, pet
    return nearest


def damage(target, pet, amount, point):
    records = pet.world.health_pools
    current, maximum = pool_sum(records), pool_sum(records, maximum=True)
    remaining = expr.f32(max(0.0, current - amount))
    pet.world.health_pools = tuple(world.health(remaining, maximum)[51])
    pet.set(253, remaining)
    script = target.owner.body_script
    instance = next(i for i in script.component.instances.values() if i.graph.index == OWNER_GRAPH)
    # Mirror the owned pool into the shield graph's resource variable.
    instance.write((0, 586), remaining)
    if remaining == 0:
        # Deliver the authored break entry with the reconstructed impact pose.
        yaw = expr.f32(target.owner.mover.yaw * 360 / 65536)
        runtime.send_instance_message(instance, 0x0240000000000053,
                                      {0x5D6: expr.Vec3(*point), 0x5D7: yaw}, pet.entity)
    script.component.run_now()


def pool_value(instance, cfg):
    combat = owner_combat(instance)
    if combat is None:
        return NotImplemented
    pet = combat.barrier_pets.get(instance.component.entity)
    if pet is None or pet.world.health_pools is None:
        return NotImplemented
    if expr.to_entity(instance.evaluate(cfg.get("m_entity"))) != pet.entity:
        return NotImplemented
    kind = cfg.get("m_7E8B666C") or {}
    if kind.get("m_7E8B666C") == 0:
        return pool_sum(pet.world.health_pools)
    return NotImplemented


class BarrierWorld(runtime.World):
    def native(self, cfg, instance):
        if cfg.get("$") == "STU_295F1CDE" and instance.graph.index == POOL_GRAPH:
            # Owned Pet context with no inherited cached-stat overrides.
            return 1.0
        return super().native(cfg, instance)

    def install_pool(self, state):
        if state.instance.graph.index != POOL_GRAPH or state.node.pos != 3:
            return
        maximum = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
        initial = expr.to_float(state.evaluate(state.node.field("m_BCFDD2E9"), watch=False))
        if not math.isfinite(maximum) or maximum <= 0 or not math.isfinite(initial):
            raise ValueError("invalid Reinhardt barrier health pool")
        current = expr.f32(max(0, min(maximum, initial)))
        self.health_pools = tuple(world.health(current, maximum)[51])
        state.instance.component.set(253, current)


def owner_combat(instance):
    from ow174.game.script.driver import BodyWorld

    component = instance.component
    host = component.world
    if (component.strict or not isinstance(host, BodyWorld) or host.match is None
            or host.hero != HERO or instance.graph.index != OWNER_GRAPH):
        return None
    if any(player.has_body and not player.gone and player.body == component.entity
           and player.body_script is not None and player.body_script.component is component
           for player in host.match.players):
        return host.match.combat
    return None


def begin(state):
    from ow174.game.script.driver import create_body_graphs

    instance = state.instance
    combat = owner_combat(instance)
    if combat is None or state.node.pos != 51:
        return
    definition = state.node.field("m_entityDefinition") or {}
    if expr.guid_of(definition.get("m_entityDef")) != DEFINITION:
        return
    record = graph.entity_body(DEFINITION)
    if record is None or graph.graph(POOL_GRAPH) is None:
        return
    owner = instance.component
    retire(combat, owner.entity)
    entity = combat.next_bot
    if entity >= 0xD0000000:
        raise ValueError("barrier entity allocator exhausted")
    combat.next_bot += 1
    pet = runtime.Component(entity, world=BarrierWorld(HERO, owner.world.mode))
    pet.frame, pet.now = owner.frame, owner.now
    for root in create_body_graphs(pet, record):
        root.init()
    pet.run_now()
    combat.barrier_pets[owner.entity] = pet
    instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(entity))


def retire(combat, owner):
    pet = combat.barrier_pets.pop(owner, None)
    if pet is not None:
        for instance in list(pet.instances.values()):
            pet.destroy(instance)
        pet.world.health_pools = None


def end(state):
    combat = owner_combat(state.instance)
    if combat is not None and state.node.pos == 51:
        pet = combat.barrier_pets.get(state.instance.component.entity)
        target = expr.lvalue(state.node.field("m_B3019338"))
        if pet is None or target is None:
            return
        if expr.to_entity(state.instance.find_var(*target).value()) != pet.entity:
            return
        retire(combat, state.instance.component.entity)
        state.instance.write(target, None)


def play_owned(instance, node):
    combat = owner_combat(instance)
    if combat is None or node.pos != 6:
        return
    owner = instance.component.entity
    pet = combat.barrier_pets.get(owner)
    if pet is None:
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != 0x0580000000000096:
        return
    request = play.resolve_request(instance, node, resource,
                                   context_provider=lambda: play.InvocationContext(owner, owner, owner))
    if request is not None and request.targets == (pet.entity,) and request.wire_flag == 0:
        pet.play_loaded_graph(graph.graph(POOL_GRAPH), source=request.context.caller,
                              instigator=request.context.instigator, params=request.params,
                              overrides=resource.overrides, policy=request.policy)
        pet.run_now()
