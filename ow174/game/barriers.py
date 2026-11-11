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


@dataclass
class Barrier:
    current: float
    maximum: float
    last_update: float
    regen_after: float
    broken_until: float = 0.0
    active: bool = False


class HandCannonBarriers:
    """Scoped Hand Cannon host, not a Pet actor or general receiver service.

    Shared geometry/resource candidate reviewed from lane0007. Only Doomfist's
    known pellet definition participates here. Other weapon and ability/barrier
    policies require their own evidence; no generic barrier immunity is added.
    """

    def __init__(self, combat):
        self.combat = combat
        self.geometry = configured_geometry()
        self.states: dict[int, Barrier] = {}

    @staticmethod
    def instance(player):
        script = player.body_script
        if script is None or script.hero != 0x02E0000000000007 or script.component.strict:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x95), None)

    @staticmethod
    def lower(instance):
        ability = instance.states.get(59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = instance.states.get(17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        instance.component.run_now()

    def sync(self, player):
        fight, geometry = self.combat, self.geometry
        if geometry is None or not player.has_body:
            return None
        instance = self.instance(player)
        if instance is None:
            return None
        state = self.states.get(player.body)
        if state is None:
            state = Barrier(geometry.maximum, geometry.maximum, fight.now, fight.now)
            self.states[player.body] = state
        pet = instance.states.get(17)
        unusable = (fight.is_dead(player) or fight.is_stunned(player)
                    or fight.punches.controlled(player.body) or player.gone
                    or not player.spawned or player.client is None)
        if unusable and pet is not None and pet.active:
            self.lower(instance)
        raised = not unusable and pet is not None and pet.active
        if not raised and state.active:
            state.regen_after = fight.now + geometry.regen_delay
        if not raised and not fight.is_dead(player) and fight.now > max(state.last_update, state.regen_after):
            duration = fight.now - max(state.last_update, state.regen_after)
            state.current = expr.f32(min(state.maximum, state.current + geometry.regen_rate * duration))
        if raised and (state.current <= 0 or fight.now < state.broken_until):
            self.lower(instance)
            raised = False
        state.active = raised and state.current > 0 and fight.now >= state.broken_until
        state.last_update = max(fight.now, state.last_update)
        current = instance.find_var(0, 586)
        if not expr.same(current.value(), expr.f32(state.current)):
            instance.write((0, 586), expr.f32(state.current))
        return state

    def update(self):
        live = set()
        for player in self.combat.match.players:
            if player.has_body:
                live.add(player.body)
                self.sync(player)
        for body in self.states.keys() - live:
            del self.states[body]

    def contact(self, rocket, length):
        if (self.geometry is None or rocket.direct is None
                or (rocket.direct.loadout != 0x0B90000000000109 and not rocket.npc_direct_projectile)):
            return None
        return self.ray_contact(rocket.shooter, rocket.team, rocket.position, rocket.direction, length)

    def ray_contact(self, shooter, team, origin, direction, length):
        if self.geometry is None:
            return None
        fight = self.combat
        found = []
        for owner in fight.match.players:
            if (owner is shooter or not owner.has_body
                    or (not fight.match.game_map.free_for_all and owner.team == team)):
                continue
            state = self.sync(owner)
            if state is None or not state.active:
                continue
            distance = intersect(self.geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                                 origin, direction, length,
                                 owner_radius=owner.mover.values.stand_radius,
                                 owner_height=owner.mover.values.stand_height)
            if distance is not None:
                found.append((distance, owner, state))
        return min(found, key=lambda row: (row[0], row[1].body)) if found else None

    def damage(self, contact, amount):
        _, owner, state = contact
        if amount <= 0 or not state.active:
            return
        state.current = expr.f32(max(0.0, state.current - amount))
        instance = self.instance(owner)
        if instance is None:
            return
        instance.write((0, 586), state.current)
        if state.current <= 0:
            state.broken_until = self.combat.now + self.geometry.break_delay
            state.regen_after = self.combat.now + self.geometry.regen_delay
            state.active = False
            self.lower(instance)
            instance.write((0, 61), self.geometry.break_delay)
            instance.component.run_now()


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
