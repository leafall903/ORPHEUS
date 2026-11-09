"""Orisa 0BD7 flight and 0BD8 mesh deployment, adapted from lane 013E."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.collision import World
from ow174.game.contacts import closest_on_triangle
from ow174.game.script import expr, graph, runtime
from ow174.paths import DATA_DIR

TICK = 0.016
GRAPH = 0x0BD7
DEFINITION = 0x04000000000014A1
DEPLOY_DELAY = 0.1  # 0252
HEALTH = 600.0  # 0BD8
LIFETIME = 20.0  # 0BD8


class BarrierWorld(runtime.World):
    def __init__(self, fight, entity, position):
        super().__init__(mode=fight.match.game_map.mode_guid)
        self.fight, self.entity, self.location = fight, entity, position

    def position(self, entity):
        if entity == self.entity:
            return expr.Vec3(*self.location)
        return self.fight.match.script_body_position(entity, self.entity)


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
        cosine, sine = math.cos(yaw), math.sin(yaw)
        x, y, z = (a - b for a, b in zip(point, position, strict=True))
        local = (cosine * x - sine * z, y, sine * x + cosine * z)
        samples = (closest_on_triangle(a, b, c, local) for a, b, c in self.triangles)
        x, y, z = min(samples, key=lambda p: math.dist(p, local))
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

    def blocking(self):
        if self.script is None:
            return True
        pool = self.script.states.get(self.script.graph.node(0).state)
        return pool is not None and pool.active


@dataclass
class Flight:
    shooter: object
    source_body: int
    team: int
    position: tuple
    velocity: tuple
    launch_direction: tuple
    gravity: float
    left: float
    last_tick: int | None = None


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        path = Path(os.environ.get("ORPHEUS_ORISA_GEOMETRY", DATA_DIR / "orisa_geometry_174.json"))
        self.geometry = Geometry(path) if path.is_file() else None
        if self.geometry is not None and graph.graph(0xBD8) is None:
            raise ValueError("barrier geometry requires original0BD8 graph")
        self.active: dict[int, Barrier] = {}
        self.retiring: dict[int, Barrier] = {}
        self.pending = []
        self.flights: list[Flight] = []

    def launch(self, shooter, state, origin, direction):
        if self.geometry is None:
            return
        node = state.node
        spawn = (node.field("m_49CBA3BC") or {}).get("m_entityDefinition")
        asset = state.evaluate(spawn, watch=False)
        if not isinstance(asset, expr.Asset) or asset.guid != DEFINITION:
            return
        speed = expr.to_float(state.evaluate(node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(node.field("m_projectileLifetime"), watch=False))
        motion = next((m for m in node.field("m_projectileMotions") or ()
                       if m.get("$") == "STU_E1A5B579"), None)
        if motion is None:
            return
        gravity = expr.to_float(state.evaluate(motion.get("m_599AB8D7"), watch=False))
        if not all(math.isfinite(v) and v > 0 for v in (speed, lifetime, gravity)):
            return
        velocity = tuple(v * speed for v in direction)
        self.flights.append(Flight(shooter, shooter.body, shooter.team, origin, velocity, direction,
                                   gravity, lifetime))

    def retire(self, body):
        self.flights[:] = [flight for flight in self.flights if flight.source_body != body]
        self.pending[:] = [(due, flight) for due, flight in self.pending if flight.source_body != body]

    def _advance(self, flight, tick):
        start = tick if flight.last_tick is None else flight.last_tick + 1
        flight.last_tick = tick
        for at in range(start, tick + 1):
            duration = min(TICK, flight.left)
            if duration <= 0:
                break
            dx, dy, dz = (v * duration for v in flight.velocity)
            delta = (dx, dy - 0.5 * flight.gravity * duration ** 2, dz)
            length = math.hypot(*delta)
            if length > 0:
                direction = tuple(v / length for v in delta)
                hit = self.fight._collision().cast_ray(flight.position, direction, length)
                if hit is not None:
                    flight.position = tuple(hit.point)
                    self.pending.append((at + math.ceil(DEPLOY_DELAY / TICK), flight))
                    self.flights.remove(flight)
                    return
                flight.position = tuple(a + b for a, b in zip(flight.position, delta, strict=True))
            vx, vy, vz = flight.velocity
            flight.velocity = (vx, vy - flight.gravity * duration, vz)
            flight.left -= duration
        if flight.left <= 0:
            self.flights.remove(flight)

    def update(self, tick):
        for flight in tuple(self.flights):
            self._advance(flight, tick)
        for entity, barrier in tuple(self.retiring.items()):
            barrier.component.run_frame(tick)
            if not self._tail_active(barrier):
                self.remove(entity)
        for entity, barrier in tuple(self.active.items()):
            if barrier.health <= 0 or (barrier.component is None and tick >= barrier.until):
                self.remove(entity)
            elif barrier.component is not None:
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
            script.start_entries((), rocket.source_body)
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
            direction = rocket.launch_direction
            self.active[entity] = Barrier(entity, rocket.shooter, rocket.source_body,
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

    def opponents(self, shooter):
        return tuple(b for b in self.active.values()
                     if b.health > 0 and b.owner is not shooter
                     and (b.blocking() if isinstance(b, Barrier) and b.script is not None
                          else self.fight.tick < b.until)
                     and (self.fight.match.game_map.free_for_all or b.team != shooter.team))

    def trace(self, shooter, origin, direction, length, *, barriers=None):
        if self.geometry is None:
            return None  # unavailable geometry must not silently turn into a guessed plane
        best = None
        # An explosion keeps the pre-impact set: a barrier broken by that same
        # projectile still occludes that event, rather than leaking splash behind it.
        for barrier in self.opponents(shooter) if barriers is None else barriers:
            distance = self.geometry.cast(barrier.position, barrier.yaw, origin, direction, length)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, barrier
        return best

    def splash(self, shooter, point, origin, blast, *, struck=None):
        if self.geometry is None:
            return
        from ow174.game.combat import EPSILON, ray_world

        impacts = []
        for barrier in self.opponents(shooter):
            if barrier is struck:
                continue
            sample = self.geometry.closest(barrier.position, barrier.yaw, point)
            scalar = blast.splash(math.dist(point, sample))
            if scalar <= 0:
                continue
            delta = tuple(x - y for x, y in zip(sample, origin, strict=True))
            distance = math.hypot(*delta)
            if distance > EPSILON:
                reach = max(0.0, distance - 0.001)
                direction = tuple(axis / distance for axis in delta)
                if ray_world(self.fight._collision(), origin, direction, reach) is not None:
                    continue
                obstruction = self.fight._barrier_contact(shooter, origin, direction, reach)
                if obstruction is not None and obstruction[1] is not barrier:
                    continue
            impacts.append((barrier, blast.amount * scalar))
        for barrier, amount in impacts:
            self.damage(barrier, amount)

    def damage(self, barrier, amount):
        barrier.health -= min(barrier.health, max(0.0, amount))
        barrier.component.set(253, barrier.health)
        if barrier.health <= 0:
            self.remove(barrier.entity)

    def remove(self, entity):
        barrier = self.active.pop(entity, None) or self.retiring.pop(entity, None)
        if barrier is not None and barrier.component is not None:
            for instance in tuple(barrier.component.instances.values()):
                barrier.component.destroy(instance)
