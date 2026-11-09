"""Sigma Grasp's explicit temporary-shield consumer for the permissive body VM.

DATA:1D02 PlayScript1D03 parameters, cap400, captured*.6, delay3 and5/1 decay.
RECONSTRUCTED: grant at normal capture completion,5HP/s decay after3s, one merged
temporary layer. Native PlayScript hosting, separate tagged pool replication,
stacking/bypass/ultimate-source semantics and presentation remain open.
"""
from ow174.game.script import expr


def update(fight, actor):
    script = getattr(actor, 'body_script', None)
    if script is None or script.component.owner_dead:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1D02), None)
    if instance is None:
        return
    phase = instance.states.get(1)
    if phase is None:
        return
    target = next((t for t in fight._targets() if t.owner is actor), None)
    if target is None:
        return
    vitals = fight.vitals_of(target)
    now = script.component.now
    before = vitals.temporary_shields, vitals.temporary_capacity
    if not phase.active and phase.counter != vitals.grasp_counter:
        vitals.grasp_counter = phase.counter
        countdown = instance.states.get(20)
        # The abort graph can also write215=0. The VM's reached flag distinguishes
        # actual countdown completion from interruption without inventing a timer.
        if countdown is not None and countdown.reached and not countdown.interrupted:
            play = next(n for n in instance.graph.nodes if n.pos == 34)
            params = {int(row['m_B5051BCE'], 16) & 0xFFFF: instance.evaluate(row['m_value'])
                      for row in play.field('m_params') or ()}
            amount, cap = expr.to_float(params.get(0x2A)), expr.to_float(params.get(0x3D1))
            added = max(0.0, min(amount, cap - vitals.temporary_shields))
            if added > 0:
                vitals.temporary_shields += added
                vitals.temporary_capacity = vitals.temporary_shields
                wait = instance.evaluate(instance.graph.states[29].field('m_timeout'))
                vitals.grasp_decay_at = now + round(expr.to_float(wait) * 1000)
    if vitals.temporary_shields > 0 and vitals.grasp_decay_at is not None:
        elapsed = max(0, now - max(vitals.grasp_updated, vitals.grasp_decay_at))
        if elapsed:
            state = instance.graph.states[28]
            rate = expr.to_float(instance.evaluate((state.field('m_modifyHealth') or {}).get('m_amount')))
            period = expr.to_float(instance.evaluate(state.field('m_95F1FB0B')))
            if period > 0:
                vitals.temporary_shields = max(0.0, vitals.temporary_shields - rate / period * elapsed / 1000)
            if vitals.temporary_shields <= 1e-9:
                vitals.temporary_shields = vitals.temporary_capacity = 0.0
                vitals.grasp_decay_at = None
    vitals.grasp_updated = now
    if before != (vitals.temporary_shields, vitals.temporary_capacity):
        from ow174.game.world import SHIELDS_POOL

        vitals.changed.add(SHIELDS_POOL)
        fight._sync_body_health(actor, vitals)
