"""Scoped1D02 grasp host, not a general native health/filter/PlayScript backend.

Resource outputs and graph sum/conversion/cap/wait/rate are retained. Eye-space
boxes, per-interception direct+splash value, extra-pool transport and continuous
decay meaning are explicit reconstructions; original-client proof stays open.
"""

from ow174.game import deflection, projectile_tags
from ow174.game.script import expr
from ow174.game.transcendence import active_state
from ow174.game.world import SHIELDS_POOL

SIGMA = 0x02E000000000023B
GRAPH, STATE = 0x1D02, 1
CONVERSION, WAIT, DECAY = 34, 71, 70


def active(player):
    return active_state(player, SIGMA, GRAPH, STATE)


class SigmaGrasp:
    def __init__(self, fight):
        self.fight = fight
        self.conversions = {}
        self.last_tick = None

    def retire(self, body):
        for key in [key for key in self.conversions if key[0] == body]:
            del self.conversions[key]

    def has_bonus_shields(self):
        return any(
            (vitals := self.fight.vitals.get(body)) is not None
            and not vitals.dead and vitals.bonus_shields > 0
            for body, _ in self.conversions
        )

    def trace(self, shooter, definition, origin, direction, reach, *, implicit=False):
        if not projectile_tags.absorbable(definition, implicit=implicit):
            return None
        best = None
        for target in self.fight._enemies(shooter):
            if not hasattr(target.owner, "mover"):
                continue  # NPC-owned defensive abilities require a separate receiver service
            state = active(target.owner)
            if state is None or deflection.frontal(target, direction, state=state) is None:
                continue
            distance = deflection.box_contact(target, state, origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, target, state
        return best

    def absorb(self, state, shooter, direct, blast, distance):
        """Supply intercepted amount/source and run the authored6584->21312 sum."""
        from ow174.game.combat import _source_entity

        amount = 0.0 if direct is None else max(0.0, direct.amount * direct.scale(distance))
        if blast is not None:
            amount += max(0.0, blast.amount)
        instance = state.instance
        instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), amount)
        instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(_source_entity(shooter)))
        # Do not fabricate tag outputs or native callback arguments.
        instance.follow(state.node, "m_160B5DBE", state)
        instance.component.run_now()

    def play_script(self, body, instance, node):
        """Bridge only actual1D02/node34 ->1D03 at its synchronous action boundary."""
        if (instance.graph.index != GRAPH or node is not instance.graph.node(CONVERSION)
                or expr.guid_of(node.field("m_script.m_graph")) != 0x0580000000001D03):
            return False
        target = next((t for t in self.fight._targets() if t.entity == body
                       and getattr(getattr(t.owner, "hero", None), "guid", None) == SIGMA), None)
        if target is None or target.owner.body_script.component is not instance.component:
            return False
        wait = next((s for s in instance.states.values() if s.node.pos == WAIT), None)
        activation = ((0 if wait is None else wait.counter) + 1) & 0xFFFF
        key = body, instance.id
        if self.conversions.get(key) == activation:
            return True
        self.conversions[key] = activation
        vitals = self.fight.vitals_of(target)
        if vitals.dead or instance.component.owner_dead:
            return True
        params = {expr.guid_of(p.get("m_B5051BCE")): p["m_value"] for p in node.field("m_params")}
        amount = max(0.0, expr.to_float(instance.evaluate(params[0x0A4000000000002A])))
        cap = max(0.0, expr.to_float(instance.evaluate(params[0x0A400000000003D1])))
        vitals.bonus_shields = min(cap, vitals.bonus_shields + amount)
        vitals.bonus_shields_max = max(vitals.bonus_shields_max, vitals.bonus_shields)
        vitals.changed.add(SHIELDS_POOL)
        self.fight._sync_body_health(target.owner, vitals)
        return True

    def update(self):
        from ow174.game.combat import TICK

        fight = self.fight
        previous = fight.tick if self.last_tick is None else self.last_tick
        self.last_tick = fight.tick
        if fight.tick <= previous or not self.conversions:
            return
        current = set()
        for target in fight._targets():
            owner = target.owner
            if getattr(getattr(owner, "hero", None), "guid", None) != SIGMA:
                continue
            script = getattr(owner, "body_script", None)
            if script is None or script.retired:
                continue
            vitals = fight.vitals_of(target)
            for instance in script.component.instances.values():
                if instance.graph.index != GRAPH:
                    continue
                key = target.entity, instance.id
                current.add(key)
                if vitals.dead:
                    continue
                decay = next((s for s in instance.states.values() if s.node.pos == DECAY and s.active), None)
                if decay is not None and vitals.bonus_shields > 0:
                    # Body clocks can start on a different command epoch. Limit
                    # world catch-up to the period the state could have existed.
                    age = max(0, instance.component.now - decay.start) / 1000
                    seconds = min((fight.tick - previous) * TICK, age + TICK)
                    modify = decay.node.field("m_modifyHealth")
                    rate = max(0.0, expr.to_float(decay.evaluate(modify.get("m_amount"), watch=False)))
                    vitals.bonus_shields = max(0.0, vitals.bonus_shields - rate * seconds)
                    vitals.changed.add(SHIELDS_POOL)
                    fight._sync_body_health(owner, vitals)
        for key in set(self.conversions) - current:
            del self.conversions[key]
