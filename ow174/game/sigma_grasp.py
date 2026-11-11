"""Scoped projectile contributions to Sigma's authored Grasp lifecycle."""

from ow174.game.script import expr

SIGMA = 0x02E000000000023B
GRAPH = 0x1D02


def absorb(state, shooter, amount):
    from ow174.game.combat import _source_entity

    instance = state.instance
    instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), amount)
    instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(_source_entity(shooter)))
    instance.follow(state.node, "m_160B5DBE", state)
    instance.component.run_now()


def convert(fight, instance, node):
    if (node.pos != 34 or expr.guid_of(node.field("m_script.m_graph")) != 0x0580000000001D03
            or instance.evaluate(node.field("m_targets")) != expr.Entity(instance.component.entity)):
        return False
    entity = instance.component.entity
    target = fight._target_entity(entity)
    if (target is None or target.owner.hero.guid != SIGMA or target.owner.body_script is None
            or target.owner.body_script.component is not instance.component):
        return False
    state = instance.states.get(1)
    if state is None or instance.component.owner_dead:
        return True
    vitals = fight.vitals_of(target)
    activation = instance.id, state.counter
    if vitals.grasp_activation == activation:
        return True
    vitals.grasp_activation = activation
    params = {expr.guid_of(p["m_B5051BCE"]): instance.evaluate(p["m_value"])
              for p in node.field("m_params")}
    amount = expr.to_float(params[0x0A4000000000002A])
    cap = expr.to_float(params[0x0A400000000003D1])
    delay = instance.evaluate(instance.graph.node(71).field("m_timeout"))
    modify = instance.graph.node(70).field("m_modifyHealth")
    rate = instance.evaluate(modify["m_amount"])
    if vitals.grant_temporary_shields(amount, cap, instance.component.now / 1000 + delay, rate):
        fight._sync_body_health(target.owner, vitals)
    return True
