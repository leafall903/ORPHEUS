"""Reconstructed Zarya combat services driven by the target build's authored States.

Target selection, damage interception and shield recovery use host services. Pet entity
hosting, control immunity and original-client presentation remain open.
"""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.script import expr, graph, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E0000000000068


@dataclass
class Barrier:
    activation: tuple
    remaining: float


@dataclass
class Recovery:
    damaged_tick: int
    processed_tick: int


class ShieldRecovery:
    """0033 shield regeneration bridged to authoritative pools for Zarya.

    Node66 supplies delay3 times21868e, node5 supplies rate30 times21869e.
    Nodes68/69 supply the authored default scalars1. Undeclared scalars do not
    regenerate. Reconstruction: actual body pool loss resets delay, a fully
    intercepted barrier hit does not, and only shields regenerate.
    """

    def __init__(self, combat):
        self.combat = combat
        self.bodies: dict[int, Recovery] = {}

    def retire(self, body: int) -> None:
        self.bodies.pop(body, None)

    def damaged(self, owner, tick: int) -> None:
        if getattr(getattr(owner, "hero", None), "guid", None) != HERO:
            return
        previous = self.bodies.get(owner.body)
        if previous is not None:
            tick = max(tick, previous.damaged_tick, previous.processed_tick)
        self.bodies[owner.body] = Recovery(tick, tick)

    @staticmethod
    def scalar(component, identifier):
        var = component.entity_var(identifier, create=False)
        value = None if var is None else var.value()
        return None if value is None else max(0.0, expr.to_float(value))

    def update(self, tick: int) -> None:
        current = {p.body: p for p in self.combat.match.players if p.has_body and not p.gone}
        for body, recovery in list(self.bodies.items()):
            player = current.get(body)
            vitals = self.combat.vitals.get(body)
            if (self.combat.match.ended or player is None or vitals is None or vitals.dead
                    or player.hero.guid != HERO):
                self.retire(body)
                continue
            previous = recovery.processed_tick
            recovery.processed_tick = max(previous, tick)
            script = player.body_script
            if script is None or tick <= previous:
                continue
            base = graph.graph(0x0033)
            delay = base.node(66).field("m_7130456B.m_expression.m_D99EF254")[0]
            rate = base.node(5).field("m_modifyHealth.m_amount.m_expression.m_D99EF254")[1]
            delay_scalar = self.scalar(script.component, 21868)
            rate_scalar = self.scalar(script.component, 21869)
            if delay_scalar is None or rate_scalar is None:
                continue
            delay *= delay_scalar
            rate *= rate_scalar
            since = max(previous, recovery.damaged_tick + delay / 0.016)
            amount = rate * max(0.0, tick - since) * 0.016
            amount = min(amount, vitals.most[SHIELDS_POOL] - vitals.now[SHIELDS_POOL])
            if amount > 0:
                vitals.now[SHIELDS_POOL] += amount
                vitals.changed.add(SHIELDS_POOL)
                self.combat._sync_body_health(player, vitals)


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.active: dict[int, Barrier] = {}
        self.projected: dict[int, Barrier] = {}

    def retire(self, body: int) -> None:
        self.active.pop(body, None)
        self.projected.pop(body, None)

    def track_targets(self, state, angles):
        """Reconstructed ally selection for the two authored projected searches.

        Radius and base cone come from the resource. Missing cone modifier uses1.
        Rank by angular alignment then distance; require a live ally and static LOS.
        Other target States remain undeclared, rather than sharing this filter policy.
        """
        if state.instance.graph.index != 0x025C or state.node.pos not in (11, 18):
            return NotImplemented
        owner = next((p for p in self.combat.match.players
                      if p.body == state.instance.component.entity), None)
        if (owner is None or getattr(owner.hero, "guid", None) != HERO
                or self.combat.is_dead(owner) or self.combat.match.game_map.free_for_all):
            return ()
        # Import geometry lazily: combat owns this service and its geometric types.
        from ow174.game.combat import aim, ray_world

        origin, direction = self.combat._eye(owner), aim(*angles)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        cone = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
        modifier = state.instance.component.entity_var(9595, create=False)
        if modifier is None or modifier.value() is None:
            cone = state.node.field("m_A30B383E")["m_expression"]["m_D99EF254"][0]
        best = None
        for target in self.combat._targets():
            if target.owner is owner or target.team != owner.team or self.combat.vitals_of(target).dead:
                continue
            point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
            delta = tuple(point[k] - origin[k] for k in range(3))
            distance = math.sqrt(sum(v * v for v in delta))
            if distance <= 0 or distance > reach:
                continue
            alignment = sum(delta[k] * direction[k] for k in range(3)) / distance
            if alignment < math.cos(math.radians(cone)):
                continue
            wall = ray_world(self.combat._collision(), origin,
                             tuple(v / distance for v in delta), distance)
            if wall is not None and wall < distance:
                continue
            score = (1 - alignment, distance, target.entity)
            if best is None or score < best[0]:
                best = score, target.entity
        return () if best is None else (best[1],)

    def intercept(self, owner, amount: float, source: int) -> bool:
        """Consume one hostile damage event while an authored barrier is active.

        Reconstructed policy: the event that breaks the barrier is blocked in full;
        only remaining barrier HP contributes to absorbed-damage energy. A broken
        activation cannot refill until a different authored pet activation starts.
        """
        if amount <= 0:
            return False
        for caster, instance, duration, barrier in self._covering(owner):
            if barrier.remaining <= 0:
                continue
            absorbed = min(amount, barrier.remaining)
            barrier.remaining -= absorbed
            entry = graph.graph(0x0242).node(5)
            message = graph.guid(entry.field("m_gameMessage.m_gameMessage"))
            runtime.send_message(instance.component, message,
                                 {0x2A: absorbed, 0x12: expr.Entity(source)}, caster.body)
            if barrier.remaining <= 0:
                instance.state(duration).request_finish()
            return True
        return False

    def protects(self, owner) -> bool:
        """Check status admission without consuming HP or awarding energy."""
        return any(barrier.remaining > 0 for _, _, _, barrier in self._covering(owner))

    def _covering(self, owner):
        if getattr(getattr(owner, "hero", None), "guid", None) == HERO:
            instance = self._instance(owner, 0x0244)
            if instance is not None:
                barrier = self._barrier(owner, instance, 2, self.active)
                if barrier is not None:
                    yield owner, instance, 1, barrier
        target = owner.entity if isinstance(owner, bots.Bot) else owner.body
        for caster in self.combat.match.players:
            if (not caster.has_body or caster.team != owner.team
                    or self.combat.match.game_map.free_for_all
                    or getattr(caster.hero, "guid", None) != HERO):
                continue
            instance = self._instance(caster, 0x025C)
            if instance is None:
                continue
            selected = instance.vars.get(536)
            if selected is not None and selected.value() == expr.Entity(target):
                barrier = self._barrier(caster, instance, 10, self.projected)
                if barrier is not None:
                    yield caster, instance, 11, barrier

    @staticmethod
    def _instance(owner, index):
        script = owner.body_script
        if script is None or script.component.owner_dead:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == index), None)

    @staticmethod
    def _barrier(owner, instance, pet_state, storage):
        pet = instance.state(pet_state)
        if not pet.active:
            storage.pop(owner.body, None)
            return None
        activation = (instance.id, pet.counter, pet.start)
        barrier = storage.get(owner.body)
        if barrier is None or barrier.activation != activation:
            pool = graph.graph(0x0256).node(0).field("m_amount")
            capacity = expr.to_float(pet.evaluate(pool, watch=False))
            barrier = storage[owner.body] = Barrier(activation, max(0.0, capacity))
        return barrier
