"""Reconstructed Sigma grasp interaction, driven by actual1D02 graph states.

Resource facts: two-second ChaseVar, per-interception output6584, sum21312,
conversion0.6 via node34, cap11218, three-second Wait71 then ModifyHealth70.
This hosts the missing interception/extra-pool/decay services; it does not
implement generic PlayScript/HealthPool or claim original-client acceptance.
"""

from ow174.game import matrix
from ow174.game.script import expr, runtime
from ow174.game.world import SHIELDS_POOL

BODY, GRAPH = 0x0400000000002AB1, 0x1D02


def intercept(player, origin, direction, reach, anchor, forward):
    if sum(a * b for a, b in zip(direction, forward, strict=True)) >= 0:
        return None
    return matrix.active_boxes(player, origin, direction, reach, anchor, forward, GRAPH)


class Sigma:
    def __init__(self, combat):
        self.combat = combat
        self.conversions = {}
        self.last_tick = None

    def retire(self, body):
        for key in [key for key in self.conversions if key[0] == body]:
            del self.conversions[key]

    def absorb(self, state, amount, source):
        """Supply the payload to the authored per-hit and sum outputs."""
        instance = state.instance
        instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), amount)
        instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(source))
        instance.follow(state.node, "m_160B5DBE", state)
        instance.component.run_now()

    def update(self):
        fight = self.combat
        previous = fight.tick if self.last_tick is None else self.last_tick
        self.last_tick = fight.tick
        current = set()
        for target in fight._targets():
            if getattr(getattr(target.owner, "hero", None), "body", None) != BODY:
                continue
            script = getattr(target.owner, "body_script", None)
            if script is None or script.retired or script.component.fault is not None:
                continue
            component = getattr(script, "component", None)
            if not isinstance(component, runtime.Component):
                continue
            vitals = fight.vitals_of(target)
            for instance in component.instances.values():
                if instance.graph.index != GRAPH:
                    continue
                key = (target.entity, instance.id)
                current.add(key)
                if vitals.dead:
                    continue
                # PlayScript34's authored successor Wait71 is a causal conversion
                # boundary. Aborted/dead casts that never reach it grant nothing.
                wait = next((s for s in instance.states.values() if s.node.pos == 71), None)
                if wait is not None and wait.counter != self.conversions.get(key, 0):
                    self.conversions[key] = wait.counter
                    action = instance.graph.node(34)
                    amount_cfg = next(p["m_value"] for p in action.field("m_params")
                                      if expr.guid_of(p.get("m_B5051BCE")) & 0xFFFF == 0x2A)
                    amount = max(0.0, expr.to_float(instance.evaluate(amount_cfg)))
                    cap = max(0.0, expr.to_float(instance.find_var(0, 11218).value()))
                    vitals.bonus_shields = min(cap, vitals.bonus_shields + amount)
                    vitals.bonus_shields_max = max(vitals.bonus_shields_max, vitals.bonus_shields)
                    vitals.changed.add(SHIELDS_POOL)
                decay = next((s for s in instance.states.values() if s.node.pos == 70 and s.active), None)
                if decay is not None and vitals.bonus_shields > 0:
                    # Reconstructed rate interpretation: authored ModifyHealth70
                    # amount is shields/second, confined to this extra pool.
                    start_tick = decay.start / instance.component.frame_ms
                    seconds = max(0.0, fight.tick - max(previous, start_tick)) * 0.016
                    modify = decay.node.field("m_modifyHealth")
                    rate = max(0.0, expr.to_float(decay.evaluate(modify.get("m_amount"), watch=False)))
                    vitals.bonus_shields = max(0.0, vitals.bonus_shields - rate * seconds)
                    vitals.changed.add(SHIELDS_POOL)
                if SHIELDS_POOL in vitals.changed:
                    fight._sync_body_health(target.owner, vitals)
        for key in set(self.conversions) - current:
            del self.conversions[key]
