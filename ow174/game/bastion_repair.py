"""Host healing from Bastion's authored Self-Repair state."""

from ow174.game import ultimate
from ow174.game.script import expr

HERO = ultimate.BASTION
GRAPH = 0x0153


def selected(state):
    return (state.instance.graph.index, state.node.pos) == (GRAPH, 3)


def interrupt(component):
    if component.world.hero != HERO:
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH and instance.find_var(expr.INSTANCE, 1735).value() in (1, 2):
            # Ordinary release can wait for the minimum repair interval.
            # Control takes the graph's recovery transition immediately.
            instance.run_node(instance.graph.node(34), None)
            return


def native(world, cfg, instance):
    kind = cfg.get("$")
    if kind not in ("STU_32E6E4D8", "STU_7D7D2470"):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if entity != instance.component.entity:
        return NotImplemented
    fight = world.match.combat
    target = next((target for target in fight._targets() if target.entity == entity), None)
    if target is None:
        return 0.0
    vitals = fight.vitals_of(target)
    return vitals.total if kind == "STU_32E6E4D8" else sum(vitals.most.values())


def heal(world, state, elapsed_ms):
    from ow174.game.combat import damage_tags

    component = state.instance.component
    if world.match is None or elapsed_ms <= 0 or component.owner_dead:
        return
    recipient = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if expr.to_entity(recipient) != component.entity:
        return
    modify = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
    interval = expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False))
    if amount <= 0 or interval <= 0:
        return
    fight = world.match.combat
    target = next((target for target in fight._targets() if target.entity == component.entity), None)
    # A deferred exit can settle an interval after Hack already applied.
    if target is None or fight.tick < fight.vitals_of(target).hack_until:
        return
    given = fight._heal(target.owner, target, amount * elapsed_ms / (1000 * interval))
    ultimate.credit_healing(target.owner, target, given, damage_tags(state, modify))
