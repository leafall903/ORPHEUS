"""Zarya personal and projected damage interception from original barrier states."""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000068


@dataclass
class Barrier:
    activation: tuple
    remaining: float


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.active: dict[int, Barrier] = {}
        self.projected: dict[int, Barrier] = {}

    def retire(self, body: int) -> None:
        self.active.pop(body, None)
        self.projected.pop(body, None)

    def track_targets(self, state):
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

        origin, direction = self.combat._eye(owner), aim(owner.mover.yaw, owner.mover.pitch)
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

    def protects(self, owner) -> bool:
        return self.intercept(owner, None, 0)

    def intercept(self, owner, amount: float | None, source: int) -> bool:
        """Consume one hostile damage event while the authored personal pet is active.

        Reconstructed policy: the event that breaks the barrier is blocked in full;
        only remaining barrier HP contributes to absorbed-damage energy. A broken
        activation cannot refill until a different authored pet activation starts.
        None queries protection without touching the stored activation.
        """
        if (getattr(getattr(owner, "hero", None), "guid", None) == HERO
                and self._intercept(owner, amount, source, projected=False)):
            return True
        target = owner.entity if isinstance(owner, bots.Bot) else owner.body
        for caster in self.combat.match.players:
            if (not caster.has_body or caster.team != owner.team
                    or self.combat.match.game_map.free_for_all
                    or getattr(caster.hero, "guid", None) != HERO):
                continue
            script = caster.body_script
            if script is None:
                continue
            instance = next((i for i in script.component.instances.values()
                             if i.graph.index == 0x025C), None)
            if instance is None:
                continue
            selected = instance.vars.get(536)
            if (selected is not None and selected.value() == expr.Entity(target)
                    and self._intercept(caster, amount, source, projected=True)):
                return True
        return False

    def _intercept(self, owner, amount: float | None, source: int, *, projected: bool) -> bool:
        script = owner.body_script
        if script is None or script.component.owner_dead:
            return False
        instance = next((i for i in script.component.instances.values()
                         if i.graph.index == (0x025C if projected else 0x0244)), None)
        if instance is None:
            return False
        pet = instance.state(10 if projected else 2)
        storage = self.projected if projected else self.active
        if not pet.active or pet.ending:
            if amount is not None:
                storage.pop(owner.body, None)
            return False
        activation = (instance.id, pet.counter, pet.start)
        barrier = storage.get(owner.body)
        if barrier is None or barrier.activation != activation:
            pool = graph.graph(0x0256).node(0).field("m_amount")
            capacity = expr.to_float(pet.evaluate(pool, watch=False))
            if amount is None:
                return capacity > 0
            barrier = storage[owner.body] = Barrier(activation, max(0.0, capacity))
        if amount is None:
            return barrier.remaining > 0
        if barrier.remaining <= 0 or amount <= 0:
            return False
        absorbed = min(amount, barrier.remaining)
        barrier.remaining -= absorbed
        # Deliver the existing energy graph's authored message and damage parameter.
        # Its expression caps energy and its States own the subsequent decay delay/rate.
        entry = graph.graph(0x0242).node(5)
        message = graph.guid(entry.field("m_gameMessage.m_gameMessage"))
        runtime.send_message(script.component, message,
                             {0x2A: absorbed, 0x12: expr.Entity(source)}, owner.body)
        if barrier.remaining <= 0:
            # Reconstruction: exhaustion ends the parent duration State, following
            # its authored StopAbility/cooldown branch on the next script step.
            instance.state(11 if projected else 1).request_finish()
        return True
