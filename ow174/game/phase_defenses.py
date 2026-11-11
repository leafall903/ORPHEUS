"""Scoped Wraith/Fade defense from authored stacks, not a universal tag engine.

Reviewed phase helpers from the isolated Torbjorn/Winston lanes. Zero-delay
entry continuation, immunity and intangible target policy are reconstructions.
The original ability graph owns input, stack lifetime and cooldown.10467 alone
is not protection: it also covers recovery. No synthetic immunity timer.
"""

from ow174.game.script import expr

PHASES = {
    0x02E0000000000002: (0x0020, 8440),
    0x02E00000000001A2: (0x0F7C, 6604),
}
CLOCK_HEROES = frozenset((*PHASES, 0x02E00000000000DD, 0x02E0000000000029, 0x02E0000000000040,
                          0x02E000000000007A, 0x02E0000000000068, 0x02E000000000013B,
                          0x02E0000000000009, 0x02E000000000013E, 0x02E000000000023B, 0x02E0000000000016))


def active(actor):
    spec = PHASES.get(getattr(getattr(actor, 'hero', None), 'guid', None))
    script = getattr(actor, 'body_script', None)
    if (spec is None or script is None or script.component.strict or script.component.owner_dead
            or script.component.entity != actor.body):
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
