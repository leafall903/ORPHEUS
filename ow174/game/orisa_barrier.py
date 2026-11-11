"""Host 0BD7's thrown barrier and 0BD8's landed actor.

The launch, gravity, entity definitions, mesh, pool and timers come from 104319.
Eye origin, ground-only landing, wall bounce, mesh alignment, replacement and
enemy interception are reconstructed server policies.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game.collision import MOVER, NOT_GROUND, World
from ow174.game.contacts import closest_on_triangle
from ow174.game.script import expr, graph, runtime

_EXPECTED_GEOMETRY = "8e17ea34e5742cba5bb11f4049a5326d4261ee641dd5deffb9281d5b20a764b9"
CAST_GRAPH = 0x0BD7
# 1846 carries tag 145.06C, excluded by the Matrix, Deflect and
# Grasp filters. This flight checks world contact directly.
FLIGHT_ENTITY = 0x0400000000001846


def descriptor_sha256():
    path = os.environ.get("ORPHEUS_ORISA_COLLISION")
    if not path:
        return None
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != _EXPECTED_GEOMETRY:
        raise ValueError("Orisa barrier geometry checksum mismatch")
    return digest


def load_geometry():
    path = os.environ.get("ORPHEUS_ORISA_COLLISION")
    if not path:
        return None
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != _EXPECTED_GEOMETRY:
        raise ValueError("Orisa barrier geometry checksum mismatch")
    data = json.loads(raw)
    if (data["schema"], data["build"]) != ("orpheus-orisa-barrier-v1", 104319):
        raise ValueError("Orisa barrier geometry schema mismatch")
    triangles = tuple(map(tuple, data["triangles"]))
    return triangles, min(y for triangle in triangles for y in triangle[1::3])


@cache
def mesh(triangles):
    return World(triangles, name="Orisa barrier")


@dataclass
class Flight:
    owner: object
    source: object
    body: int
    position: tuple
    velocity: tuple
    yaw: int
    gravity: float
    remaining: float
    tick: int
    shot: object
    flown: float = 0.


@dataclass
class Shield:
    owner: object
    source_body: int
    body: int
    team: int
    position: tuple
    yaw: int
    component: object = None
    health: float = 0
    retired: bool = False

    def instance(self):
        return next(i for i in self.component.instances.values() if i.graph.index == 0xBD8)

    def active(self):
        if self.retired or self.health <= 0:
            return False
        state = self.instance().states.get(0)
        return state is not None and state.active


class BarrierWorld(runtime.World):
    def __init__(self, mode, actor):
        super().__init__(0x02E000000000013E, mode)
        self.actor = actor

    def position(self, entity):
        if entity == self.actor.body:
            return expr.Vec3(*self.actor.position)
        return super().position(entity)

    def destroy_entity(self, instance, node):
        if instance.graph.index == 0xBD8 and node.pos in (7, 36):
            self.actor.retired = True
            return True
        return NotImplemented


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.geometry = load_geometry()
        self.flights = {}
        self.actors = {}

    def admits(self, state):
        return self.geometry is not None and (state.instance.graph.index, state.node.pos) == (CAST_GRAPH, 1)

    def launch(self, owner, state, origin, direction, yaw, *, frame, activation, snapshot, tick=None):
        node = state.node
        impact = node.field("m_49CBA3BC")
        if (impact["m_entityDefinition"]["m_entityDef"], impact["m_410BF8BD"]["m_graph"]) != (
            "0x04000000000014A1", "0x0580000000000BD8",
        ):
            raise ValueError("Orisa landing resources changed")
        speed = expr.to_float(state.evaluate(node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(node.field("m_projectileLifetime"), watch=False))
        motion, = node.field("m_projectileMotions")
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        body = self.allocate()
        shot = self.fight.projectiles.fired(
            owner, state, frame, FLIGHT_ENTITY, lifetime, origin, direction,
            activation=activation, snapshot=snapshot,
        )
        if owner.body in self.flights:
            self.end_flight(owner.body)
        self.flights[owner.body] = Flight(owner, state.instance.component, body, origin,
                                          tuple(speed*v for v in direction), yaw, gravity, lifetime,
                                          self.fight.tick if tick is None else tick, shot)
        state.instance.write(expr.dynamic(node.field("m_505472EF")), expr.Entity(body))

    def allocate(self):
        body = self.fight.next_bot
        if body >= 0xD0000000:
            raise ValueError("Orisa barrier entity allocator exhausted")
        self.fight.next_bot += 1
        return body

    def end_flight(self, source):
        flight = self.flights.pop(source)
        if flight.shot is not None:
            self.fight.projectiles.flown(flight.shot, flight.flown)

    def land(self, flight, position, tick):
        owner = flight.owner
        body = self.allocate()
        actor = Shield(owner, flight.source.entity, body, owner.team, position, flight.yaw)
        component = runtime.Component(body, world=BarrierWorld(self.fight.match.game_map.mode_guid, actor))
        component.frame, component.now = tick, tick * component.frame_ms
        actor.component = component
        instance = component.play_loaded_graph(graph.graph(0xBD8), source=actor.source_body,
                                               instigator=actor.source_body)
        component.run_now()
        actor.health = expr.to_float(instance.vars[587].value())
        if not actor.active():
            raise ValueError("Orisa barrier health pool failed to initialize")
        instance.write((expr.ENTITY, 253), actor.health)
        instance.write((expr.ENTITY, 478), False)
        component.run_now()
        for source, old in tuple(self.actors.items()):
            if old.owner is not owner:
                continue
            old.retired = True
            for child in tuple(old.component.instances.values()):
                old.component.destroy(child)
            del self.actors[source]
        self.actors[actor.source_body] = actor

    def update(self, tick, dt):
        for source, flight in tuple(self.flights.items()):
            script = flight.owner.body_script
            if flight.owner.gone or script is None or script.retired or script.component is not flight.source:
                self.end_flight(source)
                continue
            for frame in range(flight.tick + 1, tick + 1):
                if flight.remaining <= 0 or self.advance(flight, frame, min(dt, flight.remaining)):
                    self.end_flight(source)
                    break
        for source, actor in tuple(self.actors.items()):
            # The finish of the countdown starts a second authored timer. Feed
            # retained world frames so a late update does not shift that tail.
            for frame in range(actor.component.frame + 1, tick + 1):
                actor.component.run_frame(frame)
                if actor.retired:
                    break
            if actor.retired:
                for child in tuple(actor.component.instances.values()):
                    actor.component.destroy(child)
                del self.actors[source]

    def advance(self, flight, tick, dt):
        flight.tick = tick
        flight.remaining -= dt
        x, y, z = flight.velocity
        displacement = (x*dt, (y-flight.gravity*dt/2)*dt, z*dt)
        distance = math.hypot(*displacement)
        velocity = (x, y-flight.gravity*dt, z)
        if distance <= 1e-9:
            flight.velocity = velocity
            flight.flown += dt
            return False
        direction = tuple(v/distance for v in displacement)
        hit = self.fight._collision().cast_ray(flight.position, direction, distance)
        if hit is None:
            flight.position = tuple(a+b for a, b in zip(flight.position, displacement, strict=True))
            flight.velocity = velocity
            flight.flown += dt
            return False
        normal = hit.normal
        if normal[1] > .5 and y <= 0 and not hit.surface & NOT_GROUND:
            flight.flown += dt * hit.distance / distance
            self.land(flight, hit.point, tick)
            return True
        flight.flown += dt
        projection = sum(a*b for a, b in zip(velocity, normal, strict=True))
        flight.velocity = tuple(a-1.5*projection*b for a, b in zip(velocity, normal, strict=True))
        flight.position = tuple(a+.002*b for a, b in zip(hit.point, normal, strict=True))
        return False

    def enemies(self, shooter):
        for actor in self.actors.values():
            if actor.active() and actor.owner is not shooter and (
                self.fight.match.game_map.free_for_all or actor.team != shooter.team
            ):
                yield actor

    def pose(self, actor):
        x, y, z = actor.position
        angle = actor.yaw * math.tau / 65536
        return (x, y - self.geometry[1], z), math.sin(angle), math.cos(angle)

    def intersect(self, actor, origin, direction, reach):
        centre, sine, cosine = self.pose(actor)
        x, y, z = (a - b for a, b in zip(origin, centre, strict=True))
        dx, dy, dz = direction
        local = x * cosine - z * sine, y, x * sine + z * cosine
        ray = dx * cosine - dz * sine, dy, dx * sine + dz * cosine
        hit = mesh(self.geometry[0]).cast_ray(local, ray, reach, MOVER)
        return hit.distance if hit is not None else None

    def intercept(self, shooter, origin, direction, reach):
        hit = min(self.projectile_contacts(shooter, origin, direction, reach), key=lambda hit: hit[0], default=None)
        return hit[:2] if hit is not None else None

    def projectile_contacts(self, shooter, origin, direction, reach):
        for actor in self.enemies(shooter):
            distance = self.intersect(actor, origin, direction, reach)
            if distance is not None:
                yield distance, actor, actor.body

    def blast_distance(self, actor, origin, reach, *, ice_walls=None):
        return self._visible_distance(actor, origin, origin, reach, ice_walls)

    def melee(self, shooter, eye, centre, radius, amount):
        for actor in self.enemies(shooter):
            if self._visible_distance(actor, centre, eye, radius, self.fight.ice_walls) is not None:
                self.damage(actor, amount)

    def _visible_distance(self, actor, centre, eye, reach, ice_walls):
        from ow174.game.combat import capsule_visible

        origin, sine, cosine = self.pose(actor)
        x, y, z = (a - b for a, b in zip(centre, origin, strict=True))
        local = x * cosine - z * sine, y, x * sine + z * cosine
        nearest = None
        for face in self.geometry[0]:
            witness = closest_on_triangle(face[:3], face[3:6], face[6:], local)
            distance = math.dist(witness, local)
            if distance > reach or (nearest is not None and distance >= nearest):
                continue
            x, y, z = witness
            point = origin[0] + x * cosine + z * sine, origin[1] + y, origin[2] - x * sine + z * cosine
            if capsule_visible(self.fight._collision(), eye, point, point, 0.0, ice_walls=ice_walls):
                nearest = distance
        return nearest

    def explosion(self, shooter, origin, targets, reach, blast_amount):
        protected = set()
        for actor in self.enemies(shooter):
            distance = self.blast_distance(actor, origin, reach, ice_walls=self.fight.ice_walls)
            if distance is None:
                continue
            for target in targets:
                centre = target.feet[0], target.feet[1] + target.height / 2, target.feet[2]
                length = math.dist(origin, centre)
                if length <= 1e-9:
                    continue
                direction = tuple((a - b) / length for a, b in zip(centre, origin, strict=True))
                if self.intersect(actor, origin, direction, length) is not None:
                    protected.add(target.entity)
            self.damage(actor, blast_amount(distance))
        return protected

    def damage(self, actor, amount):
        if amount <= 0 or not actor.active():
            return
        actor.health = expr.f32(max(0, actor.health - amount))
        instance = actor.instance()
        instance.write((expr.ENTITY, 253), actor.health)
        if actor.health <= 0:
            instance.write((expr.ENTITY, 478), True)
        actor.component.run_now()
