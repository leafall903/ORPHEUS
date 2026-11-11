"""Scoped Wraith/Fade defense from authored stacks, not a universal tag engine.

Reviewed phase helpers from the isolated Torbjorn/Winston lanes. Zero-delay
entry continuation, immunity and intangible target policy are reconstructions.
The original ability graph owns input, stack lifetime and cooldown.10467 alone
is not protection: it also covers recovery. No synthetic immunity timer.
"""

import math

from ow174.game.script import expr

ORISA = 0x02E000000000013E

PHASES = {
    0x02E0000000000002: (0x0020, 8440),
    0x02E00000000001A2: (0x0F7C, 6604),
}


def fortify_damage_scale(actor, entity: int) -> float:
    """Read the live received-damage filter on 0CF4/10."""
    if getattr(getattr(actor, "hero", None), "guid", None) != ORISA:
        return 1.0
    script = actor.body_script
    if (script is None or script.retired or script.component.owner_dead
            or script.component.entity != entity):
        return 1.0
    for instance in script.component.instances.values():
        state = instance.states.get(4) if instance.graph.index == 0x0CF4 else None
        if state is not None and state.active and not state.ending:
            value = state.evaluate(state.node.field("m_50530EAB"), watch=False)
            if type(value) in (int, float) and math.isfinite(value):
                return max(0.0, min(1.0, 1.0 + value))
    return 1.0


def active(actor):
    spec = PHASES.get(getattr(getattr(actor, 'hero', None), 'guid', None))
    script = getattr(actor, 'body_script', None)
    if (spec is None or script is None or script.component.strict or script.component.owner_dead
            or script.retired or script.component.entity != actor.body):
        return False
    variable = script.component.entity_var(spec[1], create=False)
    if variable is None or not expr.truthy(variable.value()):
        return False
    return any(
        state.active and not state.ending and state.node.cls == 'STUStatescriptStateStack'
        and expr.lvalue(state.node.field('m_out_Var')) == (expr.ENTITY, spec[1])
        for instance in script.component.instances.values() if instance.graph.index == spec[0]
        for state in instance.states.values()
    )


def enter(state):
    spec = PHASES.get(getattr(state.instance.component.world, 'hero', None))
    if (spec is None or state.instance.component.strict or state.instance.graph.index != spec[0]
            or state.node.pos != 2 or state.node.cls != 'STU_54FEB21C'):
        return
    if any(state.node.field(key) is not None for key in
           ('m_7C8AD485', 'm_C7A42E2A', 'm_F8CE386A', 'm_C46403A5')):
        return
    cfg = state.node.field('m_ED97A97C')
    if cfg is not None and state.evaluate(cfg, watch=False) == 0:
        state.request_finish()
