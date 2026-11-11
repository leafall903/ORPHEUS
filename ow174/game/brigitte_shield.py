"""Host the finite pool attached by 0EE3's authored shield pet.

The 5DA0 mesh is authored. Its aim-relative pose, the pool and the break
lockout are reconstructed without a separate 0EE4 receiver VM.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.collision import World
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
    triangles = data["triangles"]
    bottom = min(row[i] for row in triangles for i in (1, 4, 7))
    rear = min(row[i] for row in triangles for i in (2, 5, 8))
    return World(triangles), bottom, rear


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
    entity: int
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

    def create(self, instance, node):
        if self.geometry is None or (instance.graph.index, node.pos) != (GRAPH, 8):
            return NotImplemented
        if node.field("m_entityDefinition")["m_entityDef"] != "0x0400000000002284":
            raise ValueError("Brigitte shield definition changed")
        maximum = expr.to_float(instance.find_var(expr.INSTANCE, 587).value())
        if maximum <= 0:
            raise ValueError("authored Brigitte shield capacity is unavailable")
        entity = self.combat.next_bot
        if entity >= 0xD0000000:
            raise ValueError("barrier entity allocator exhausted")
        self.combat.next_bot += 1
        self.by_body[instance.component.entity] = Shield(instance, entity, maximum, maximum, self.combat.now)
        instance.write(expr.dynamic(node.field("m_B3019338")), expr.Entity(entity))
        return True

    def body_health(self, entity):
        for shield in self.by_body.values():
            if shield.entity == entity:
                return shield.health, shield.maximum
        return None

    def trace(self, shooter, origin, direction, reach, *, team=None, extra=None):
        if self.geometry is None:
            return None
        if team is None:
            team = shooter.team
        free_for_all = self.combat.match.game_map.free_for_all
        mesh, bottom, rear = self.geometry
        best = None
        for target in self.combat._targets():
            if target.owner is shooter or (target.team == team and not free_for_all):
                continue
            shield = self.by_body.get(target.entity)
            if shield is None or (shield is not extra and (
                not shield.active() or self.combat.now < shield.broken_until
            )):
                continue
            centre, axes = pose(target.owner.mover, bottom, rear, target.radius, target.height)
            delta = tuple(a - b for a, b in zip(origin, centre, strict=True))
            ray_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in axes)
            ray_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in axes)
            hit = mesh.cast_ray(ray_origin, ray_direction, reach, category=0xFFFF)
            if hit is not None and (best is None or hit.distance < best[0]):
                best = hit.distance, shield
        return best

    @staticmethod
    def lower(shield):
        ability = shield.instance.states.get(30)
        if ability is not None and ability.active:
            ability.stop()
        state = shield.instance.states.get(5)
        if state is not None and state.active:
            state.request(runtime.ABORT)
        shield.instance.component.run_now()

    def enforce_lockout(self, player):
        shield = self.by_body.get(player.body)
        if shield is not None and self.combat.now < shield.broken_until and shield.active():
            self.lower(shield)

    def update(self, now):
        for player in self.combat.match.players:
            shield = self.by_body.get(player.body)
            if shield is None or self.combat.is_dead(player):
                continue
            self.enforce_lockout(player)
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
            shield.raised = False
        else:
            shield.instance.component.run_now()

    def bash_move(self, player):
        shield = self.by_body.get(player.body)
        if shield is None:
            return None
        instance = shield.instance
        window = instance.states.get(instance.graph.node(21).state)
        if window is None or not window.active:
            # The unhosted dash transition normally clears this hit latch.
            latch = instance.find_var(expr.INSTANCE, 488, create=False)
            if latch is not None and expr.truthy(latch.value()):
                instance.write((expr.INSTANCE, 488), False)
            return None
        from ow174.game.combat import _along, aim

        # Node171 supplies displacement per 60 Hz step.
        speed = expr.to_float(instance.find_var(expr.INSTANCE, 7726).value()) * 60
        hit = instance.states.get(instance.graph.node(15).state)
        if hit is not None and hit.counter and hit.start >= window.start:
            # Keep the remaining throttle modifier from advancing past the hit.
            return tuple(player.mover.position), speed
        direction = aim(player.mover.yaw, 0)
        return _along(player.mover.position, direction, speed * window.ms / 1000), speed

    def interrupt_bash(self, actor):
        shield = self.by_body.get(actor.body)
        if shield is None:
            return
        instance = shield.instance
        window = instance.states.get(instance.graph.node(21).state)
        if window is None or not window.active:
            return
        window.request(runtime.ABORT)
        movement = instance.graph.node(20).state
        for key in tuple(actor.mover.mods):
            if key[:2] == (instance.id, movement):
                del actor.mover.mods[key]
        actor.mover.velocity = (0.0, actor.mover.velocity[1], 0.0)

    def bash_owner(self, instance):
        fight = self.combat
        shield = self.by_body.get(instance.component.entity)
        if shield is None or shield.instance is not instance:
            return None
        return next((p for p in fight.match.players if p.body == instance.component.entity
                     and p.has_body and p.spawned and not p.gone and not fight.is_dead(p)
                     and not fight.is_asleep(p) and not fight.is_stunned(p)), None)

    def bash_targets(self, state):
        from ow174.game.combat import aim, ray_capsule, ray_world

        caster = self.bash_owner(state.instance)
        if caster is None:
            return ()
        fight = self.combat
        origin, direction = fight._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        hits = []
        for target in fight._enemies(caster):
            distance = ray_capsule(origin, direction, *target.segment(), target.radius)
            if distance is not None and distance <= reach:
                hits.append((distance, target.entity))
        if not hits:
            return ()
        distance, entity = min(hits)
        if (ray_world(fight._collision(), origin, direction, distance) is not None
                or fight._barrier_contact(caster, origin, direction, distance) is not None
                or fight._body_barrier_active(entity)):
            return ()
        return (expr.Entity(entity),)

    def bash_hit(self, state):
        caster = self.bash_owner(state.instance)
        if caster is None:
            return
        fight = self.combat
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if not selected:
            return
        entity = expr.to_entity(selected[0])
        target = next((target for target in fight._enemies(caster) if target.entity == entity), None)
        if target is None:
            return
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
        loadout = state.evaluate(modify["m_loadout"], watch=False)
        guid = loadout.guid if isinstance(loadout, expr.Asset) else None
        fight._damage(caster, target, amount, False, guid, fight.tick, world_tick=fight.tick)

    def bash_stun(self, instance, node):
        selected = instance.evaluate(node.field("m_targets"))
        caster = self.bash_owner(instance)
        if selected and caster is not None:
            fight = self.combat
            entity = expr.to_entity(selected[0])
            target = next((target for target in fight._enemies(caster) if target.entity == entity), None)
            if target is not None and not fight._body_barrier_active(entity):
                duration = node.field("m_69F4B52C.m_6525E98B.m_1EB5A024")[0]["m_value"]
                fight.stun(target, expr.to_float(instance.evaluate(duration)))
        return True
