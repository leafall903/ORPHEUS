"""Selected Photon Barrier placement, pet graph and world interception.

Use the widest authored plane for map-spanning collision. The position query,
orientation bridge, finite pool and team filtering are server reconstruction.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, contacts, pools
from ow174.game.collision import World
from ow174.game.script import expr, graph, play, play_queue, runtime
from ow174.game.sigma_barrier import view_basis

HERO = 0x02E0000000000016
GRAPH, ACTOR_GRAPH = 0x156B, 0x156C
BODY = 0x04000000000026B0
BODY_BUNDLE_HASH = "0595250af9a5c12e7f1d881a21d8e3bd4f7c29cd5f69d40d8685985741fdfadb"


@cache
def supplemental_body(path):
    blob = Path(path).read_bytes()
    if hashlib.sha256(blob).hexdigest() != BODY_BUNDLE_HASH:
        raise ValueError("Photon actor bundle does not match verified 104319 input")
    return json.loads(blob)["entities"][f"0x{BODY:016X}"]


def body_definition():
    definition = graph.entity_body(BODY)
    path = os.environ.get("ORPHEUS_PHOTON_ENTITY_BUNDLE")
    if definition is None and path:
        definition = supplemental_body(path)
    return definition


GEOMETRY_SHA256 = "d11a09fc7224c2b73819a06a40f9dbb1cb35ff681c373aa1891a987de6eedfe8"


def geometry_bytes(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
        raise ValueError("Photon Barrier geometry does not match the verified 104319 mesh")
    return raw


class Geometry:
    def __init__(self, path):
        data = json.loads(geometry_bytes(path))
        triangles = data["triangles"]
        self.world = World(triangles)
        self.triangles = tuple(tuple(tuple(t[k:k + 3]) for k in (0, 3, 6)) for t in triangles)
        self.source = data["model_sha256"]


    def cast(self, position, yaw, origin, direction, length, radius):
        cosine, sine = math.cos(yaw), math.sin(yaw)

        def local(vector):
            x, y, z = vector
            return cosine * x - sine * z, y, sine * x + cosine * z

        delta = tuple(a - b for a, b in zip(origin, position, strict=True))
        point, ray = local(delta), local(direction)
        if radius <= 0:
            hit = self.world.cast_ray(point, ray, length, category=0xFFFF)
            return hit.distance if hit is not None else None
        end = tuple(a + b * length for a, b in zip(point, ray, strict=True))
        hit = contacts.scene_sweep(self.world.engine, point, end, ((0.0, 0.0, 0.0),), radius, 0xFFFF)
        return hit[0] * length if hit is not None else None

    def closest(self, position, yaw, point):
        """Closest actual mesh point for blast radius/distance, in world coordinates."""
        cosine, sine = math.cos(yaw), math.sin(yaw)
        x, y, z = (a - b for a, b in zip(point, position, strict=True))
        local = (cosine * x - sine * z, y, sine * x + cosine * z)
        candidates = (contacts.closest_on_triangle(a, b, c, local) for a, b, c in self.triangles)
        near = min(candidates, key=lambda p: math.dist(p, local))
        x, y, z = near
        return (position[0] + cosine * x + sine * z, position[1] + y,
                position[2] - sine * x + cosine * z)


@dataclass
class Shield:
    owner: object
    source_body: int
    body: int
    team: int
    position: tuple
    yaw: float
    health: float
    expires: int
    component: object = None


class BarrierWorld(runtime.World):
    def __init__(self, mode, actor):
        super().__init__(HERO, mode)
        self.actor = actor

    def position(self, entity):
        if entity == self.actor.body:
            return expr.Vec3(*self.actor.position)
        return super().position(entity)


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        override = os.environ.get("ORPHEUS_SYMMETRA_COLLISION")
        self.geometry = Geometry(Path(override)) if override else None
        self.actors = {}
        self.casts = {}

    def create(self, world, state):
        instance, node = state.instance, state.node
        if self.geometry is None or not _applies(world, instance) or node.pos != 17:
            return NotImplemented
        from ow174.game.combat import TICK

        definition = body_definition()
        if definition is None or state.evaluate(node.field("m_entityDefinition")) != expr.Asset(BODY):
            return NotImplemented
        owner = _owner(world)
        if (owner is None or owner.gone or self.fight.match.ended
                or self.fight.is_dead(owner) or self.fight.is_stunned(owner)):
            return NotImplemented
        position = state.evaluate(node.field("m_position"), watch=False)
        axis = state.evaluate(node.field("m_AD8E614B"), watch=False)
        if (not isinstance(position, expr.Vec3) or not isinstance(axis, expr.Vec3)
                or not math.hypot(axis.x, axis.z)
                or not all(math.isfinite(value) for value in (*position, *axis))):
            return NotImplemented
        row = next(row for row in definition["graphs"] if row["graph"] == pools.BODY_GRAPH)
        health = pools._numbers(row["m_1EB5A024"])[pools.HEALTH_VAR]
        lifetime = expr.to_float(instance.evaluate(instance.graph.node(18).field("m_EB0FC261")))
        watchdog = expr.to_float(instance.evaluate(graph.graph(ACTOR_GRAPH).node(42).field("m_timeout")))
        if not all(math.isfinite(value) and value > 0 for value in (health, lifetime, watchdog)):
            return NotImplemented
        body = self.fight.next_bot
        if not bots.BOT_ENTITY <= body < 0xD0000000:
            raise ValueError("Photon Barrier entity allocator exhausted")
        self.fight.next_bot += 1
        source = instance.component.entity
        for existing in tuple(self.actors.values()):
            if existing.owner is owner:
                self.end(existing)
        yaw = math.atan2(axis.x, axis.z)
        deployed = self.fight.tick
        cast = self.casts.pop(owner, None)
        if cast is not None and cast[0] is instance:
            _, confirmed_tick, confirmed_frame = cast
            elapsed = (instance.component.frame - confirmed_frame) * instance.component.frame_ms
            deployed = min(deployed, confirmed_tick + math.ceil(elapsed / (TICK * 1000)))
        actor = Shield(owner, source, body, owner.team, tuple(position), yaw, health,
                       deployed + math.ceil(min(lifetime, watchdog) / TICK))
        actor.component = runtime.Component(body, frame_ms=16,
                                            world=BarrierWorld(self.fight.match.game_map.mode_guid, actor))
        actor.component.frame = deployed
        actor.component.now = actor.component.frame * 16
        self.actors[source] = actor
        return body

    def play(self, instance, node):
        if self.geometry is None or (instance.graph.index, node.pos) != (0x156B, 13):
            return NotImplemented
        actor = self.actors.get(instance.component.entity)
        if actor is None:
            return True
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != 0x058000000000156C:
            raise ValueError("Photon Barrier receiver graph changed")
        source = instance.component.entity
        request = play.resolve_request(instance, node, resource, context_provider=lambda:
                                       play.InvocationContext(source, source, instance.source_entity()))
        if request is None or request.targets != (actor.body,):
            raise ValueError("Photon Barrier graph target does not resolve")
        queue = play_queue.Queue(actor.component, lambda _: play_queue.Load("ready", graph.graph(0x156C)))
        queue.submit(request)
        actor.component.run_now()
        return True

    def retire(self, source):
        actor = self.actors.pop(source, None)
        if actor is not None:
            for instance in tuple(actor.component.instances.values()):
                actor.component.destroy(instance)

    def update(self, tick):
        from ow174.game.combat import TICK
        from ow174.game.projectiles import MAX_LEASE_FRAME

        for owner, (instance, confirmed_tick, confirmed_frame) in tuple(self.casts.items()):
            script = owner.body_script
            wait = _active_wait(instance)
            if (self.fight.match.ended or owner.gone or owner.hero.guid != HERO
                    or script is None or script.retired
                    or script.component is not instance.component or instance.stopped
                    or self.fight.is_dead(owner) or self.fight.is_stunned(owner) or wait is None):
                del self.casts[owner]
                continue
            elapsed = max(0, tick - confirmed_tick) * round(TICK * 1000)
            after_windup = (wait.start + wait.ms) // script.frame_ms + 1
            frame = min(MAX_LEASE_FRAME - 1, confirmed_frame + elapsed // script.frame_ms, after_windup)
            # Leave the current frame for incoming input and deliver the wait on its
            # original body frame even if the scheduler skipped the early world ticks.
            owner._advance_body_gap(frame)
        for actor in tuple(self.actors.values()):
            if (self.fight.match.ended or tick >= actor.expires
                    or actor.owner.gone or actor.owner.hero.guid != HERO):
                self.end(actor)
                continue
            if tick > actor.component.frame:
                actor.component.run_frame(tick)

    def end(self, actor):
        self.retire(actor.source_body)
        script = actor.owner.body_script
        if script is None or script.component.entity != actor.source_body:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
        pet = instance.states.get(9) if instance is not None else None
        if pet is not None and pet.pet_entity == actor.body:
            pet.pet_entity = 0
            pet.dependency_changed()
            if pet.active and not pet.ending:
                pet.request(runtime.ABORT)
                instance.component.run_now()

    def opponents(self, shooter):
        return tuple(actor for actor in self.actors.values()
                     if actor.health > 0 and actor.owner is not shooter
                     and (self.fight.match.game_map.free_for_all or actor.team != shooter.team))

    def closest(self, barrier, point):
        return self.geometry.closest(barrier.position, barrier.yaw, point)

    def trace(self, shooter, origin, direction, reach, radius=0.0):
        best = None
        for actor in self.opponents(shooter):
            distance = self.geometry.cast(actor.position, actor.yaw, origin, direction, reach, radius)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, actor
        return best

    def damage(self, actor, amount):
        actor.health = expr.f32(max(0, actor.health - max(0, amount)))
        if actor.health <= 0:
            self.end(actor)

    def melee(self, shooter, origin, centre, radius, amount):
        from ow174.game.combat import capsule_visible

        for actor in self.opponents(shooter):
            point = self.closest(actor, centre)
            if (math.dist(point, centre) <= radius and capsule_visible(
                    self.fight._collision(), origin, point, point, 0., walls=self.fight.walls)):
                self.fight._damage_barrier((0.0, actor.owner, actor), amount, shooter=shooter)


def _applies(world, instance):
    return (not instance.component.strict and world.hero == HERO and getattr(world, "match", None) is not None
            and instance.component.entity == world.body and instance.graph.index == GRAPH)


def _owner(world):
    return next((p for p in world.match.players if p.has_body and p.body == world.body), None)


def placement(world, state, *, confirm):
    if (not _applies(world, state.instance) or state.node.pos != 57
            or body_definition() is None or graph.graph(ACTOR_GRAPH) is None):
        return NotImplemented
    owner = _owner(world)
    fight = world.match.combat
    if (not state.active or owner is None or owner.gone or not owner.spawned or fight.match.ended
            or fight.is_dead(owner) or fight.is_stunned(owner)):
        return True
    instance = state.instance
    reach = expr.to_float(state.evaluate(state.node.field("m_6386E7B4"), watch=False))
    direction = view_basis(owner.mover.yaw, owner.mover.pitch)[2]
    hit = fight._collision().cast_ray(fight._eye(owner), direction, reach)
    valid = hit is not None and hit.normal[1] > 1e-6
    if valid:
        point = expr.Vec3(*(expr.f32(value) for value in hit.point))
        instance.write(expr.lvalue(state.node.field("m_30710B76")), point)
        instance.write(expr.lvalue(state.node.field("m_1549F0F9")), expr.Entity(0))
        instance.write(expr.lvalue(state.node.field("m_2FECD1E3")), expr.Entity(0))
    instance.write(expr.lvalue(state.node.field("m_2339572B")), valid)
    if confirm:
        if valid and fight.photon_barriers.geometry is not None:
            fight.photon_barriers.casts[owner] = (instance, fight.tick, instance.component.frame)
        instance.follow(state.node, "m_F6955E81" if valid else "m_425337C6", state)
    return True


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
    right, _, forward = view_basis(owner.mover.yaw, 0)
    return expr.Vec3(*(right if kind == "STU_66F3B89E" else forward))


def _active_wait(instance):
    wait = instance.states.get(instance.graph.node(0).state)
    return wait if wait is not None and wait.active else None


def interrupt(script):
    """Abort 156B's confirmed windup before its finished plug can create the wall."""
    if script.hero != HERO or script.component.strict:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            if (wait := _active_wait(instance)) is not None:
                wait.request(runtime.ABORT)


def replay_inputs():
    values = {}
    mesh = os.environ.get("ORPHEUS_SYMMETRA_COLLISION")
    if mesh:
        geometry_bytes(mesh)
        values["ow174.game.photon_barrier.mesh_sha256"] = GEOMETRY_SHA256
    if os.environ.get("ORPHEUS_PHOTON_ENTITY_BUNDLE") and graph.entity_body(BODY) is None:
        body_definition()
        values["ow174.game.photon_barrier.body_bundle_sha256"] = BODY_BUNDLE_HASH
    return values
