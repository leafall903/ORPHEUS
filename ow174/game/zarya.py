"""Reconstructed Zarya combat services driven by the target build's authored States.

This supplies damage interception for the personal barrier only. Pet entity hosting,
projected targeting, control immunity and original-client presentation remain open.
"""

from dataclasses import dataclass

from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000068


@dataclass
class Barrier:
    activation: tuple
    remaining: float


class PersonalBarriers:
    def __init__(self):
        self.active: dict[int, Barrier] = {}

    def retire(self, body: int) -> None:
        self.active.pop(body, None)

    def intercept(self, owner, amount: float, source: int) -> bool:
        """Consume one hostile damage event while the authored personal pet is active.

        Reconstructed policy: the event that breaks the barrier is blocked in full;
        only remaining barrier HP contributes to absorbed-damage energy. A broken
        activation cannot refill until a different authored pet activation starts.
        """
        if getattr(getattr(owner, "hero", None), "guid", None) != HERO:
            return False
        script = owner.body_script
        if script is None or script.component.owner_dead:
            return False
        instance = next((i for i in script.component.instances.values()
                         if i.graph.index == 0x0244), None)
        if instance is None:
            return False
        pet = instance.state(2)
        if not pet.active:
            self.retire(owner.body)
            return False
        activation = (instance.id, pet.counter, pet.start)
        barrier = self.active.get(owner.body)
        if barrier is None or barrier.activation != activation:
            pool = graph.graph(0x0256).node(0).field("m_amount")
            capacity = expr.to_float(pet.evaluate(pool, watch=False))
            barrier = self.active[owner.body] = Barrier(activation, max(0.0, capacity))
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
            instance.state(1).request_finish()
        return True
