"""Scoped0095 barrier mesh/HP host, not native pet attachment/replication.

Actual external triangles and authored resource expressions are consumed. Raised
pose, receiver/penetration and regeneration integration remain reconstructions.
"""

import math
from dataclasses import dataclass

from ow174.game import barrier_mesh
from ow174.game.script import expr, nodes, runtime

HERO = 0x02E0000000000007
GRAPH = 0x0095


@dataclass
class BarrierState:
    current: float
    maximum: float
    last_update: float
    regen_after: float
    broken_until: float = 0.0
    active: bool = False


class ReinhardtBarriers:
    def __init__(self, fight):
        self.fight = fight
        self.geometry = barrier_mesh.configured_geometry()
        self.states = {}

    def retire(self, body):
        self.states.pop(body, None)

    @staticmethod
    def instance(owner):
        script = getattr(owner, "body_script", None)
        if script is None or script.retired or script.hero != HERO or script.component.strict:
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

    def sync(self, owner):
        if self.geometry is None or not getattr(owner, "has_body", False):
            return None
        instance = self.instance(owner)
        if instance is None:
            return None
        maximum = max(0.0, expr.to_float(instance.find_var(0, 587).value()))
        state = self.states.get(owner.body)
        now = self.fight.now
        if state is None:
            state = self.states[owner.body] = BarrierState(maximum, maximum, now, now)
        state.maximum = maximum
        state.current = min(state.current, maximum)
        pet = instance.states.get(17)
        raised = pet is not None and pet.active
        unavailable = (owner.gone or not owner.spawned or owner.client is None
                       or self.fight.is_dead(owner) or self.fight.is_stunned(owner))
        if raised and (unavailable or state.current <= 0 or now < state.broken_until):
            self.lower(instance)
            raised = False
        delay = max(0.0, expr.to_float(instance.evaluate(instance.graph.node(17).field("m_timeout"))))
        if state.active and not raised:
            state.regen_after = now + delay
        if not raised and not unavailable and now > max(state.last_update, state.regen_after):
            rate = max(0.0, expr.to_float(instance.evaluate(instance.graph.node(16).field("m_9B0385B4"))))
            duration = now - max(state.last_update, state.regen_after)
            state.current = expr.f32(min(maximum, state.current + rate * duration))
        state.active = raised and not unavailable and state.current > 0 and now >= state.broken_until
        state.last_update = max(now, state.last_update)
        value = expr.f32(state.current)
        if not expr.same(instance.find_var(0, 586).value(), value):
            instance.write((0, 586), value)
        return state

    def update(self):
        current = set()
        for owner in self.fight.match.players:
            if self.instance(owner) is not None and owner.has_body:
                current.add(owner.body)
                self.sync(owner)
        for body in set(self.states) - current:
            self.retire(body)

    def enemies(self, shooter):
        if self.geometry is None:
            return ()
        found = []
        for owner in self.fight.match.players:
            if (owner is shooter
                    or (not self.fight.match.game_map.free_for_all and owner.team == shooter.team)):
                continue
            state = self.sync(owner)
            if state is not None and state.active:
                found.append(owner)
        return tuple(found)

    def intersect(self, owner, origin, direction, reach, *, radius=0.0):
        data = owner.mover.values
        return barrier_mesh.intersect(self.geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                                      origin, direction, reach, radius=radius, owner_radius=data.stand_radius,
                                      owner_height=data.stand_height)

    def trace(self, shooter, origin, direction, reach, *, radius=0.0):
        best = None
        for owner in self.enemies(shooter):
            distance = self.intersect(owner, origin, direction, reach, radius=radius)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, owner
        return best

    def damage(self, owner, amount):
        state = self.states.get(owner.body)
        if state is None or not state.active or amount <= 0:
            return
        state.current = expr.f32(max(0.0, state.current - amount))
        instance = self.instance(owner)
        instance.write((0, 586), state.current)
        if state.current <= 0:
            delay = max(0.0, expr.to_float(instance.find_var(0, 472).value()))
            state.broken_until = self.fight.now + delay
            state.regen_after = self.fight.now + max(
                0.0, expr.to_float(instance.evaluate(instance.graph.node(17).field("m_timeout"))))
            state.active = False
            self.lower(instance)
            instance.write((0, 61), delay)
            instance.component.run_now()

    def melee(self, shooter, centre, radius, amount, *, contacted=None):
        from ow174.game.combat import capsule_visible

        for owner in self.enemies(shooter):
            if contacted is not None and owner.body in contacted:
                continue
            data = owner.mover.values
            if barrier_mesh.overlaps(self.geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                                     centre, centre, radius, data.stand_radius, data.stand_height,
                                     visible=lambda p: capsule_visible(self.fight._collision(),
                                                                       self.fight._eye(shooter), p, p, 0)):
                self.damage(owner, amount)
                if contacted is not None:
                    contacted.add(owner.body)

    def blocked(self, origin, point, owners):
        delta = tuple(point[k] - origin[k] for k in range(3))
        reach = math.sqrt(sum(value * value for value in delta))
        if reach <= 1e-9:
            return False
        direction = tuple(value / reach for value in delta)
        return any((distance := self.intersect(owner, origin, direction, reach)) is not None
                   and distance < reach - 1e-6 for owner in owners)

    def nearest(self, owner, point):
        data = owner.mover.values
        return barrier_mesh.nearest(self.geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                                    point, data.stand_radius, data.stand_height)

    def covers(self, origin, a, b, radius, owners):
        axis = tuple(b[k] - a[k] for k in range(3))
        offset = tuple(origin[k] - a[k] for k in range(3))
        squared = sum(v * v for v in axis)
        share = 0 if squared <= 1e-9 else max(0.0, min(1.0, sum(axis[k] * offset[k]
                                                               for k in range(3)) / squared))
        delta = tuple(a[k] + share * axis[k] - origin[k] for k in range(3))
        distance = math.sqrt(sum(v * v for v in delta))
        if distance <= radius:
            return False
        surface = tuple(origin[k] + delta[k] * (distance - radius) / distance for k in range(3))
        return self.blocked(origin, surface, owners)
