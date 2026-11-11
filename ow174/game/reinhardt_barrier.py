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
from ow174.game.script import expr, nodes, runtime
from ow174.paths import DATA_DIR

HERO = 0x02E0000000000007
GRAPH = 0x0095


def native(world, config, instance):
    if instance.graph.index != GRAPH or world.match is None:
        return NotImplemented
    host = world.match.combat.reinhardt_barriers
    if host.geometry is None:
        return NotImplemented
    kind = config.get("$")
    if kind == "STU_295F1CDE":
        return 1.0  # Default barrier-health scalar.
    entity = config.get("m_entity")
    if kind == "STU_783B4CF0" and expr.dynamic(entity) == (expr.INSTANCE, 38):
        state = host.states.get(world.body)
        return host.geometry.maximum if state is None else state.current
    return NotImplemented

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
    entity: int = 0


def load_geometry(path: Path) -> Geometry:
    raw = path.read_bytes()
    data = json.loads(raw)
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
    return Geometry(triangles, *anchors, *values, hashlib.sha256(raw).hexdigest())


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


def overlaps(geometry: Geometry, feet, yaw, pitch, a, b, radius, owner_radius, owner_height,
             *, visible=None) -> bool:
    """A melee query capsule against the actual model's triangle mesh."""
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
                return True
    return False


class Barriers:
    """Host the authored 0095 raise state and reconstructed barrier pool."""

    def __init__(self, fight):
        self.fight = fight
        self.geometry = configured_geometry()
        self.states = {}

    @staticmethod
    def instance(owner):
        script = getattr(owner, "body_script", None)
        if script is None or script.retired or script.hero != HERO:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)

    @staticmethod
    def lower(instance):
        ability = instance.states.get(59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = instance.states.get(17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        instance.component.run_now()

    def retire(self, body):
        self.states.pop(body, None)

    def sync(self, owner):
        from ow174.game import sombra_hack

        geometry = self.geometry
        if geometry is None or not owner.has_body:
            return None
        instance = self.instance(owner)
        if instance is None:
            return None
        now = self.fight.now
        state = self.states.get(owner.body)
        unavailable = (owner.gone or not owner.spawned or owner.client is None
                       or self.fight.is_dead(owner))
        if state is None:
            if unavailable:
                return None
            maximum = geometry.maximum
            state = self.states[owner.body] = Barrier(maximum, maximum, now, now)
        if state.broken_until and now >= state.broken_until:
            ability = instance.states.get(59)
            if isinstance(ability, nodes.Ability) and ability.remaining() <= 0.:
                state.broken_until = 0.
                if ability.var is not None:
                    # The linked cooldown reaches zero without a base write.
                    # Wake 0095's ready gate after the barrier break deadline.
                    instance.changed(ability.var, ability.cur)
                    instance.component.run_now()
        pet = instance.states.get(17)
        raised = pet is not None and pet.active
        if raised and (unavailable or self.fight.is_stunned(owner) or sombra_hack.locked(owner)
                       or state.current <= 0 or now < state.broken_until):
            self.lower(instance)
            raised = False
        if not raised and state.active:
            state.regen_after = now + geometry.regen_delay
        since = max(state.last_update, state.regen_after)
        if not raised and not unavailable and now > since:
            state.current = expr.f32(min(state.maximum, state.current + geometry.regen_rate * (now - since)))
        if raised and not state.active:
            entity = self.fight.next_bot
            if not 0xC0001000 <= entity < 0xD0000000:
                raise ValueError("ability entity allocator exhausted")
            self.fight.next_bot += 1
            state.entity = entity
        state.active = raised and not unavailable
        if not state.active:
            state.entity = 0
        state.last_update = max(now, state.last_update)
        if not expr.same(instance.find_var(0, 586).value(), state.current):
            instance.write((0, 586), state.current)
        return state

    def update(self):
        if self.geometry is not None:
            for owner in self.fight.match.players:
                self.sync(owner)

    def enemies(self, shooter):
        if self.geometry is None:
            return
        match = self.fight.match
        for owner in match.players:
            if owner is shooter or (not match.game_map.free_for_all and owner.team == shooter.team):
                continue
            state = self.sync(owner)
            if state is not None and state.active:
                yield owner, state

    def intercept(self, shooter, origin, direction, length):
        hit = min(self.projectile_contacts(shooter, origin, direction, length), key=lambda hit: hit[0], default=None)
        return hit[:2] if hit is not None else None

    def projectile_contacts(self, shooter, origin, direction, reach):
        for owner, state in self.enemies(shooter):
            distance = self.contact(owner, origin, direction, reach)
            if distance is not None:
                yield distance, owner, state.entity

    @staticmethod
    def contact_pose(owner):
        mover = owner.mover
        return (tuple(mover.position), mover.yaw, mover.pitch,
                mover.values.stand_radius, mover.values.stand_height)

    def contact(self, owner, origin, direction, length):
        return self.contact_at_pose(self.contact_pose(owner), origin, direction, length)

    def contact_at_pose(self, pose, origin, direction, length):
        position, yaw, pitch, radius, height = pose
        return intersect(
            self.geometry, position, yaw, pitch, origin, direction, length,
            owner_radius=radius, owner_height=height,
        )

    def damage(self, owner, amount):
        state = self.states.get(owner.body)
        if amount <= 0 or state is None or not state.active:
            return
        geometry = self.geometry
        instance = self.instance(owner)
        state.current = expr.f32(max(0., state.current - amount))
        instance.write((0, 586), state.current)
        if state.current == 0:
            state.active = False
            state.entity = 0
            state.regen_after = self.fight.now + geometry.regen_delay
            state.broken_until = self.fight.now + geometry.break_delay
            self.lower(instance)
            instance.write((0, 61), geometry.break_delay)
            instance.component.run_now()

    def melee(self, shooter, eye, centre, radius, amount):
        from ow174.game.combat import capsule_visible

        def visible(point):
            return capsule_visible(self.fight._collision(), eye, point, point, 0.0,
                                   ice_walls=self.fight.ice_walls)

        for owner, _ in self.enemies(shooter):
            mover = owner.mover
            if overlaps(self.geometry, mover.position, mover.yaw, mover.pitch, centre, centre, radius,
                        mover.values.stand_radius, mover.values.stand_height, visible=visible):
                self.damage(owner, amount)

    def blast_distance(self, owner, origin, reach):
        from ow174.game.combat import capsule_visible

        mover = owner.mover
        position, axes = frame(self.geometry, mover.position, mover.yaw, mover.pitch,
                               mover.values.stand_radius, mover.values.stand_height)
        local = tuple(contacts.dot(contacts.sub(origin, position), axis) for axis in axes)
        nearest = None
        for face in self.geometry.triangles:
            point = contacts.closest_on_triangle(face[:3], face[3:6], face[6:], local)
            distance = math.dist(point, local)
            if distance > reach or (nearest is not None and distance >= nearest):
                continue
            witness = tuple(position[k] + sum(axes[n][k] * point[n] for n in range(3)) for k in range(3))
            if capsule_visible(self.fight._collision(), origin, witness, witness, 0.0,
                               ice_walls=self.fight.ice_walls):
                nearest = distance
        return nearest

    def explosion(self, shooter, origin, targets, reach, blast_amount):
        protected = set()
        for owner, _ in self.enemies(shooter):
            distance = self.blast_distance(owner, origin, reach)
            if distance is None:
                continue
            for target in targets:
                centre = target.feet[0], target.feet[1] + target.height / 2, target.feet[2]
                length = math.dist(origin, centre)
                if length <= 1e-9:
                    continue
                direction = tuple((a - b) / length for a, b in zip(centre, origin, strict=True))
                if self.contact(owner, origin, direction, length) is not None:
                    protected.add(target.entity)
            self.damage(owner, blast_amount(distance))
        return protected
