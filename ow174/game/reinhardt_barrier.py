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


def geometry_path() -> Path | None:
    selected = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
    path = Path(selected) if selected else DATA_DIR / "reinhardt_collision_174.json"
    return path if selected or path.is_file() else None


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


def intersect(geometry: Geometry, feet, yaw, pitch, origin, direction, length, *,
              owner_radius, owner_height, radius=0.0) -> float | None:
    position, axes = frame(geometry, feet, yaw, pitch, owner_radius, owner_height)
    local = tuple(sum((origin[k] - position[k]) * axis[k] for k in range(3)) for axis in axes)
    toward = tuple(sum(direction[k] * axis[k] for k in range(3)) for axis in axes)
    world = mesh(geometry)
    if radius <= 0:
        hit = world.cast_ray(local, toward, length, MOVER)
        return hit.distance if hit is not None else None
    end = tuple(a + b * length for a, b in zip(local, toward, strict=True))
    hit = contacts.scene_sweep(world.engine, local, end, ((0.0, 0.0, 0.0),), radius, MOVER)
    return hit[0] * length if hit is not None else None

class Barriers:
    """Keep 0095's authored raise state and server-side shield health together."""

    def __init__(self, combat):
        self.combat = combat
        path = geometry_path()
        self.geometry = load_geometry(path) if path is not None else None
        self.states: dict[int, Barrier] = {}

    @staticmethod
    def instance(player):
        script = player.body_script
        if script is None or script.hero != 0x02E0000000000007 or script.retired:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x0095), None)

    def retire(self, entity):
        self.states.pop(entity, None)

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
        if self.geometry is None or not player.has_body:
            return None
        instance = self.instance(player)
        if instance is None or instance.component.fault is not None:
            return None
        now = self.combat.now
        geometry = self.geometry
        state = self.states.get(player.body)
        if state is None:
            state = Barrier(geometry.maximum, geometry.maximum, now, now)
            self.states[player.body] = state
        pet = instance.states.get(17)
        raised = pet is not None and pet.active
        if player.gone or not player.spawned or player.client is None or self.combat.is_dead(player):
            if raised:
                self.lower(instance)
            state.active, state.last_update = False, now
            return state
        if raised and (self.combat.is_stunned(player) or state.current <= 0 or now < state.broken_until):
            self.lower(instance)
            raised = False
        if not raised:
            if state.active:
                state.regen_after = now + geometry.regen_delay
            elapsed = max(0.0, now - max(state.last_update, state.regen_after))
            state.current = expr.f32(min(state.maximum, state.current + geometry.regen_rate * elapsed))
        state.active = raised
        state.last_update = max(now, state.last_update)
        current = instance.find_var(0, 586)
        if not expr.same(current.value(), state.current):
            instance.write((0, 586), state.current)
        return state

    def update(self):
        if self.geometry is not None:
            for player in self.combat.match.players:
                self.sync(player)

    def enemies(self, shooter):
        free_for_all = self.combat.match.game_map.free_for_all
        for owner in self.combat.match.players:
            if owner is shooter or (not free_for_all and owner.team == shooter.team):
                continue
            state = self.sync(owner)
            if state is not None and state.active:
                yield owner, state

    def first(self, shooter, origin, direction, limit, *, radius=0.0, excluded=()):
        if self.geometry is None or limit <= 0:
            return None
        best = None
        for owner, state in self.enemies(shooter):
            if owner.body in excluded:
                continue
            mover = owner.mover
            height = mover.data.crouch_height if mover.crouched else mover.data.stand_height
            distance = intersect(self.geometry, mover.position, mover.yaw, mover.pitch,
                                 origin, direction, limit, owner_radius=mover.data.stand_radius,
                                 owner_height=height, radius=radius)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, owner, state)
        return best

    def pose(self, owner):
        mover = owner.mover
        height = mover.data.crouch_height if mover.crouched else mover.data.stand_height
        return frame(self.geometry, mover.position, mover.yaw, mover.pitch, mover.data.stand_radius, height)

    def within(self, shooter, centre, radius, *, visible):
        """Find each enemy shield's closest visible surface inside a query sphere."""
        if self.geometry is None or radius <= 0:
            return
        for owner, state in self.enemies(shooter):
            position, axes = self.pose(owner)
            local = tuple(sum((centre[k] - position[k]) * axis[k] for k in range(3)) for axis in axes)
            best = None
            for triangle in self.geometry.triangles:
                corners = tuple(triangle[n:n + 3] for n in (0, 3, 6))
                reach = radius if best is None else best
                if any(max(v[k] for v in corners) < local[k] - reach
                       or min(v[k] for v in corners) > local[k] + reach for k in range(3)):
                    continue
                hit = contacts.gjk(corners, (local,), reach=reach)
                if hit.distance <= reach:
                    point = tuple(position[k] + sum(axes[n][k] * hit.point_a[n] for n in range(3))
                                  for k in range(3))
                    if visible(point, owner.body):
                        best = hit.distance
                        if best == 0:
                            break
            if best is not None:
                yield owner, state, best

    def damage(self, owner, state, amount):
        instance = self.instance(owner)
        if instance is None or not state.active or amount <= 0:
            return
        state.current = expr.f32(max(0.0, state.current - amount))
        instance.write((0, 586), state.current)
        if state.current <= 0:
            now = self.combat.now
            state.active = False
            state.broken_until = now + self.geometry.break_delay
            state.regen_after = now + self.geometry.regen_delay
            self.lower(instance)
            instance.write((0, 61), self.geometry.break_delay)
            instance.component.run_now()
