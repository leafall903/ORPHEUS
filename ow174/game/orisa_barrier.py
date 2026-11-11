"""Protective Barrier placement and model collision from build 104319."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import contacts
from ow174.game.collision import World
from ow174.game.script import expr, graph, runtime
from ow174.paths import DATA_DIR

TICK = 0.016
HERO = 0x02E000000000013E
GRAPH = 0x0BD7
DEFINITION = 0x04000000000014A1
DEPLOY_DELAY = 0.1  # 0252
HEALTH = 600.0  # 0BD8
LIFETIME = 20.0  # 0BD8
MESH_SHA256 = "b5efc5527090c96054db9851a001e6386d1f061aea4fd4982eb70351d3d6e216"
MODEL_SHA256 = "92c08849cb4348870aad667c74231e8c475cd05bf438c443860d2e8b9f55b28e"


def geometry_path():
    override = os.environ.get("ORPHEUS_ORISA_GEOMETRY")
    path = Path(override) if override else DATA_DIR / "orisa_geometry_174.json"
    if override and not path.is_file():
        raise FileNotFoundError(path)
    return path


def geometry_sha256():
    path = geometry_path()
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class BarrierWorld(runtime.World):
    def __init__(self, fight, entity, position):
        super().__init__(mode=fight.match.game_map.mode_guid)
        self.match, self.entity, self.location = fight.match, entity, position

    def position(self, entity):
        if entity == self.entity:
            return expr.Vec3(*self.location)
        return self.match.script_body_position(entity, self.entity)


class Geometry:
    def __init__(self, path):
        self.load(json.loads(Path(path).read_text(encoding="utf-8")), model="0x0D00000000004062")

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
        encoded = json.dumps({"triangles": triangles, "masks": masks}, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
        if model == "0x0D00000000004062" and (
                data.get("raw_sha256") != MODEL_SHA256 or hashlib.sha256(encoded).hexdigest() != MESH_SHA256
        ):
            raise ValueError("Orisa barrier geometry does not match the verified104319 mesh")
        # Use the existing fingerprinted static-world asset type; queries still
        # use this model-local triangle mesh, with no character mover attached.
        self.world = World(triangles, masks)
        self.triangles = tuple(tuple(tuple(t[k:k + 3]) for k in (0, 3, 6)) for t in triangles)
        self.source = data["raw_sha256"]

    def cast(self, position, yaw, origin, direction, length, radius=0.0):
        cosine, sine = math.cos(yaw), math.sin(yaw)

        def local(vector):
            x, y, z = vector
            return cosine * x - sine * z, y, sine * x + cosine * z

        delta = tuple(a - b for a, b in zip(origin, position, strict=True))
        start, toward = local(delta), local(direction)
        if radius:
            end = tuple(a + b * length for a, b in zip(start, toward, strict=True))
            hit = contacts.scene_sweep(self.world.engine, start, end, ((0.0, 0.0, 0.0),), radius, 0xFFFF)
            return hit[0] * length if hit is not None else None
        hit = self.world.cast_ray(start, toward, length, category=0xFFFF)
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
    health: float = HEALTH
    component: runtime.Component | None = None
    script: runtime.Instance | None = None

    @property
    def body(self):
        return self.entity

    def blocking(self):
        if self.script is None:
            return True
        pool = self.script.states.get(self.script.graph.node(0).state)
        return pool is not None and pool.active


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        path = geometry_path()
        self.geometry = Geometry(path) if path.is_file() else None
        if self.geometry is not None and graph.graph(0xBD8) is None:
            raise ValueError("barrier geometry requires original0BD8 graph")
        self.active: dict[int, Barrier] = {}
        self.retiring: dict[int, Barrier] = {}
        self.pending = []

    def landed(self, rocket, tick=None):
        if tick is None:
            tick = self.fight.tick
        if (rocket.deploy_body is not None and not rocket.shooter.gone
                and rocket.shooter.body == rocket.deploy_body):
            due = tick + math.ceil(DEPLOY_DELAY / TICK)
            self.pending.append((due, rocket))

    def retire(self, body):
        self.pending[:] = [(due, rocket) for due, rocket in self.pending
                           if rocket.deploy_body != body]

    def update(self, tick):
        for entity, barrier in tuple(self.retiring.items()):
            if tick > barrier.component.frame:
                barrier.component.run_frame(tick)
            if not self._tail_active(barrier):
                self.remove(entity)
        for entity, barrier in tuple(self.active.items()):
            if barrier.health <= 0 or (barrier.component is None and tick >= barrier.until):
                self.remove(entity)
            elif barrier.component is not None and tick > barrier.component.frame:
                barrier.component.run_frame(tick)
                if not barrier.blocking():
                    if self._tail_active(barrier):
                        del self.active[entity]
                        self.retiring[entity] = barrier
                    else:
                        self.remove(entity)
        for due, rocket in tuple(self.pending):
            if tick < due:
                continue
            self.pending.remove((due, rocket))
            entity = self.fight.next_bot
            if not 0xC0001000 <= entity < 0xD0000000:
                raise ValueError("ability entity allocator exhausted")
            component = runtime.Component(entity, world=BarrierWorld(self.fight, entity, rocket.position))
            component.now, component.frame = due * 16, due
            script = component.create(graph.graph(0xBD8))
            script.start_entries((), rocket.deploy_body)
            component.run_now()  # original start entry owns600 health and20-second initial value
            health = expr.to_float(script.find_var(graph.INSTANCE, 587).value())
            seconds = expr.to_float(component.entity_var(567).value())
            if health <= 0 or seconds <= 0:
                raise ValueError("original0BD8 initialization lacks health/lifetime")
            until = due + math.ceil(seconds / TICK)
            if tick >= until:
                component.destroy(script)
                continue  # do not refresh an expired deployment after a world gap
            self.fight.next_bot += 1
            component.set(253, health)  # authoritative aggregate consumed by original HUD mappings
            for old, barrier in tuple(self.active.items()):
                if barrier.owner is rocket.shooter:
                    self.remove(old)
            direction = rocket.direction
            self.active[entity] = Barrier(entity, rocket.shooter, rocket.deploy_body,
                                          rocket.team, rocket.position,
                                          math.atan2(direction[0], direction[2]), until,
                                          health, component, script)
            if tick > due:
                component.run_frame(tick)

    @staticmethod
    def _tail_active(barrier):
        # Original0BD8 starts Wait30 when ChaseVar29's health-pool scope ends.
        # Keep its authored phase, without assigning general native semantics
        # to the523EE38D output or to unrelated entity destruction.
        wait = barrier.script.states.get(barrier.script.graph.node(30).state)
        return wait is not None and wait.active

    def opponents(self, shooter, team=None):
        if team is None:
            team = shooter.team
        return tuple(b for b in self.active.values()
                     if b.health > 0 and b.owner is not shooter
                     and (b.blocking() if isinstance(b, Barrier) and b.script is not None else self.fight.tick < b.until)
                     and (self.fight.match.game_map.free_for_all or b.team != team))

    def intersection(self, shooter, origin, direction, length, *, radius=0.0):
        return self.trace(shooter, origin, direction, length, radius=radius)

    def trace(self, shooter, origin, direction, length, *, radius=0.0, barriers=None):
        if self.geometry is None:
            return None
        nearest = None
        for barrier in self.opponents(shooter) if barriers is None else barriers:
            distance = self.geometry.cast(barrier.position, barrier.yaw, origin, direction, length, radius)
            if distance is not None and (nearest is None or distance < nearest[0]):
                nearest = distance, barrier
        return nearest

    def damage(self, barrier, amount, distance=None, *, multiplier=1.0):
        if distance is not None:
            amount = amount.amount * amount.scale(distance) * multiplier
        taken = min(barrier.health, max(0.0, amount))
        barrier.health -= taken
        if barrier.component is not None:
            barrier.component.set(253, barrier.health)
        if barrier.health <= 0:
            self.remove(barrier.entity)
        return taken

    def splash(self, barrier, definition, distance, *, multiplier=1.0):
        return self.damage(barrier, definition.amount * definition.splash(distance) * multiplier)

    def remove(self, entity):
        barrier = self.active.pop(entity, None) or self.retiring.pop(entity, None)
        if barrier is not None and barrier.component is not None:
            for instance in tuple(barrier.component.instances.values()):
                barrier.component.destroy(instance)
