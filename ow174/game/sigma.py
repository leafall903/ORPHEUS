"""Reconstructed Sigma grasp interaction, driven by actual1D02 graph states.

Resource facts: two-second ChaseVar, per-interception output6584, sum21312,
conversion0.6 via node34, cap11218, three-second Wait71 then ModifyHealth70.
This hosts the missing interception/extra-pool/decay services; it does not
implement generic PlayScript/HealthPool or claim original-client acceptance.
"""

from ow174.game.script import expr, runtime
from ow174.game.world import SHIELDS_POOL


class Sigma:
    def __init__(self, combat):
        self.combat = combat
        self.conversions = {}
        self.last_tick = None

    def retire(self, body):
        for key in [key for key in self.conversions if key[0] == body]:
            del self.conversions[key]

    def absorb(self, state, rocket, distance):
        """Supply authored per-hit output and let the actual graph accumulate it.

        Reconstructed absorbed amount: direct falloff damage plus splash payload,
        without a fabricated critical hit or victim-side Discord amplification.
        Interception output source is the current projectile damage owner.
        """
        amount = 0.0
        if rocket.direct is not None:
            amount += max(0.0, rocket.direct.amount * rocket.direct.scale(distance))
        if rocket.blast is not None:
            amount += max(0.0, rocket.blast.amount)
        self.record_absorption(state, rocket.shooter, amount)

    def record_absorption(self, state, shooter, amount):
        """Publish the absorbed amount and current damage owner to the authored graph."""
        from ow174.game.combat import _source_entity

        instance = state.instance
        instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), amount)
        source = expr.Entity(_source_entity(shooter))
        instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), source)
        # Projectile tags are not fabricated. The numeric output and its authored
        # sum path are hosted; general native tag/filter and event semantics remain open.
        instance.follow(state.node, "m_160B5DBE", state)
        instance.component.run_now()

    def update(self):
        fight = self.combat
        previous = fight.tick if self.last_tick is None else self.last_tick
        self.last_tick = fight.tick
        current = set()
        for target in fight._targets():
            script = getattr(target.owner, "body_script", None)
            component = getattr(script, "component", None)
            if not isinstance(component, runtime.Component):
                continue
            vitals = fight.vitals_of(target)
            for instance in component.instances.values():
                if instance.graph.index != 0x1D02:
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
                fight._sync_body_health(target.owner, vitals)
        for key in set(self.conversions) - current:
            del self.conversions[key]
