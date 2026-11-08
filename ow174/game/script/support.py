"""Reconstructed authoritative consumers for selected Mercy support effects.

The source state/effect GUID, target expression and amount parameter come from
104319. This does not replace the full engine effect owner or mount external
effect scripts, healing modifiers or chained Valkyrie effects.
"""

from ow174.game.script import expr, graph, regeneration

HEAL = 0x04A0000000000019
BOOST = 0x04A0000000000016


def boost_amount(script, target):
    """Read the active effect target and its extracted006C modifier value.

    The additive outgoing multiplier and non-stacking source policy are
    reconstructed. No mounted effect pack means no inferred damage bonus.
    """
    if script.hero != regeneration.MERCY or script.component.owner_dead:
        return 0.0
    binding = graph.effect_script(BOOST)
    definition = graph.graph(binding["graph"]) if binding else None
    if definition is None:
        return 0.0
    setters = [node for node in definition.nodes if node.cls == "STUStatescriptActionSetVar"
               and expr.lvalue(node.field("m_out_Var")) == (graph.INSTANCE, 174)]
    if len(setters) != 1:
        return 0.0
    for instance in script.component.instances.values():
        if instance.graph.index != 0x006B:
            continue
        for state in instance.states.values():
            if not state.active or state.node.cls != "STU_431F3FCC":
                continue
            effect = state.evaluate(state.node.field("m_A76A8CF6"), watch=False)
            recipient = state.evaluate(state.node.field("m_targets"), watch=False)
            if (isinstance(effect, expr.Asset) and effect.guid == BOOST
                    and recipient == expr.Entity(target)):
                return max(0.0, expr.to_float(state.evaluate(setters[0].field("m_value"), watch=False)))
    return 0.0


def healing_reports(script, frame):
    if script.hero != regeneration.MERCY or script.component.owner_dead:
        return ()
    reports = []
    for instance in script.component.instances.values():
        if instance.graph.index != 0x006B:
            continue
        for state in instance.states.values():
            if not state.active or state.node.cls != "STU_431F3FCC":
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
                reports.append((frame, target.id, rate * script.frame_ms / 1000.0))
    return reports
