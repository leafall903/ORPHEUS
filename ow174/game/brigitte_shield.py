"""Host the finite 0EE4 pool behind 0EE3's authored raised state.

The 5DA0 mesh is authored. Its aim-relative pose, the pool and the break
lockout are reconstructed without a separate 0EE4 receiver VM.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.script import expr, runtime

HERO = 0x02E0000000000195
GRAPH = 0x0EE3
REGEN_DELAY = 2.0
REGEN_RATE = 85.0
BREAK_DELAY = 5.0
POSE_FORWARD = 0.5
_mesh_path = os.environ.get("ORPHEUS_BRIGITTE_COLLISION")
_mesh_digest = "7b5144339f3bd9ce503bd322987766d2d3ec8eb42d892242f1a122cfdc6548a6"
GEOMETRY_SHA256 = _mesh_digest if _mesh_path else None


def load_geometry():
    if not _mesh_path:
        return None
    raw = Path(_mesh_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
        raise ValueError("Brigitte barrier geometry checksum mismatch")
    data = json.loads(raw)
    if (data["schema"], data["build"]) != ("orpheus-brigitte-barrier-v1", 104319):
        raise ValueError("Brigitte barrier geometry schema mismatch")
    vertices = tuple(tuple(row[i:i + 3]) for row in data["triangles"] for i in (0, 3, 6))
    triangles = tuple((i, i + 1, i + 2) for i in range(0, len(vertices), 3))
    bottom = min(vertex[1] for vertex in vertices)
    rear = min(vertex[2] for vertex in vertices)
    return vertices, triangles, bottom, rear


def pose(mover, bottom, rear, radius, height):
    yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    right, up, forward = (cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp)
    # At level pitch the authored mesh's lower edge rests on the floor.
    # Keep the rear of the mesh beyond the wearer's capsule.
    support = max(forward[1] * radius, forward[1] * (height - radius)) + radius
    distance = max(POSE_FORWARD, support - rear + 0.02)
    centre = tuple(p - bottom * u + distance * f
                   for p, u, f in zip(mover.position, up, forward, strict=True))
    return centre, (right, up, forward)


@dataclass
class Shield:
    instance: object
    health: float
    maximum: float
    last: float
    regen_after: float = 0.0
    broken_until: float = 0.0
    raised: bool = False

    def active(self):
        state = self.instance.states.get(5)
        return self.health > 0 and state is not None and state.active and not state.ending

    def publish(self):
        self.instance.write((expr.INSTANCE, 586), expr.f32(self.health))
        state = self.instance.states.get(32)
        if state is not None and state.active:
            state.dependency_changed()


class Shields:
    def __init__(self, combat):
        self.combat = combat
        self.geometry = load_geometry()
        self.by_body = {}

    def retire(self, body):
        self.by_body.pop(body, None)

    def native(self, world, cfg, instance):
        key = cfg.get("m_entity")
        if (self.geometry is None or instance is None or world.hero != HERO
                or instance.graph.index != GRAPH or instance.component.entity != world.body
                or cfg.get("$") != "STU_9B7648C3" or not isinstance(key, dict)
                or key.get("m_identifier") != 13214):
            return NotImplemented
        shield = self.by_body.get(world.body)
        if shield is None:
            maximum = expr.to_float(instance.find_var(expr.INSTANCE, 587).value())
            if maximum <= 0:
                raise ValueError("authored Brigitte shield capacity is unavailable")
            shield = Shield(instance, maximum, maximum, self.combat.now)
            self.by_body[world.body] = shield
        return expr.f32(shield.health)

    @staticmethod
    def lower(shield):
        ability = shield.instance.states.get(30)
        if ability is not None and ability.active:
            ability.stop()
        state = shield.instance.states.get(5)
        if state is not None and state.active:
            state.request(runtime.ABORT)
        shield.instance.component.run_now()

    def update(self, now):
        for player in self.combat.match.players:
            shield = self.by_body.get(player.body)
            if shield is None or self.combat.is_dead(player):
                continue
            if now < shield.broken_until and shield.active():
                self.lower(shield)
            active = shield.active()
            if shield.raised and not active:
                shield.regen_after = now + REGEN_DELAY
            start = max(shield.last, shield.regen_after)
            if not active and now > start and shield.health < shield.maximum:
                shield.health = min(shield.maximum, shield.health + (now - start) * REGEN_RATE)
                shield.publish()
                shield.instance.component.run_now()
            shield.raised = active
            shield.last = now

    def damage(self, shield, amount):
        shield.health = max(0.0, shield.health - amount)
        shield.regen_after = self.combat.now + REGEN_DELAY
        shield.publish()
        if shield.health == 0:
            shield.broken_until = self.combat.now + BREAK_DELAY
            self.lower(shield)
        else:
            shield.instance.component.run_now()
