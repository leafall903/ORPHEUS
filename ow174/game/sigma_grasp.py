"""Sigma's authored Grasp absorption and temporary shield request."""

from dataclasses import dataclass

from ow174.game.script import expr, graph

GRASP = 0x1D02
SHIELD_TAG = 0x5847


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


def absorbed(state, amount):
    """Feed the actual 1D02 absorption output and its authored accumulator plug.

    6584 is the per-hit amount; the authored node32 adds it to21312. Do not grant
    health or bypass the authored end/PlayScript path here. The declared
    reconstructed shield request is handled separately; native pool services
    and original-client presentation remain open.
    """
    state.instance.write((graph.INSTANCE, 6584), float(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()


def grant_shields(combat, entity, instance, node):
    """Explicit reconstruction of 1D02/node34's self-only 1D03 shield request.

    Permissive bodies otherwise skip PlayScript. Consume its actual amount/cap
    arguments and authored decay fields; do not pretend the native engine host
    or 1D03 HealthPool State has executed. Native pool queries, cap stacking and
    presentation still need comparison with the original engine.
    """
    if instance.graph.index != GRASP or node.pos != 34:
        return False
    resource = instance.evaluate(node.field("m_script"))
    target = instance.evaluate(node.field("m_targets"))
    if resource != expr.Asset(0x0580000000001D03) or target != expr.Entity(entity):
        return False
    owner = next((target for target in combat._targets() if target.entity == entity), None)
    if owner is None:
        return True
    script = owner.owner.body_script
    if instance.component.entity != entity or script is None or script.component is not instance.component:
        return False
    absorption = instance.states.get(1)
    if absorption is None or absorption.counter == 0:
        return True
    activation = instance.id, absorption.counter
    if combat.grasp_grants.get(entity) == activation:
        return True
    combat.grasp_grants[entity] = activation
    vitals = combat.vitals_of(owner)
    if vitals.dead:
        return True
    params = {graph.guid(entry["m_B5051BCE"]): expr.to_float(instance.evaluate(entry["m_value"]))
              for entry in node.field("m_params") or []}
    amount, limit = params[0x0A4000000000002A], params[0x0A400000000003D1]
    delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
    modify = instance.graph.node(70).field("m_modifyHealth")
    rate = expr.to_float(instance.evaluate(modify["m_amount"]))
    if vitals.grant_temporary_shields(SHIELD_TAG, amount, limit) > 0:
        now = instance.component.now / 1000
        combat.grasp_shields[entity] = ShieldDecay(now + delay, now, rate)
        combat._sync_body_health(owner.owner, vitals)
    return True


def update_shields(combat):
    """Elapsed-time decay without hit notices/healing credit or base shield loss."""
    if not combat.grasp_shields:
        return
    owners = {target.entity: target.owner for target in combat._targets()}
    for entity, decay in list(combat.grasp_shields.items()):
        vitals = combat.vitals.get(entity)
        if vitals is None or vitals.dead or entity not in owners:
            del combat.grasp_shields[entity]
            continue
        duration = max(0.0, combat.now - max(decay.last, decay.starts))
        decay.last = max(decay.last, combat.now)
        if vitals.decay_temporary_shields(SHIELD_TAG, duration * decay.rate) > 0:
            combat._sync_body_health(owners[entity], vitals)
        if vitals.temporary_shields[SHIELD_TAG].now <= 0:
            del combat.grasp_shields[entity]
