"""Combat interpretation of Orisa's authored Fortify filter."""

from ow174.game.script import combat_mod_filter, expr
from ow174.game.script.combat_mod_filter import damage_scale as damage_scale

HERO = 0x02E000000000013E
GRAPH = 0x0CF4


def active_filter(actor):
    return combat_mod_filter.active_actor_filter(actor, HERO, GRAPH, 4)


def control_immune(actor):
    state = active_filter(actor)
    if state is None:
        return False
    flag = state.instance.component.entity_var(9161, create=False)
    return flag is not None and expr.truthy(flag.value())
