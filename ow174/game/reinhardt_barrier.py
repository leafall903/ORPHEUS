"""Reconstructed server barrier geometry/resources; native pet replication is separate.

Geometry comes from a prepared external104319 model descriptor. The raised pose
aligns the authored shield mesh to aim at the owner's bind-pose hardpoint29,
offset0.2 forward, subtracting shieldbone8C. This is a declared reconstruction,
not playback of the missing native skeletal animation/attachment service.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import contacts
from ow174.game.collision import MOVER, World
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


@dataclass
class Barrier:
    current: float
    maximum: float
    last_update: float
    regen_after: float
    broken_until: float = 0.0
    active: bool = False


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


def _configured_path() -> Path | None:
    selected = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
    path = Path(selected) if selected else DATA_DIR / "reinhardt_collision_174.json"
    return path if selected or path.is_file() else None


def configured_geometry() -> Geometry | None:
    path = _configured_path()
    return load_geometry(path) if path is not None else None


def geometry_sha256() -> str | None:
    path = _configured_path()
    return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None else None


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

HERO = 0x02E0000000000007


def sync(combat, player):
    geometry = combat.reinhardt_geometry
    script = player.body_script
    if geometry is None or script is None or player.hero.guid != HERO:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0095), None)
    if instance is None:
        return
    state = combat.reinhardt_barriers.get(player.body)
    if state is None:
        state = Barrier(geometry.maximum, geometry.maximum, combat.now, combat.now)
        combat.reinhardt_barriers[player.body] = state
    pet = instance.states.get(17)
    raised = (pet is not None and pet.active and not pet.ending and not combat.is_dead(player)
              and not combat.is_stunned(player) and not player.gone)
    if not raised and state.active:
        state.regen_after = combat.now + geometry.regen_delay
    if not raised:
        elapsed = max(0, combat.now - max(state.last_update, state.regen_after))
        state.current = min(state.maximum, state.current + elapsed * geometry.regen_rate)
    state.active = raised and state.current > 0 and combat.now >= state.broken_until
    state.last_update = combat.now
    if raised and not state.active:
        from ow174.game.script import runtime

        ability = instance.states.get(59)
        if ability is not None:
            ability.stop()
        pet.request(runtime.ABORT)
        instance.component.run_now()
    from ow174.game.script import expr

    health = expr.f32(state.current)
    if instance.find_var(0, 586).value() != health:
        instance.write((0, 586), health)


def intersection(combat, shooter, origin, direction, length, *, radius=0.0):
    geometry = combat.reinhardt_geometry
    if geometry is None or not combat.reinhardt_barriers:
        return None
    nearest = None
    for target in combat._enemies(shooter):
        state = combat.reinhardt_barriers.get(target.entity)
        if state is None or not state.active or combat.is_stunned(target.owner):
            continue
        mover = target.owner.mover
        hit = intersect(geometry, target.feet, mover.yaw, mover.pitch, origin, direction, length,
                        radius=radius, owner_radius=target.radius, owner_height=target.height)
        if hit is not None and (nearest is None or hit < nearest[0]):
            nearest = hit, target.owner
    return nearest


def damage(combat, player, amount):
    state = combat.reinhardt_barriers[player.body]
    geometry = combat.reinhardt_geometry
    state.current = max(0, state.current - amount)
    state.regen_after = combat.now + geometry.regen_delay
    if state.current == 0:
        state.broken_until = combat.now + geometry.break_delay
    sync(combat, player)


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


FIRST = 0xA0180000


@dataclass
class RaisedBarrier:
    entity: int
    owner: object
    body: int
    instance: object
    geometry: Geometry
    state: Barrier

    @property
    def active(self):
        return self.state.active

    @property
    def health(self):
        return self.state.current

    @health.setter
    def health(self, value):
        self.state.current = value

    @property
    def broken_until(self):
        return self.state.broken_until

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
        self.geometry = fight.reinhardt_geometry
        self.actors = {}
        self.next_id = 0

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
            self.actors.pop(owner.body, None)
            return
        sync(self.fight, owner)
        state = self.fight.reinhardt_barriers[owner.body]
        actor = self.actors.get(owner.body)
        if actor is None or actor.instance is not instance or actor.state is not state:
            self.actors[owner.body] = RaisedBarrier(
                FIRST + self.next_id, owner, owner.body, instance, self.geometry, state,
            )
            self.next_id += 1

    def enemies(self, shooter):
        for owner in self.fight.match.players:
            self.sync(owner)
        return [actor for actor in self.actors.values() if actor.active and actor.owner is not shooter
                and (self.fight.match.game_map.free_for_all or actor.owner.team != shooter.team)]

    def damage(self, actor, amount):
        if not actor.active or amount <= 0:
            return 0.0
        before = actor.health
        damage(self.fight, actor.owner, amount)
        if actor.health == 0:
            actor.instance.write((0, 61), actor.geometry.break_delay)
            actor.instance.component.run_now()
        return before - actor.health

    def explosion(self, shooter, point, blast, visible_from, forced):
        from ow174.game.combat import capsule_visible

        radius = max((ring[0] for ring in blast.rings), default=0.0)
        actors = {actor.entity: actor for actor in (*self.enemies(shooter), *forced)}
        protected = set()
        for actor in actors.values():
            for target in self.fight._enemies(shooter, damageable=True):
                a, b = target.segment()
                centre = tuple((x+y)*.5 for x,y in zip(a,b,strict=True))
                delta = tuple(x-y for x,y in zip(centre,visible_from,strict=True))
                length = math.sqrt(sum(v*v for v in delta))
                if length and actor.trace(visible_from, tuple(v/length for v in delta), length) is not None:
                    protected.add(target.entity)
            collision = self.fight._collision()
            def visible(surface, collision=collision):
                return capsule_visible(collision, visible_from, surface, surface, .001)
            distance = 0.0 if actor in forced else min(actor.surface_distances(point, radius, visible),
                                                      default=None)
            if distance is not None:
                self.damage(actor, blast.amount * blast.splash(distance))
        return protected
