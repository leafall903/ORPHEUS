"""Scoped1D02 grasp host, not a general native health/filter/PlayScript backend.

Resource outputs and graph sum/conversion/cap/wait/rate are retained. Eye-space
boxes, per-interception direct+splash value, extra-pool transport and continuous
decay meaning are explicit reconstructions; original-client proof stays open.
"""

import math

from ow174.game import arrow_deflection
from ow174.game.script import expr
from ow174.game.world import SHIELDS_POOL

SIGMA = 0x02E000000000023B
GRAPH, STATE = 0x1D02, 1
CONVERSION, WAIT, DECAY = 34, 71, 70


class SigmaGrasp:
    def __init__(self, fight):
        self.fight = fight
        self.conversions = {}
        self.last_tick = None

    def retire(self, body):
        for key in [key for key in self.conversions if key[0] == body]:
            del self.conversions[key]

    def trace(self, target, origin, direction, reach):
        owner = target.owner
        if getattr(getattr(owner, "hero", None), "guid", None) != SIGMA:
            return None
        state = arrow_deflection.active_volume(target, GRAPH, STATE)
        if state is None:
            return None
        from ow174.game.combat import _dot, aim

        angle = state.evaluate(state.node.field("m_025D0C39"), watch=False)
        if type(angle) not in (float, int) or not 0 < angle <= 180:
            return None
        if -_dot(aim(owner.mover.yaw, owner.mover.pitch), direction) + 1e-9 < math.cos(math.radians(angle)):
            return None
        entry = arrow_deflection.box_contact(target, state, origin, direction, reach)
        return None if entry is None else (entry, state)

    def absorb(self, state, source, direct, blast, distance):
        """Supply intercepted amount/source and run the authored6584->21312 sum."""
        amount = 0.0 if direct is None else max(0.0, direct.amount * direct.scale(distance))
        if blast is not None:
            amount += max(0.0, blast.amount)
        instance = state.instance
        instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), amount)
        instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(source))
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
        fight = self.fight
        previous = fight.tick if self.last_tick is None else self.last_tick
        self.last_tick = fight.tick
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
                    start_tick = decay.start / instance.component.frame_ms
                    seconds = max(0.0, fight.tick - max(previous, start_tick)) * .016
                    modify = decay.node.field("m_modifyHealth")
                    rate = max(0.0, expr.to_float(decay.evaluate(modify.get("m_amount"), watch=False)))
                    vitals.bonus_shields = max(0.0, vitals.bonus_shields - rate * seconds)
                    vitals.changed.add(SHIELDS_POOL)
                fight._sync_body_health(owner, vitals)
        for key in set(self.conversions) - current:
            del self.conversions[key]
