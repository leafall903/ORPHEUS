"""104319 Orisa deployment, authored shield clock and static mesh collision."""

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import world
from ow174.game.collision import RayWorld
from ow174.game.script import expr, graph, runtime
from ow174.game.script.health_queries import pool_sum

HERO = 0x02E000000000013E
THROW_GRAPH = 0xBD7
SHIELD_GRAPH = 0xBD8
DEFINITION = 0x04000000000014A1
DEPLOY_SECONDS = .1  # 0252 deployment delay


def configured_geometry():
    path = os.environ.get("ORPHEUS_ORISA_GEOMETRY")
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (data.get("schema") != "orpheus-orisa-geometry-v1" or data.get("build") != "1.74.0.0.104319"
            or data.get("model") != "0x0D00000000004062"):
        raise ValueError("invalid Orisa shield descriptor")
    faces, masks = tuple(map(tuple, data["triangles"])), tuple(data["masks"])
    if (not faces or len(faces) != len(masks)
            or any(len(face) != 9 or not all(map(math.isfinite, face)) for face in faces)
            or any(type(mask) is not int or not 0 <= mask <= 0xFFFF for mask in masks)):
        raise ValueError("invalid Orisa shield mesh")
    return faces, masks


@lru_cache(maxsize=4)
def mesh(geometry):
    return RayWorld(*geometry, name="Orisa shield")


@dataclass
class Flight:
    body: int
    team: int
    position: tuple
    velocity: tuple
    yaw: float
    gravity: float
    left: float
    tick: int
    due: int | None = None

    def advance(self, collision, tick, dt, delay=DEPLOY_SECONDS):
        while self.due is None and self.tick < tick and self.left > 0:
            step = min(dt, self.left)
            vx, vy, vz = self.velocity
            delta = vx * step, (vy - self.gravity * step / 2) * step, vz * step
            length = math.sqrt(sum(axis * axis for axis in delta))
            hit = None
            if length > 0:
                direction = tuple(axis / length for axis in delta)
                hit = collision.cast_ray(self.position, direction, length)
            self.tick += 1
            if hit is not None:
                self.position = hit.point
                self.due = self.tick + math.ceil(delay / dt)
                break
            self.position = tuple(a + b for a, b in zip(self.position, delta, strict=True))
            self.velocity = vx, vy - self.gravity * step, vz
            self.left -= step


@dataclass
class Shield:
    body: int
    team: int
    position: tuple
    yaw: float
    geometry: tuple
    component: runtime.Component

    @property
    def health(self):
        return pool_sum(self.component.world.health_pools)

    def state_active(self, index):
        return any((state := instance.states.get(index)) is not None and state.active
                   for instance in self.component.instances.values() if instance.graph.index == SHIELD_GRAPH)

    def ray(self, origin, direction, reach):
        cosine, sine = math.cos(self.yaw), math.sin(self.yaw)

        def local(vector):
            x, y, z = vector
            return cosine * x - sine * z, y, sine * x + cosine * z

        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        hit = mesh(self.geometry).cast_ray(local(delta), local(direction), reach, category=0xFFFF)
        return hit.distance if hit is not None else None

    def damage(self, amount):
        records = self.component.world.health_pools
        current = expr.f32(max(0.0, self.health - amount))
        self.component.world.health_pools = tuple(world.health(current, pool_sum(records, maximum=True))[51])
        self.component.set(253, current)

    def retire(self):
        for instance in list(self.component.instances.values()):
            self.component.destroy(instance)


def launch(combat, shooter, state, origin, direction, speed, lifetime):
    if (combat.orisa_geometry is None or state.instance.graph.index != THROW_GRAPH
            or state.node.pos != 1 or state.instance.component.entity != shooter.body):
        return False
    spawn = state.node.field("m_49CBA3BC") or {}
    definition = spawn.get("m_entityDefinition") or {}
    if expr.guid_of(definition.get("m_entityDef")) != DEFINITION:
        return False
    motions = state.node.field("m_projectileMotions") or ()
    gravity = next((expr.to_float(state.evaluate(motion.get("m_599AB8D7"), watch=False))
                    for motion in motions if motion.get("$") == "STU_E1A5B579"), 0)
    if not all(math.isfinite(value) for value in (speed, lifetime, gravity)) or speed <= 0 or lifetime <= 0:
        raise ValueError("invalid Orisa deployment projectile")
    combat.orisa_flights.append(Flight(
        shooter.body, shooter.team, origin, tuple(axis * speed for axis in direction),
        math.atan2(direction[0], direction[2]), gravity, lifetime, combat.tick,
    ))
    return True


def deploy(combat, flight, tick):
    definition = graph.graph(SHIELD_GRAPH)
    if definition is None:
        return
    entity = combat.next_bot
    if entity >= 0xD0000000:
        raise ValueError("Orisa shield entity allocator exhausted")
    component = runtime.Component(entity, world=runtime.World(HERO, combat.match.game_map.mode_guid))
    component.frame, component.now = flight.due, flight.due * component.frame_ms
    instance = component.create(definition)
    instance.start_entries((), flight.body)
    component.run_now()
    health = expr.to_float(instance.find_var(0, 587).value())
    if health <= 0 or not math.isfinite(health):
        raise ValueError("0BD8 did not initialize shield health")
    component.world.health_pools = tuple(world.health(health, health)[51])
    component.set(253, health)
    combat.next_bot += 1
    shield = Shield(flight.body, flight.team, flight.position, flight.yaw, combat.orisa_geometry, component)
    if tick > component.frame:
        component.run_frame(tick)
    old = combat.orisa_shields.pop(flight.body, None)
    if old is not None:
        old.retire()
    combat.orisa_shields[flight.body] = shield


def update(combat, tick, dt):
    for body, shield in tuple(combat.orisa_shields.items()):
        if tick > shield.component.frame:
            shield.component.run_frame(tick)
        if shield.health <= 0 or not (shield.state_active(0) or shield.state_active(11)):
            shield.retire()
            del combat.orisa_shields[body]
    for flight in tuple(combat.orisa_flights):
        flight.advance(combat._collision(), tick, dt)
        if flight.due is not None and tick >= flight.due:
            combat.orisa_flights.remove(flight)
            deploy(combat, flight, tick)
        elif flight.left <= 0:
            combat.orisa_flights.remove(flight)


def retire(combat, body):
    combat.orisa_flights[:] = [flight for flight in combat.orisa_flights if flight.body != body]
    shield = combat.orisa_shields.pop(body, None)
    if shield is not None:
        shield.retire()


def colliders(combat, body, team):
    ffa = combat.match.game_map.free_for_all
    return tuple(shield for shield in combat.orisa_shields.values()
                 if shield.body != body and (ffa or shield.team != team)
                 and shield.health > 0 and shield.state_active(0))
