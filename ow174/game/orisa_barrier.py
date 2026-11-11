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

from ow174.game import defenses
from ow174.game.collision import MOVER, NOT_GROUND, World
from ow174.game.contacts import closest_on_triangle
from ow174.game.script import expr, graph, runtime

_geometry_path = os.environ.get("ORPHEUS_ORISA_COLLISION")
_expected_geometry = "8e17ea34e5742cba5bb11f4049a5326d4261ee641dd5deffb9281d5b20a764b9"
GEOMETRY_SHA256 = _expected_geometry if _geometry_path else None
CAST_GRAPH = 0x0BD7


def load_geometry():
    if not _geometry_path:
        return None
    raw = Path(_geometry_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
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

    def pending(self, owner):
        return (self.geometry is not None
                and defenses.active_state(owner, defenses.ORISA, CAST_GRAPH, 7) is not None)

    def launch_unposed(self, owner, state, time):
        """Use the last committed pose for an already admitted delayed throw."""
        from ow174.game.combat import TICK, aim

        script = owner.body_script
        if not self.admits(state) or script.world_frame_anchor is None or owner.mover.last_command is None:
            return
        anchor_tick, anchor_frame = script.world_frame_anchor
        frame = time // script.frame_ms
        elapsed = max(0, frame - anchor_frame) * script.frame_ms
        tick = min(self.fight.tick, anchor_tick + math.ceil(elapsed / (TICK * 1000)))
        yaw, pitch = round(owner.mover.yaw), round(owner.mover.pitch)
        self.launch(owner, state, self.fight._eye(owner), aim(yaw, pitch), yaw, tick=tick)

    def launch(self, owner, state, origin, direction, yaw, *, tick=None):
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
        body = self.fight.next_bot
        self.fight.next_bot += 1
        self.flights[owner.body] = Flight(owner, state.instance.component, body, origin,
                                          tuple(speed*v for v in direction), yaw, gravity, lifetime,
                                          self.fight.tick if tick is None else tick)
        state.instance.write(expr.dynamic(node.field("m_505472EF")), expr.Entity(body))

    def land(self, flight, position, tick):
        owner = flight.owner
        body = self.fight.next_bot
        self.fight.next_bot += 1
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
                del self.flights[source]
                continue
            for frame in range(flight.tick + 1, tick + 1):
                if flight.remaining <= 0 or self.advance(flight, frame, min(dt, flight.remaining)):
                    del self.flights[source]
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
            return False
        direction = tuple(v/distance for v in displacement)
        hit = self.fight._collision().cast_ray(flight.position, direction, distance)
        if hit is None:
            flight.position = tuple(a+b for a, b in zip(flight.position, displacement, strict=True))
            flight.velocity = velocity
            return False
        normal = hit.normal
        if normal[1] > .5 and y <= 0 and not hit.surface & NOT_GROUND:
            self.land(flight, hit.point, tick)
            return True
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

    def trace(self, shooter, origin, direction, reach):
        return min(((hit, actor) for actor in self.enemies(shooter)
                    if (hit := self.intersect(actor, origin, direction, reach)) is not None),
                   key=lambda item: item[0], default=None)

    def covers(self, cover, origin, a, b):
        if not cover:
            return False
        target = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
        reach = math.dist(origin, target)
        if reach <= 0:
            return False
        direction = tuple((y - x) / reach for x, y in zip(origin, target, strict=True))
        return any((hit := self.intersect(actor, origin, direction, reach)) is not None and hit < reach
                   for actor in cover)

    def splash(self, point, origin, blast, cover, impact, blocked):
        from ow174.game.combat import capsule_visible

        for actor in cover:
            if actor is impact:
                continue
            centre, sine, cosine = self.pose(actor)
            x, y, z = (a - b for a, b in zip(point, centre, strict=True))
            local = x * cosine - z * sine, y, x * sine + z * cosine
            witness = min((closest_on_triangle(face[:3], face[3:6], face[6:], local)
                           for face in self.geometry[0]), key=lambda vertex: math.dist(vertex, local))
            scalar = blast.splash(math.dist(witness, local))
            x, y, z = witness
            surface = centre[0] + x * cosine + z * sine, centre[1] + y, centre[2] - x * sine + z * cosine
            if (scalar > 0 and capsule_visible(self.fight._collision(), origin, surface, surface, 0)
                    and not blocked(self, actor, surface)):
                self.damage(actor, blast.amount * scalar)

    def damage(self, actor, amount):
        if amount <= 0 or not actor.active():
            return
        actor.health = expr.f32(max(0, actor.health - amount))
        instance = actor.instance()
        instance.write((expr.ENTITY, 253), actor.health)
        if actor.health <= 0:
            instance.write((expr.ENTITY, 478), True)
        actor.component.run_now()
