"""Selected Reinhardt weapon barrier on the reconstructed match host.

The mesh and initial capacity come from 104319. Current eye/yaw attachment,
team filtering and health ownership are host policies. Native Pet replication,
hardpoint animation, receiver filters and recovery remain separate gates.
"""

import hashlib
import os
import struct
from functools import cache
from pathlib import Path

from ow174.game import collision, deflection, props
from ow174.game.script import expr, runtime
from ow174.paths import DATA_DIR

REINHARDT = 0x02E0000000000007
BREAK_MESSAGE = 0x0240000000000053
MODEL_SHA256 = "56d88e87a4e404264bbbbb2e0b2a42248abedfd4dea5358f1d96c826b0aec9fa"


@cache
def mesh():
    configured = os.environ.get("ORPHEUS_REINHARDT_BARRIER_MODEL")
    path = Path(configured) if configured else DATA_DIR / "reinhardt_barrier_104319.00C"
    if not configured and not path.is_file():
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError("Reinhardt Barrier Field requires verified 104319 A3C.00C")
    payload = props.chunks(data)["MSTU"]
    offset, size = struct.unpack_from("<qq", payload)
    stu = props.Structured(payload[offset : offset + size])
    wrapper, = (struct.unpack_from("<i", row)[0] for row in stu.array(0, props.SHAPES, 8))
    shape = stu.ref(wrapper, props.SHAPE)
    points = [struct.unpack_from("<3f", row) for row in stu.array(shape, props.VERTICES, 16)]
    raw = b"".join(stu.array(shape, props.TRIANGLES, 1))
    triangles = [
        tuple(coordinate for i in row[:3] for coordinate in points[i])
        for row in struct.iter_unpack("<3I3i4H", raw)
    ]
    return collision.World(triangles)


def model_identity():
    return MODEL_SHA256 if mesh() is not None else None


class BarrierField:
    def __init__(self, combat):
        self.combat = combat
        self.health = {}
        self.children = {}
        self.model_sha256 = model_identity()

    def native(self, instance, cfg):
        if self.model_sha256 is None or instance.graph.index != 0x95:
            return NotImplemented
        kind = cfg.get("$")
        if kind == "STU_295F1CDE":
            # Unit capacity scaling for the selected default-ruleset host.
            return 1.0
        body = instance.component.entity
        child = self.children.get(body)
        if (kind == "STU_783B4CF0" and child is not None
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(child)
                and (cfg.get("m_7E8B666C") or {}).get("m_7E8B666C") == 0):
            return self.health[body]
        return NotImplemented

    def pet(self, state, entering):
        instance = state.instance
        if (self.model_sha256 is None or instance.graph.index != 0x95 or state.node.pos != 51
                or instance.evaluate(state.node.field("m_entityDefinition")) != expr.Asset(0x040000000000011A)):
            return
        body = instance.component.entity
        if entering:
            maximum = expr.to_float(instance.vars[587].value())
            remaining = expr.to_float(instance.vars[586].value())
            self.health[body] = max(0.0, min(maximum, remaining))
            child = self.combat.allocate_combat_entity()
            self.children[body] = child
        else:
            if self.children.pop(body, None) is not None:
                instance.write((expr.INSTANCE, 586), self.health[body])
            child = 0
        instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(child) if child else 0)

    def retire(self, body):
        self.health.pop(body, None)
        self.children.pop(body, None)

    def intersect(self, shooter, origin, direction, limit, *, include_endpoint=False, team=None):
        return min(self.intersections(shooter, origin, direction, limit, include_endpoint=include_endpoint, team=team),
                   key=lambda hit: hit[0], default=None)

    def intersections(self, shooter, origin, direction, limit, *, include_endpoint=False, team=None):
        scene = mesh()
        if scene is None:
            return
        for target in self.combat._enemies(shooter, team=team):
            owner = target.owner
            if getattr(getattr(owner, "hero", None), "guid", None) != REINHARDT:
                continue
            script = owner.body_script
            if script is None or script.retired or self.combat.is_stunned(owner):
                continue
            ability = next((i for i in script.component.instances.values() if i.graph.index == 0x95), None)
            state = ability.states.get(17) if ability else None
            if target.entity not in self.children or state is None or not state.active or state.node.pos != 51:
                continue
            initial = ability.vars[587].value()
            remaining = self.health.get(target.entity, initial)
            if remaining <= 0:
                continue
            axes = deflection.basis(owner.mover.yaw, 0)
            eye = self.combat._eye(owner)
            # The animated 0029 attachment is unavailable on the host.
            centre = tuple(value + forward for value, forward in zip(eye, axes[2], strict=True))
            relative = tuple(a - b for a, b in zip(origin, centre, strict=True))
            local_origin = tuple(sum(a * b for a, b in zip(relative, axis, strict=True)) for axis in axes)
            local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in axes)
            hit = scene.cast_ray(local_origin, local_direction, limit)
            if hit is not None and (include_endpoint or hit.distance < limit):
                yield hit.distance, target.entity, remaining

    def damage(self, entity, remaining, amount):
        capacity = max(0., remaining - amount)
        self.health[entity] = capacity
        owner = next((p for p in self.combat.match.players if p.body == entity), None)
        if owner is None or owner.body_script is None or owner.body_script.retired:
            return
        component = owner.body_script.component
        ability = next((i for i in component.instances.values() if i.graph.index == 0x95), None)
        if ability is None:
            return
        ability.write((expr.INSTANCE, 586), capacity)
        if capacity > 0 or remaining <= 0:
            return
        # The host mesh child sends gameplay only. Native visual parameters are open.
        sender = self.children.get(entity)
        if sender is not None and runtime.send_instance_message(ability, BREAK_MESSAGE, {}, sender):
            component.run_now()
