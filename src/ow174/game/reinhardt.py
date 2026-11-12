"""Reconstructed Reinhardt barrier host using the pinned external model ray mesh.

Actual0095 Pet51/secondary input controls deployment. HP587, recovery Wait17,
Chase16 rate and break cooldown472 are authored. Model-to-eye attachment,
regeneration scheduling and damage routing are explicit reconstructions.
Generic Pet actors, native raycast filter policies and client transport are open.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.matrix import ray_box
from ow174.game.script import expr, runtime
from ow174.paths import DATA_DIR

MESH_SHA256 = 'dca55f2163d88355e718f842edc5b0e5ed8b0c79463cde5c64a6d564562aea10'
MODEL_SHA256 = '56d88e87a4e404264bbbbb2e0b2a42248abedfd4dea5358f1d96c826b0aec9fa'
def load_mesh():
    override = os.environ.get('ORPHEUS_BARRIER_DATA')
    path = Path(override) if override else DATA_DIR/'barriers_174.json'
    if not path.is_file():
        if override:
            raise FileNotFoundError(path)
        return (), None
    data = json.loads(path.read_text(encoding='utf-8'))
    geometry = {key: data[key] for key in ('vertices', 'triangles')}
    encoded = json.dumps(geometry, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if (data.get('schema') != 'ow174.reinhardt-barrier-mesh.v1' or data.get('build') != 104319
            or data.get('source_sha256') != MODEL_SHA256
            or hashlib.sha256(encoded).hexdigest() != MESH_SHA256):
        raise ValueError('barrier geometry does not match the verified104319 ray mesh')
    vertices = geometry['vertices']
    triangles = tuple(tuple(vertices[index] for index in row) for row in geometry['triangles'])
    bounds = tuple(tuple(op(p[k] for p in vertices) for k in range(3)) for op in (min, max))
    return triangles, bounds


@dataclass
class Barrier:
    player: object
    body: int
    instance: int
    hp: float
    most: float
    last: float
    lowered_at: float
    damaged_at: float
    blocked_until: float = 0.0
    raised: bool = False


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.triangles, self.bounds = load_mesh()
        self.owners = {}

    def retire(self, body):
        self.owners.pop(body, None)

    def owner(self, target):
        if getattr(getattr(target.owner, 'hero', None), 'guid', None) != 0x02E0000000000007:
            return None
        script = getattr(target.owner, 'body_script', None)
        if script is None or script.retired or script.component.fault is not None:
            return None
        component = getattr(script, 'component', None)
        if (not self.triangles or not isinstance(component, runtime.Component)
                or getattr(target.owner, 'mover', None) is None):
            return None
        instance = next((i for i in component.instances.values() if i.graph.index == 0x95), None)
        if instance is None or self.combat.vitals_of(target).dead:
            return None
        now = self.combat.now
        found = self.owners.get(target.entity)
        if found is None or found.instance != instance.id:
            most = max(0.0, expr.to_float(instance.find_var(0, 587).value()))
            found = Barrier(target.owner, target.entity, instance.id, most, most, now, now, now)
            self.owners[target.entity] = found
        pet = next((s for s in instance.states.values() if s.node.pos == 51), None)
        raised = (pet is not None and pet.active and 29 in component.held
                  and not self.combat.is_stunned(target.owner)
                  and not self.combat.is_asleep(target.owner)
                  and found.hp > 0 and now >= found.blocked_until)
        if found.raised and not raised:
            found.lowered_at = now
        delay = expr.to_float(instance.evaluate(instance.graph.node(17).field('m_timeout')))
        if not raised and found.hp < found.most:
            start = max(found.lowered_at, found.damaged_at) + delay
            rate = expr.to_float(instance.evaluate(instance.graph.node(16).field('m_9B0385B4')))
            found.hp = min(found.most, found.hp + max(0.0, now-max(found.last, start))*max(0.0, rate))
        found.last, found.raised = now, raised
        # The separate Pet health graph cannot execute yet. Publish the host's
        # current HP through0095's authored variable instead of the absent pet.
        slot = instance.vars.get(586)
        if slot is None or slot.value() != expr.f32(found.hp):
            instance.write((0, 586), found.hp)
            component.run_now()
        return found

    def update(self):
        if not self.triangles:
            return
        current = {owner.body for target in self.combat._targets()
                   if (owner := self.owner(target)) is not None}
        for body in self.owners.keys() - current:
            del self.owners[body]

    def damage(self, barrier, amount):
        barrier.hp = max(0.0, barrier.hp - max(0.0, amount))
        barrier.damaged_at = self.combat.now
        component = barrier.player.body_script.component
        instance = component.instances[barrier.instance]
        if barrier.hp <= 0:
            delay = max(0.0, expr.to_float(instance.find_var(0, 472).value()))
            barrier.blocked_until = self.combat.now + delay
            barrier.lowered_at = self.combat.now
            barrier.raised = False
        instance.write((0, 586), barrier.hp)
        component.run_now()

    def _distance(self, barrier, target, origin, direction, reach):
        from ow174.game.combat import _dot, _sub, aim

        mover = barrier.player.mover
        forward = aim(mover.yaw, mover.pitch)
        yaw = mover.yaw*2*math.pi/65536
        right = (-math.cos(yaw), 0.0, math.sin(yaw))
        up = cross(right, forward)
        eye = (target.feet[0], target.feet[1]+target.eye, target.feet[2])
        delta = tuple(origin[k]-eye[k]-forward[k]*(target.radius+.2) for k in range(3))
        # Reconstructed attachment: front capsule surface plus authored0.2;
        # animated hardpoint29 is not evaluated. Model -Z points outward.
        # Mesh vertices/topology are exact external resource data.
        basis = (right, up, tuple(-v for v in forward))
        local = tuple(_dot(delta, axis) for axis in basis)
        ray = tuple(_dot(direction, axis) for axis in basis)
        if ray_box(local, ray, *self.bounds, reach) is None:
            return None
        best = None
        for a, b, c in self.triangles:
            ab, ac = _sub(b, a), _sub(c, a)
            p = cross(ray, ac)
            determinant = _dot(ab, p)
            if abs(determinant) < 1e-10:
                continue
            offset = _sub(local, a)
            u = _dot(offset, p)/determinant
            q = cross(offset, ab)
            v = _dot(ray, q)/determinant
            distance = _dot(ac, q)/determinant
            if 0 <= u <= 1 and v >= 0 and u+v <= 1 and 0 <= distance <= reach:
                best = distance if best is None else min(best, distance)
        return best

    def ray(self, shooter, origin, direction, reach, *, team=None, extra=None):
        if not self.triangles:
            return None
        if team is None:
            team = shooter.team
        best = None
        for target in self.combat._targets():
            if target.owner is shooter:
                continue
            if not self.combat.match.game_map.free_for_all and target.team == team:
                continue
            barrier = extra if extra is not None and extra.body == target.entity else self.owner(target)
            if barrier is None or (not barrier.raised and barrier is not extra):
                continue
            distance = self._distance(barrier, target, origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, barrier
        return best


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
