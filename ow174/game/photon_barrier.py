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

from ow174.game import bots, contacts, pools, sigma_flux
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


class Geometry:
    def __init__(self, path):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
            raise ValueError("Photon Barrier geometry does not match the verified 104319 mesh")
        data = json.loads(raw)
        triangles = data["triangles"]
        self.world = World(triangles)
        self.triangles = tuple(tuple(tuple(t[k:k + 3]) for k in (0, 3, 6)) for t in triangles)
        self.source = data["model_sha256"]


    def cast(self, position, yaw, origin, direction, length):
        cosine, sine = math.cos(yaw), math.sin(yaw)

        def local(vector):
            x, y, z = vector
            return cosine * x - sine * z, y, sine * x + cosine * z

        delta = tuple(a - b for a, b in zip(origin, position, strict=True))
        hit = self.world.cast_ray(local(delta), local(direction), length, category=0xFFFF)
        return hit.distance if hit is not None else None

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

    def create(self, world, state):
        instance, node = state.instance, state.node
        if self.geometry is None or not _applies(world, instance) or node.pos != 17:
            return NotImplemented
        from ow174.game.combat import TICK

        definition = body_definition()
        if definition is None or state.evaluate(node.field("m_entityDefinition")) != expr.Asset(BODY):
            return NotImplemented
        owner = _owner(world)
        if owner is None or owner.gone or self.fight.is_dead(owner) or self.fight.is_stunned(owner):
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
        actor = Shield(owner, source, body, owner.team, tuple(position), yaw, health,
                       self.fight.match.tick + math.ceil(min(lifetime, watchdog) / TICK))
        actor.component = runtime.Component(body, frame_ms=16,
                                            world=BarrierWorld(self.fight.match.game_map.mode_guid, actor))
        actor.component.frame = self.fight.match.tick
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
        for actor in tuple(self.actors.values()):
            if tick >= actor.expires or actor.owner.gone or actor.owner.hero.guid != HERO:
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
            pet.pet_entity = None
            instance.write(expr.lvalue(pet.node.field("m_B3019338")), expr.Entity(0))
            if pet.active and not pet.ending:
                pet.request(runtime.ABORT)
                instance.component.run_now()

    def opponents(self, shooter, team=None):
        if team is None:
            team = shooter.team
        return tuple(actor for actor in self.actors.values()
                     if actor.health > 0 and actor.owner is not shooter
                     and (self.fight.match.game_map.free_for_all or actor.team != team))

    def closest(self, barrier, point):
        return self.geometry.closest(barrier.position, barrier.yaw, point)

    def trace(self, shooter, origin, direction, reach, *, team=None, barriers=None):
        if barriers is None:
            barriers = self.opponents(shooter, team)
        best = None
        for actor in barriers:
            distance = self.geometry.cast(actor.position, actor.yaw, origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, actor
        return best

    def damage(self, actor, amount):
        actor.health = expr.f32(max(0, actor.health - max(0, amount)))
        if actor.health <= 0:
            self.end(actor)

    def melee(self, shooter, centre, radius, amount, visible):
        for actor in self.opponents(shooter):
            point = self.closest(actor, centre)
            if math.dist(point, centre) <= radius and visible(point):
                self.damage(actor, amount)


def _applies(world, instance):
    return (not instance.component.strict and world.hero == HERO and world.match is not None
            and instance.component.entity == world.body and instance.graph.index == GRAPH)


def _owner(world):
    return next((p for p in world.match.players if p.has_body and p.body == world.body), None)


def placement(world, state, *, confirm):
    if (not _applies(world, state.instance) or state.node.pos != 57
            or body_definition() is None or graph.graph(ACTOR_GRAPH) is None):
        return NotImplemented
    owner = _owner(world)
    fight = world.match.combat
    if owner is None or owner.gone or not owner.spawned or fight.is_dead(owner) or fight.is_stunned(owner):
        return True
    return sigma_flux.place_on_static_ground(world, state, confirm=confirm)


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


def interrupt(script):
    """Abort 156B's confirmed windup before its finished plug can create the wall."""
    if getattr(script, "hero", None) != HERO or script.component.strict:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            wait = instance.states.get(instance.graph.node(0).state)
            if wait is not None and wait.active:
                wait.request(runtime.ABORT)




def replay_inputs():
    values = {}
    mesh = os.environ.get("ORPHEUS_SYMMETRA_COLLISION")
    if mesh:
        Geometry(Path(mesh))
        values["ow174.game.photon_barrier.mesh_sha256"] = GEOMETRY_SHA256
    if os.environ.get("ORPHEUS_PHOTON_ENTITY_BUNDLE") and graph.entity_body(BODY) is None:
        body_definition()
        values["ow174.game.photon_barrier.body_bundle_sha256"] = BODY_BUNDLE_HASH
    return values
