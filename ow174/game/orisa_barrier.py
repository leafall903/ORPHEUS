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

from ow174.game import bots, contacts
from ow174.game.collision import MOVER, NOT_GROUND, World
from ow174.game.script import expr, graph, runtime
from ow174.paths import DATA_DIR

GEOMETRY_SHA256 = "8e17ea34e5742cba5bb11f4049a5326d4261ee641dd5deffb9281d5b20a764b9"
HERO = 0x02E000000000013E
CAST_GRAPH = 0x0BD7


def load_geometry():
    selected = os.environ.get("ORPHEUS_ORISA_COLLISION")
    path = Path(selected) if selected else DATA_DIR / "orisa_collision_174.json"
    if not selected and not path.is_file():
        return None
    raw = path.read_bytes()
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
    team: int
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
        super().__init__(HERO, mode)
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
        self.casts = {}

    @staticmethod
    def admits(state):
        return (state.instance.graph.index, state.node.pos) == (CAST_GRAPH, 1)

    @staticmethod
    def pending(script):
        return any(instance.graph.index == CAST_GRAPH
                   and (state := instance.states.get(7)) is not None and state.active
                   for instance in script.component.instances.values())

    def remember_pose(self, owner, command):
        if self.geometry is not None and self.pending(owner.body_script):
            self.casts[owner] = (owner.body_script, self.fight.tick, command.frame)
        else:
            self.casts.pop(owner, None)

    def launch_unposed(self, owner, state, time):
        from ow174.game.combat import TICK, aim

        anchor = self.casts.get(owner)
        if anchor is None or anchor[0] is not owner.body_script:
            return
        script, world_tick, body_frame = anchor
        elapsed = max(0, time // script.frame_ms - body_frame) * script.frame_ms
        tick = min(self.fight.tick, world_tick + math.ceil(elapsed / (TICK * 1000)))
        yaw, pitch = round(owner.mover.yaw), round(owner.mover.pitch)
        self.launch(owner, state, self.fight._eye(owner), aim(yaw, pitch), yaw, tick=tick)

    def allocate(self):
        entity = self.fight.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("Protective Barrier entity allocator exhausted")
        self.fight.next_bot += 1
        return entity

    def launch(self, owner, state, origin, direction, yaw, *, tick=None):
        if self.geometry is None or graph.graph(0xBD8) is None or self.fight.match.ended:
            return
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
        self.flights[owner.body] = Flight(owner, state.instance.component, body, owner.team, origin,
                                          tuple(speed*v for v in direction), yaw, gravity, lifetime,
                                          self.fight.tick if tick is None else tick)
        state.instance.write(expr.dynamic(node.field("m_505472EF")), expr.Entity(body))

    def land(self, flight, position, tick):
        owner = flight.owner
        body = self.allocate()
        actor = Shield(owner, flight.source.entity, body, flight.team, position, flight.yaw)
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
        from ow174.game.projectiles import MAX_LEASE_FRAME

        for owner, (script, world_tick, body_frame) in tuple(self.casts.items()):
            if (self.fight.match.ended or owner.gone or owner.body_script is not script or script.retired
                    or self.fight.is_dead(owner) or self.fight.is_stunned(owner) or not self.pending(script)):
                del self.casts[owner]
                continue
            elapsed = max(0, tick - world_tick) * round(dt * 1000)
            frame = min(MAX_LEASE_FRAME - 1, body_frame + elapsed // script.frame_ms)
            owner._advance_body_gap(frame)
        for source, flight in tuple(self.flights.items()):
            script = flight.owner.body_script
            if (self.fight.match.ended or flight.owner.gone or script is None
                    or script.retired or script.component is not flight.source):
                del self.flights[source]
                continue
            for frame in range(flight.tick + 1, tick + 1):
                if flight.remaining <= 0 or self.advance(flight, frame, min(dt, flight.remaining)):
                    del self.flights[source]
                    break
        for source, actor in tuple(self.actors.items()):
            # The finish of the countdown starts a second authored timer. Feed
            # retained world frames so a late update does not shift that tail.
            if actor.owner.gone or self.fight.match.ended:
                actor.retired = True
            for frame in range(actor.component.frame + 1, tick + 1):
                if actor.retired:
                    break
                actor.component.run_frame(frame)
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

    def intersect(self, actor, origin, direction, reach, radius):
        centre, sine, cosine = self.pose(actor)
        x, y, z = (a - b for a, b in zip(origin, centre, strict=True))
        dx, dy, dz = direction
        local = x * cosine - z * sine, y, x * sine + z * cosine
        ray = dx * cosine - dz * sine, dy, dx * sine + dz * cosine
        world = mesh(self.geometry[0])
        if radius <= 0:
            hit = world.cast_ray(local, ray, reach, MOVER)
            return hit.distance if hit is not None else None
        end = tuple(a + b * reach for a, b in zip(local, ray, strict=True))
        hit = contacts.scene_sweep(world.engine, local, end, ((0.0, 0.0, 0.0),), radius, MOVER)
        return hit[0] * reach if hit is not None else None

    def trace(self, shooter, origin, direction, reach, radius=0.0):
        return min(((hit, actor) for actor in self.enemies(shooter)
                    if (hit := self.intersect(actor, origin, direction, reach, radius)) is not None),
                   key=lambda item: item[0], default=None)

    def melee(self, shooter, origin, centre, radius, amount):
        from ow174.game.combat import capsule_visible

        for actor in self.enemies(shooter):
            position, sine, cosine = self.pose(actor)
            x, y, z = (a - b for a, b in zip(centre, position, strict=True))
            local = x * cosine - z * sine, y, x * sine + z * cosine
            for face in self.geometry[0]:
                witness = contacts.closest_on_triangle(face[:3], face[3:6], face[6:], local)
                if math.dist(witness, local) > radius:
                    continue
                x, y, z = witness
                point = (position[0] + x * cosine + z * sine, position[1] + y,
                         position[2] - x * sine + z * cosine)
                if capsule_visible(self.fight._collision(), origin, point, point, 0., walls=self.fight.walls):
                    self.fight._damage_barrier((0.0, actor.owner, actor), amount, shooter=shooter)
                    break

    def damage(self, actor, amount):
        if amount <= 0 or not actor.active():
            return
        actor.health = expr.f32(max(0, actor.health - amount))
        instance = actor.instance()
        instance.write((expr.ENTITY, 253), actor.health)
        if actor.health <= 0:
            instance.write((expr.ENTITY, 478), True)
        actor.component.run_now()
