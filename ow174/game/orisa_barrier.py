"""Scoped static barrier combat consumer; network deployment remains unimplemented.

The shape comes from4062.00C. Original0BD8 initializes health/lifetime and
owns the health-pool scope that gates collision. World health/damage, team
filtering and placement remain reconstructed; native HealthPool internals,
destroy-effect tail, dynamic filters and network entity transport remain open.
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import contacts
from ow174.game.collision import World
from ow174.game.projectiles import deployable_contact
from ow174.game.script import expr, graph, runtime
from ow174.paths import DATA_DIR

TICK = 0.016
GRAPH = 0x0BD7
DEFINITION = 0x04000000000014A1
DEPLOY_DELAY = 0.1  # 0252


class BarrierWorld(runtime.World):
    def __init__(self, fight, entity, position):
        super().__init__(mode=fight.match.game_map.mode_guid)
        self.fight, self.entity, self.location = fight, entity, position

    def position(self, entity):
        if entity == self.entity:
            return expr.Vec3(*self.location)
        return super().position(entity)


class Geometry:
    def __init__(self, path, *, model="0x0D00000000004062"):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.load(data, model=model)

    def load(self, data, *, model):
        if (data.get("schema") != "orpheus-orisa-geometry-v1"
                or data.get("build") != "1.74.0.0.104319"
                or data.get("model") != model):
            raise ValueError("Orisa deployable geometry has wrong schema/build/model")
        triangles, masks = data["triangles"], data["masks"]
        if (not triangles or len(triangles) != len(masks)
                or any(len(t) != 9 or not all(math.isfinite(v) for v in t) for t in triangles)
                or any(type(mask) is not int or not 0 <= mask <= 0xFFFF for mask in masks)):
            raise ValueError("invalid Orisa barrier geometry")
        # Use the existing fingerprinted static-world asset type; queries still
        # use this model-local triangle mesh, with no character mover attached.
        self.world = World(triangles, masks)
        self.triangles = tuple(tuple(tuple(t[k:k + 3]) for k in (0, 3, 6)) for t in triangles)
        self.source = data["raw_sha256"]

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
class Barrier:
    entity: int
    owner: object
    source_body: int
    team: int
    position: tuple
    yaw: float
    until: int
    health: float
    component: runtime.Component | None = None
    script: runtime.Instance | None = None

    def blocking(self):
        if self.script is None:
            return True
        pool = self.script.states.get(self.script.graph.node(0).state)
        return pool is not None and pool.active


@dataclass
class Seed:
    owner: object
    body: int
    team: int
    position: tuple
    velocity: tuple
    yaw: float
    gravity: float
    remaining: float
    tick: int


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        path = Path(os.environ.get("ORPHEUS_ORISA_GEOMETRY", DATA_DIR / "orisa_geometry_174.json"))
        self.geometry = Geometry(path) if path.is_file() else None
        self.flights = []
        self.pending = []
        self.active = {}
        self.retiring = {}

    def launch(self, shooter, state, position, direction, speed, lifetime):
        if self.geometry is None or state.instance.graph.index != GRAPH:
            return False
        config = state.node.field("m_49CBA3BC.m_entityDefinition")
        if state.evaluate(config, watch=False) != expr.Asset(DEFINITION):
            return False
        motion = next((row for row in state.node.field("m_projectileMotions") or ()
                       if row.get("$") == "STU_E1A5B579"), None)
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False)) if motion else 0.0
        velocity = tuple(axis * speed for axis in direction)
        self.flights.append(Seed(shooter, shooter.body, shooter.team, position, velocity,
                                 math.atan2(direction[0], direction[2]), gravity, lifetime, self.fight.tick))
        return True

    def retire(self, body):
        self.flights[:] = [seed for seed in self.flights if seed.body != body]
        self.pending[:] = [(due, seed) for due, seed in self.pending if seed.body != body]

    def fly(self, seed, tick):
        hit = deployable_contact(seed, tick, self.fight._collision())
        if hit is not None:
            self.pending.append((hit + math.ceil(DEPLOY_DELAY / TICK), seed))
        return hit is None and seed.remaining > 0

    def update(self, tick):
        self.flights[:] = [seed for seed in self.flights if self.fly(seed, tick)]
        for entity, barrier in tuple(self.retiring.items()):
            barrier.component.run_frame(tick)
            if not self.tail_active(barrier):
                self.remove(entity)
        for entity, barrier in tuple(self.active.items()):
            barrier.component.run_frame(tick)
            if not barrier.blocking():
                if self.tail_active(barrier):
                    del self.active[entity]
                    self.retiring[entity] = barrier
                else:
                    self.remove(entity)
        for due, seed in tuple(self.pending):
            if tick >= due:
                self.pending.remove((due, seed))
                self.deploy(seed, due, tick)

    def deploy(self, seed, due, tick):
        entity = self.fight.next_bot
        if not 0xC0001000 <= entity < 0xD0000000:
            raise ValueError("ability entity allocator exhausted")
        component = runtime.Component(entity, world=BarrierWorld(self.fight, entity, seed.position))
        component.now, component.frame = due * 16, due
        script = component.create(graph.graph(0x0BD8))
        script.start_entries((), seed.body)
        component.run_now()
        health = expr.to_float(script.find_var(graph.INSTANCE, 587).value())
        seconds = expr.to_float(component.entity_var(567).value())
        until = due + math.ceil(seconds / TICK)
        if health <= 0 or tick >= until:
            component.destroy(script)
            return
        self.fight.next_bot += 1
        component.set(253, health)
        for old, barrier in tuple(self.active.items()):
            if barrier.owner is seed.owner:
                self.remove(old)
        self.active[entity] = Barrier(entity, seed.owner, seed.body, seed.team, seed.position,
                                      seed.yaw, until, health, component, script)
        if tick > due:
            component.run_frame(tick)

    @staticmethod
    def tail_active(barrier):
        wait = barrier.script.states.get(barrier.script.graph.node(30).state)
        return wait is not None and wait.active

    def enemies(self, shooter):
        return (b for b in self.active.values() if b.health > 0 and b.blocking() and b.owner is not shooter
                and (self.fight.match.game_map.free_for_all or b.team != shooter.team))

    def contacts(self, shooter, origin, direction, length):
        for barrier in self.enemies(shooter):
            distance = self.geometry.cast(barrier.position, barrier.yaw, origin, direction, length)
            if distance is not None:
                yield distance, barrier.owner, barrier

    def damage(self, barrier, amount):
        barrier.health = max(0.0, barrier.health - amount)
        barrier.component.set(253, barrier.health)
        if barrier.health <= 0:
            self.remove(barrier.entity)

    def remove(self, entity):
        barrier = self.active.pop(entity, None) or self.retiring.pop(entity, None)
        if barrier is not None:
            for instance in tuple(barrier.component.instances.values()):
                barrier.component.destroy(instance)
