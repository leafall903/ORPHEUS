"""Reconstructed authoritative consumers for selected Mercy support effects.

The source state/effect GUID, target expression and amount parameter come from
104319. With an explicit pack the support-effect owner runs original effect
graphs. Neutral/default host inputs and filters are reconstructed; complete
native status modifiers and effect ownership/transport remain open.
"""

from ow174.game.script import expr, graph, regeneration

HEAL = 0x04A0000000000019
BOOST = 0x04A0000000000016


def healing_tags(state):
    """Evaluate original ModifyHealth tags in that effect's real source context."""
    return modify_health_tags(state, state.node.field("m_modifyHealth") or {})


def modify_health_tags(state, modify):
    """Resolve the authored tag rows in the state that owns the health change."""
    tags = []

    def collect(value):
        if isinstance(value, expr.Asset) and value.guid:
            if value not in tags:
                tags.append(value)
        elif isinstance(value, tuple):
            for item in value:
                collect(item)

    for row in modify.get("m_148F3152") or ():
        collect(state.evaluate(row.get("m_EE3055BD"), watch=False))
    return tuple(tags)


def interrupt_on_stun(script, state):
    """Preserve Mercy10DA's autonomous timed transformation on control loss.

    Reconstructed server policy: its Ability9 has no hold/continue/continuing
    stack gate; released input must not end its authored15s subgraph. This does
    not claim native224E execution or general interruptibility of other ults.
    """
    return not (script.hero == regeneration.MERCY and state.instance.graph.index == 0x10DA
                and state.index == 9 and state.buttons()[1] == 0
                and state.node.field("m_F495731F") is None
                and state.node.field("m_343E99EE") is None)


def boost_amount(script, target, *, frame=None):
    """Read the actual active006C/10DD CombatModFilter's value174.

    The additive outgoing multiplier and non-stacking source policy are
    reconstructed. No mounted effect pack means no inferred damage bonus.
    """
    if script.hero != regeneration.MERCY or script.component.owner_dead:
        return 0.0
    binding = graph.effect_script(BOOST)
    definition = graph.graph(binding["graph"]) if binding else None
    if definition is None:
        return 0.0
    match = getattr(script.world, "match", None)
    return match.combat.support_effects.boost(script.entity, target, frame=frame) if match is not None else 0.0


def healing_reports(script, frame):
    if script.hero != regeneration.MERCY or script.component.owner_dead:
        return ()
    match = getattr(script.world, "match", None)
    handled, reports = (match.combat.support_effects.advance(script, frame)
                        if match is not None else (set(), []))
    for instance in script.component.instances.values():
        if instance.graph.index != 0x006B:
            continue
        for state in instance.states.values():
            if not state.active or state.node.cls != "STU_431F3FCC":
                continue
            if (instance.id, state.index) in handled:
                continue
            definition = state.evaluate(state.node.field("m_A76A8CF6"), watch=False)
            if not isinstance(definition, expr.Asset) or definition.guid != HEAL:
                continue
            target = state.evaluate(state.node.field("m_targets"), watch=False)
            if not isinstance(target, expr.Entity):
                continue
            rate = 0.0
            for parameter in state.node.field("m_params") or ():
                if expr.guid_of(parameter.get("m_B5051BCE")) & 0xFFFF == 0x002A:
                    rate = expr.to_float(state.evaluate(parameter.get("m_value"), watch=False))
            if rate > 0:
                # Missing effect definition has no evaluated ModifyHealth tags.
                reports.append((frame, target.id, rate * script.frame_ms / 1000.0, None))
    return reports
