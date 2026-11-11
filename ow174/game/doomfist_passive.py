"""Reconstructed Doomfist 0DDD temporary shield grants for confirmed hits."""

import math

from ow174.game import defenses
from ow174.game.script import expr

SHIELD_TAG = 0x0DDD
METEOR_GAIN = 21


def grant(fight, player, *, gain_node=20):
    passive = next((i for i in player.body_script.component.instances.values()
                    if i.graph.index == SHIELD_TAG), None)
    target = next((t for t in fight._targets() if t.owner is player), None)
    if passive is None or target is None:
        return
    cap = passive.find_var(expr.INSTANCE, 11218, create=False)
    if cap is None:
        return
    amount = expr.to_float(passive.evaluate(passive.graph.node(gain_node).field("m_value")))
    limit = expr.to_float(cap.value())
    delay = expr.to_float(passive.state(0).evaluate(passive.graph.node(2).field("m_timeout")))
    rate = expr.to_float(passive.state(1).evaluate(passive.graph.node(3).field("m_modifyHealth.m_amount")))
    if not all(math.isfinite(value) and value >= 0 for value in (amount, limit, delay, rate)):
        return
    vitals = fight.vitals_of(target)
    given = vitals.grant_temporary_shields(SHIELD_TAG, amount, limit)
    if given > 0:
        fight.doomfist_shields[player.body] = defenses.ShieldDecay(fight.now + delay, fight.now, rate)
        fight._sync_body_health(player, vitals)
