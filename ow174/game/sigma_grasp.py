"""Scoped1D02 absorption output and reconstructed1D03 temporary shield host.

Amounts/cap/delay/rate come from the active authored request. This does not run
the native HealthPool service or prove its engine/query/presentation semantics.
Reviewed against the isolated Sigma lane; never imports its unrelated services.
"""

from dataclasses import dataclass

from ow174.game.script import expr

HERO = 0x02E000000000023B
GRASP = 0x1D02
SHIELD_TAG = 0x5847


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


def absorbed(state, amount):
    if state.instance.graph.index != GRASP or state.node.state != 1:
        return
    state.instance.write((expr.INSTANCE, 6584), float(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()


def grant(fight, body, instance, node):
    if instance.graph.index != GRASP or node.pos != 34 or instance.component.strict:
        return False
    if (instance.evaluate(node.field("m_script")) != expr.Asset(0x0580000000001D03)
            or instance.evaluate(node.field("m_targets")) != expr.Entity(body)):
        return False
    target = next((target for target in fight._targets() if target.entity == body), None)
    if target is None:
        return True
    script = getattr(target.owner, "body_script", None)
    if (getattr(getattr(target.owner, "hero", None), "guid", None) != HERO
            or script is None or script.component is not instance.component
            or instance.component.entity != body):
        return False
    state = instance.states.get(1)
    if state is None or not state.counter:
        return True
    activation = instance.id, state.counter
    if fight.grasp_grants.get(body) == activation:
        return True
    fight.grasp_grants[body] = activation
    vitals = fight.vitals_of(target)
    if vitals.dead:
        return True
    params = {int(entry["m_B5051BCE"], 16): expr.to_float(instance.evaluate(entry["m_value"]))
              for entry in node.field("m_params") or ()}
    amount, limit = params[0x0A4000000000002A], params[0x0A400000000003D1]
    delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
    rate = expr.to_float(instance.evaluate(instance.graph.node(70).field("m_modifyHealth")["m_amount"]))
    if vitals.grant_temporary_shields(SHIELD_TAG, amount, limit) > 0:
        now = instance.component.now / 1000
        fight.grasp_shields[body] = ShieldDecay(now + delay, now, rate)
        fight._sync_body_health(target.owner, vitals)
    return True


def update(fight):
    owners = {target.entity: target.owner for target in fight._targets()}
    for body, decay in list(fight.grasp_shields.items()):
        vitals = fight.vitals.get(body)
        if vitals is None or vitals.dead or body not in owners:
            del fight.grasp_shields[body]
            continue
        duration = max(0.0, fight.now - max(decay.last, decay.starts))
        decay.last = max(decay.last, fight.now)
        if vitals.decay_temporary_shields(SHIELD_TAG, duration * decay.rate) > 0:
            fight._sync_body_health(owners[body], vitals)
        if vitals.temporary_shields[SHIELD_TAG].now <= 0:
            del fight.grasp_shields[body]
