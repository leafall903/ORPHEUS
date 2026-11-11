"""Reconstructed server barrier geometry/resources; native pet replication is separate.

Geometry comes from a prepared external104319 model descriptor. The raised pose
aligns the authored shield mesh to aim at the owner's bind-pose hardpoint29,
offset0.2 forward, subtracting shieldbone8C. This is a declared reconstruction,
not playback of the missing native skeletal animation/attachment service.
"""

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import contacts
from ow174.game.collision import MOVER, World
from ow174.game.script import expr, nodes, runtime
from ow174.paths import DATA_DIR


@dataclass(frozen=True)
class Geometry:
    triangles: tuple
    owner_anchor: tuple
    child_anchor: tuple
    forward_offset: float
    maximum: float
    regen_rate: float
    regen_delay: float
    break_delay: float
    fingerprint: str


def load_geometry(path: Path) -> Geometry:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "orpheus-reinhardt-barrier-v1" or data.get("build") != 104319:
        raise ValueError("unsupported Reinhardt barrier descriptor")
    triangles = tuple(tuple(t) for t in data["triangles"])
    if not triangles or any(len(t) != 9 or any(type(v) not in (int, float) or not math.isfinite(v)
                                              for v in t) for t in triangles):
        raise ValueError("invalid barrier triangles")
    anchors = tuple(tuple(data[key]) for key in ("owner_anchor", "child_anchor"))
    if any(len(v) != 3 or any(type(k) not in (int, float) or not math.isfinite(k) for k in v)
           for v in anchors):
        raise ValueError("invalid barrier anchors")
    names = ("forward_offset", "maximum", "regen_rate", "regen_delay", "break_delay")
    values = tuple(data[key] for key in names)
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values) or values[1] <= 0:
        raise ValueError("invalid barrier resource policy")
    fingerprint = data["fingerprint"]
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("barrier descriptor requires source fingerprint")
    return Geometry(triangles, *anchors, *values, fingerprint)


def configured_geometry() -> Geometry | None:
    selected = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
    path = Path(selected) if selected else DATA_DIR / "reinhardt_collision_174.json"
    if selected or path.is_file():
        return load_geometry(path)
    return None


@lru_cache(maxsize=4)
def mesh(geometry: Geometry) -> World:
    """A deterministic query cache; all input geometry is in the stored descriptor."""
    return World(geometry.triangles, name="Reinhardt barrier104319")


def frame(geometry: Geometry, feet, yaw: int, pitch: int, owner_radius=0.0, owner_height=0.0):
    y, p = yaw * math.tau / 65536, pitch * math.tau / 65536
    right = (math.cos(y), 0.0, -math.sin(y))
    forward = (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))
    up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2], -forward[1] * right[0])
    anchor = geometry.owner_anchor
    origin = tuple(feet[k] + right[k] * anchor[0] + (anchor[1] if k == 1 else 0.0)
                   + (math.sin(y) if k == 0 else math.cos(y) if k == 2 else 0.0) * anchor[2]
                   + forward[k] * geometry.forward_offset
                   - sum(axis[k] * geometry.child_anchor[n] for n, axis in enumerate((right, up, forward)))
                   for k in range(3))
    if owner_radius > 0:
        # Reconstruct the raised pose beyond the native owner capsule. A bind-pose
        # hardpoint alone leaves the shield behind its wearer; the model's rear
        # extent and authored0.2m offset determine the required clearance.
        support = max(forward[1] * owner_radius, forward[1] * (owner_height - owner_radius)) + owner_radius
        rear = min(t[k] for t in geometry.triangles for k in (2, 5, 8))
        current = sum((origin[k] - feet[k]) * forward[k] for k in range(3))
        shift = max(0.0, support - rear + geometry.forward_offset - current)
        origin = tuple(origin[k] + forward[k] * shift for k in range(3))
    return origin, (right, up, forward)


def intersect(geometry: Geometry, feet, yaw, pitch, origin, direction, length, radius=0.0,
              owner_radius=0.0, owner_height=0.0) -> float | None:
    position, axes = frame(geometry, feet, yaw, pitch, owner_radius, owner_height)
    local = tuple(sum((origin[k] - position[k]) * axis[k] for k in range(3)) for axis in axes)
    toward = tuple(sum(direction[k] * axis[k] for k in range(3)) for axis in axes)
    world = mesh(geometry)
    if radius <= 0.0:
        hit = world.cast_ray(local, toward, length, MOVER)
        return hit.distance if hit is not None else None
    end = tuple(local[k] + toward[k] * length for k in range(3))
    hit = contacts.scene_sweep(world.engine, local, end, ((0.0, 0.0, 0.0),), radius, MOVER)
    return hit[0] * length if hit is not None else None


def surface_distances(geometry: Geometry, feet, yaw, pitch, a, b, radius, owner_radius, owner_height,
                      *, visible=None):
    """Yield visible mesh contact distances within the query capsule's radius."""
    position, axes = frame(geometry, feet, yaw, pitch, owner_radius, owner_height)
    points = tuple(tuple(sum((p[k] - position[k]) * axis[k] for k in range(3)) for axis in axes)
                   for p in (a, b))
    low = tuple(min(points[0][k], points[1][k]) - radius for k in range(3))
    high = tuple(max(points[0][k], points[1][k]) + radius for k in range(3))
    for triangle in geometry.triangles:
        corners = tuple(tuple(triangle[n:n + 3]) for n in (0, 3, 6))
        if any(max(v[k] for v in corners) < low[k] or min(v[k] for v in corners) > high[k]
               for k in range(3)):
            continue
        distance = contacts.gjk(corners, points, reach=radius)
        if distance.distance <= radius:
            point = tuple(position[k] + sum(axes[n][k] * distance.point_a[n] for n in range(3))
                          for k in range(3))
            if visible is None or visible(point):
                yield distance.distance


HERO = 0x02E0000000000007
FIRST = 0xA0180000


@dataclass
class RaisedBarrier:
    entity: int
    owner: object
    body: int
    instance: object
    geometry: Geometry
    health: float
    updated: float
    regen_after: float
    broken_until: float = 0.0
    active: bool = False

    @property
    def maximum(self):
        return self.geometry.maximum

    def pose(self):
        mover = self.owner.mover
        data = mover.data
        height = data.crouch_height if mover.crouched else data.stand_height
        return mover.position, mover.yaw, mover.pitch, data.stand_radius, height

    def centre(self):
        return frame(self.geometry, *self.pose())[0]

    def trace(self, origin, direction, length, radius=0.0):
        feet, yaw, pitch, owner_radius, height = self.pose()
        return intersect(self.geometry, feet, yaw, pitch, origin, direction, length,
                         radius, owner_radius, height)

    def near(self, point, radius, visible):
        return next(self.surface_distances(point, radius, visible), None) is not None

    def surface_distances(self, point, radius, visible):
        feet, yaw, pitch, owner_radius, height = self.pose()
        return surface_distances(self.geometry, feet, yaw, pitch, point, point, radius, owner_radius,
                                 height, visible=visible)


class ReinhardtBarriers:
    def __init__(self, fight):
        self.fight = fight
        self.geometry = configured_geometry()
        self.actors = {}
        self.next_id = 0

    @staticmethod
    def lower(instance):
        ability = instance.states.get(59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = instance.states.get(17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        instance.component.run_now()

    def sync(self, owner):
        if self.geometry is None or getattr(getattr(owner, "hero", None), "guid", None) != HERO:
            return
        script = owner.body_script
        if (script is None or script.retired or script.component.strict or not owner.has_body
                or not owner.spawned or owner.gone or owner.client is None):
            self.actors.pop(owner.body, None)
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x95), None)
        if instance is None:
            return
        geometry = self.geometry
        now = self.fight.now
        actor = self.actors.get(owner.body)
        if actor is None or actor.instance is not instance:
            actor = self.actors[owner.body] = RaisedBarrier(
                FIRST + self.next_id, owner, owner.body, instance, geometry, geometry.maximum, now, now)
            self.next_id += 1
        pet = instance.states.get(17)
        raised = pet is not None and pet.active
        if raised and (self.fight.is_dead(owner) or self.fight.is_stunned(owner)
                       or actor.health <= 0 or now < actor.broken_until):
            self.lower(instance)
            raised = False
        if not raised:
            if actor.active:
                actor.regen_after = now + geometry.regen_delay
            elapsed = max(0.0, now - max(actor.updated, actor.regen_after))
            actor.health = expr.f32(min(geometry.maximum, actor.health + geometry.regen_rate * elapsed))
        actor.active = raised
        actor.updated = max(now, actor.updated)
        current = instance.find_var(expr.INSTANCE, 586)
        if not expr.same(current.value(), actor.health):
            instance.write((expr.INSTANCE, 586), actor.health)

    def enemies(self, shooter):
        for owner in self.fight.match.players:
            self.sync(owner)
        return [actor for actor in self.actors.values() if actor.active and actor.owner is not shooter
                and (self.fight.match.game_map.free_for_all or actor.owner.team != shooter.team)]

    def damage(self, actor, amount):
        if not actor.active or amount <= 0:
            return 0.0
        before = actor.health
        actor.health = expr.f32(max(0.0, before - amount))
        actor.instance.write((expr.INSTANCE, 586), actor.health)
        if actor.health == 0:
            now = self.fight.now
            actor.active = False
            actor.broken_until = now + actor.geometry.break_delay
            actor.regen_after = now + actor.geometry.regen_delay
            self.lower(actor.instance)
            actor.instance.write((expr.INSTANCE, 61), actor.geometry.break_delay)
            actor.instance.component.run_now()
        return before - actor.health

    def protected_bodies(self, actor, shooter, origin):
        from ow174.game.combat import ray_world

        protected = set()
        collision = self.fight._collision()
        for target in self.fight._enemies(shooter):
            a, b = target.segment()
            centre = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
            delta = tuple(x - y for x, y in zip(centre, origin, strict=True))
            length = math.hypot(*delta)
            if not length:
                continue
            direction = tuple(axis / length for axis in delta)
            hit = actor.trace(origin, direction, length)
            if hit is not None and ray_world(collision, origin, direction, hit) is None:
                protected.add(target.entity)
        return protected

    def explosion(self, shooter, point, blast, visible_from, forced):
        from ow174.game.combat import capsule_visible

        radius = max((ring[0] for ring in blast.rings), default=0.0)
        actors = {actor.entity: actor for actor in (*self.enemies(shooter), *forced)}
        protected = set()
        for actor in actors.values():
            protected.update(self.protected_bodies(actor, shooter, visible_from))
            collision = self.fight._collision()
            def visible(surface, collision=collision):
                return capsule_visible(collision, visible_from, surface, surface, .001)
            distance = 0.0 if actor in forced else min(actor.surface_distances(point, radius, visible),
                                                      default=None)
            if distance is not None:
                self.damage(actor, blast.amount * blast.splash(distance))
        return protected
