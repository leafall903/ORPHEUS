"""Reconstructed server barrier geometry/resources; native pet replication is separate.

The prepared 104319 descriptor supplies the mesh. The reconstructed pose aligns
it to aim using the owner's bind-pose hardpoint 29, a 0.2 m forward offset and
shield bone 8C.
"""

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import bots, contacts
from ow174.game.collision import World
from ow174.game.script import expr, nodes, runtime
from ow174.game.sigma import Barrier
from ow174.paths import DATA_DIR

HERO = 0x02E0000000000007
GRAPH = 0x0095


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
    rear: float



@lru_cache(maxsize=4)
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
    rear = min(t[k] for t in triangles for k in (2, 5, 8))
    return Geometry(triangles, *anchors, *values, fingerprint, rear)


def configured_geometry() -> Geometry | None:
    selected = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
    path = Path(selected) if selected else DATA_DIR / "reinhardt_collision_174.json"
    if selected or path.is_file():
        return load_geometry(path)
    return None


@lru_cache(maxsize=4)
def mesh(geometry: Geometry) -> World:
    """A deterministic query cache; all input geometry is in the stored descriptor."""
    return World(geometry.triangles, name="Reinhardt Barrier Field 104319")


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
        # extent and authored 0.2 m offset determine the required clearance.
        support = max(forward[1] * owner_radius, forward[1] * (owner_height - owner_radius)) + owner_radius
        current = sum((origin[k] - feet[k]) * forward[k] for k in range(3))
        shift = max(0.0, support - geometry.rear + geometry.forward_offset - current)
        origin = tuple(origin[k] + forward[k] * shift for k in range(3))
    return origin, (right, up, forward)


@dataclass(kw_only=True)
class BarrierField(Barrier):
    """The collider and durability of an authored 0095 Pet activation."""

    profile: Geometry
    broken_until: float = 0.0

    def update(self, combat):
        pet = self.instance.states.get(17)
        raised = pet is not None and pet.active
        now = combat.now
        if raised and (combat.is_stunned(self.owner) or now < self.broken_until or self.vitals.total <= 0):
            self.lower()
            raised = False
        if not raised:
            if self.active:
                self.recharge_at = now + self.profile.regen_delay
            if self.recharge_at is not None:
                seconds = max(0.0, now - max(self.last, self.recharge_at))
                if seconds:
                    self.vitals.heal(seconds * self.profile.regen_rate)
        else:
            mover = self.owner.mover
            self.position, self.basis = frame(
                self.profile, mover.position, mover.yaw, mover.pitch,
                mover.values.stand_radius, mover.values.stand_height,
            )
        self.active = raised
        self.last = max(now, self.last)
        self.sync_durability()

    def melee_contact(self, centre, radius, visible):
        delta = tuple(c - p for c, p in zip(centre, self.position, strict=True))
        local = tuple(sum(d * a for d, a in zip(delta, axis, strict=True)) for axis in self.basis)
        for triangle in self.profile.triangles:
            corners = (triangle[:3], triangle[3:6], triangle[6:])
            if any(max(v[k] for v in corners) < local[k] - radius
                   or min(v[k] for v in corners) > local[k] + radius for k in range(3)):
                continue
            hit = contacts.gjk(corners, (local,), reach=radius)
            if hit.distance <= radius:
                point = tuple(self.position[k] + sum(axis[k] * v for axis, v in
                              zip(self.basis, hit.point_a, strict=True)) for k in range(3))
                if visible(point):
                    return True
        return False

    def sync_durability(self):
        current = self.instance.find_var(0, 586)
        value = expr.f32(self.vitals.total)
        if not expr.same(current.value(), value):
            self.instance.write((0, 586), value)

    def break_at(self, now):
        self.active = False
        self.recharge_at = now + self.profile.regen_delay
        self.broken_until = now + self.profile.break_delay
        self.sync_durability()
        self.lower()
        self.instance.write((0, 61), expr.f32(self.profile.break_delay))
        self.instance.component.run_now()

    def lower(self):
        ability = self.instance.states.get(59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = self.instance.states.get(17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        self.instance.component.run_now()


def native(world, cfg, instance):
    if (world.hero != HERO or world.match is None or instance.graph.index != GRAPH
            or instance.component.strict):
        return NotImplemented
    cls = cfg.get("$")
    if cls not in ("STU_295F1CDE", "STU_783B4CF0"):
        return NotImplemented
    profile = configured_geometry()
    if profile is None:
        return NotImplemented
    if cls == "STU_295F1CDE":
        return 1.0
    entity = cfg.get("m_entity")
    if (isinstance(entity, dict) and entity.get("$") == "STUConfigVarDynamic"
            and entity.get("m_identifier") == 38 and entity.get("m_60DB8F99", 0) == 0):
        return next((shield.vitals.total for shield in world.match.combat.barriers.values()
                     if shield.owner_body == world.body), profile.maximum)
    return NotImplemented


def mount_active(combat):
    """Mount the mesh when a live Reinhardt's raised Pet first becomes active."""
    from ow174.game.combat import Vitals

    if combat.match.ended:
        return
    for owner in combat.match.players:
        script = owner.body_script
        if (owner.hero.guid != HERO or not owner.has_body or owner.gone
                or script is None or script.component.strict or script.component.owner_dead):
            continue
        instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
        pet = instance.states.get(17) if instance is not None else None
        if pet is None or not pet.active:
            continue
        if any(shield.owner_body == owner.body for shield in combat.barriers.values()):
            continue
        profile = configured_geometry()
        if profile is None:
            return
        entity = combat.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("Barrier Field entity allocator exhausted")
        combat.next_bot += 1
        combat.barriers[entity] = BarrierField(
            entity, owner.body, owner, instance, Vitals(entity, profile.maximum), mesh(profile),
            tuple(owner.mover.position), (), combat.now, profile=profile,
        )
