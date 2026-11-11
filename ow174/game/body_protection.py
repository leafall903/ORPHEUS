"""Combat protection backed by the active stock body graph and stack."""

from ow174.game import bots, reaper, ultimate
from ow174.game.script import expr

BODY_PROTECTION = {
    0x02E00000000000DD: (0x05FE, 14, "cryo"),
    0x02E0000000000002: (0x0020, 8, "phase"),
    0x02E00000000001A2: (0x0F7C, 7, "phase"),
}


def active(owner):
    """Read the live protection stack of a supported player ability."""
    if isinstance(owner, bots.Bot):
        return None
    protection = BODY_PROTECTION.get(owner.hero.guid)
    script = owner.body_script
    if protection is None or script is None:
        return None
    component = script.component
    if component.strict or component.owner_dead or component.entity != owner.body:
        return None
    if owner.hero.guid == reaper.HERO and any(
        instance.graph.index == reaper.SHADOW_GRAPH
        and (state := instance.states.get(34)) is not None and state.active
        for instance in component.instances.values()
    ):
        return "phase"
    flag = component.entity_var(2575, create=False)
    if flag is None or not expr.truthy(flag.value()):
        return None
    graph_index, state_index, mode = protection
    for instance in component.instances.values():
        if instance.graph.index == graph_index:
            state = instance.states.get(state_index)
            if state is not None and state.active:
                return mode
    return None


def fortify(owner):
    """Return the active 0CF4 damage filter on the current Orisa body."""
    if isinstance(owner, bots.Bot) or owner.hero.guid != 0x02E000000000013E:
        return None
    script = owner.body_script
    if script is None or script.retired:
        return None
    component = script.component
    if component.strict or component.owner_dead or component.entity != owner.body:
        return None
    for instance in component.instances.values():
        if instance.graph.index == 0x0CF4:
            state = instance.states.get(4)
            if state is not None and state.active:
                return state
    return None


def fortify_blocks_control(owner):
    state = fortify(owner)
    if state is None:
        return False
    flag = state.instance.component.entity_var(9161, create=False)
    return flag is not None and expr.truthy(flag.value())


def ironclad(owner):
    """Return the active 012F damage filter on the current Bastion body."""
    if isinstance(owner, bots.Bot) or owner.hero.guid != ultimate.BASTION:
        return None
    script = owner.body_script
    if script is None or script.retired:
        return None
    component = script.component
    if component.strict or component.owner_dead or component.entity != owner.body:
        return None
    for instance in component.instances.values():
        if instance.graph.index == 0x012F:
            return next(
                (state for state in instance.states.values() if state.node.pos == 247 and state.active), None
            )
    return None
