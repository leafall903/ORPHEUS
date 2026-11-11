"""Scoped0095 barrier mesh/HP host, not native pet attachment/replication.

Actual external triangles and authored resource expressions are consumed. Raised
pose, receiver/penetration and regeneration integration remain reconstructions.
"""

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
        self.geometry = None
        self.states = {}

    def retire(self, body):
        self.states.pop(body, None)

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
        if self.geometry is None:
            return
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

    def intersect(self, owner, origin, direction, reach):
        data = owner.mover.values
        return barrier_mesh.intersect(self.geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                                      origin, direction, reach, owner_radius=data.stand_radius,
                                      owner_height=data.stand_height)

    def trace(self, shooter, origin, direction, reach):
        best = None
        for owner in self.enemies(shooter):
            distance = self.intersect(owner, origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, owner
        return best

    def melee(self, shooter, centre, radius, amount, *, origin=None):
        from ow174.game.combat import capsule_visible

        if amount <= 0:
            return
        if origin is None:
            origin = self.fight._eye(shooter)

        def visible(point):
            return capsule_visible(self.fight._collision(), origin, point, point, 0)

        for owner in self.enemies(shooter):
            mover = owner.mover
            data = mover.values
            if barrier_mesh.sphere_contact(self.geometry, mover.position, mover.yaw, mover.pitch,
                                           centre, radius, data.stand_radius, data.stand_height, visible):
                self.damage(owner, amount)

    def splash(self, shooter, point, origin, blast, impact, direct_amount):
        from ow174.game.combat import capsule_visible

        # Keep cover for the entire blast even if its impact breaks the shield.
        cover = self.enemies(shooter)
        for owner in cover:
            if owner is impact:
                self.damage(owner, direct_amount + blast.amount * blast.splash(0))
                continue
            mover = owner.mover
            data = mover.values
            position, axes = barrier_mesh.frame(self.geometry, mover.position, mover.yaw, mover.pitch,
                                               data.stand_radius, data.stand_height)
            distance, surface = barrier_mesh.nearest_surface(self.geometry.triangles, position, axes, point)
            scalar = blast.splash(distance)
            if scalar > 0 and capsule_visible(self.fight._collision(), origin, surface, surface, 0):
                self.damage(owner, blast.amount * scalar)
        return cover

    def covers(self, cover, origin, a, b):
        return barrier_mesh.covers(self.intersect, cover, origin, a, b)

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
