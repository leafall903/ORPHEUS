"""Scoped host bridge for Zen's authored Transcendence damage immunity."""

HERO = 0x02E0000000000020


def active_state(owner, index):
    script = getattr(owner, "body_script", None)
    if (getattr(getattr(owner, "hero", None), "guid", None) != HERO
            or script is None or script.retired or script.component.owner_dead):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == 0x017B:
            state = instance.states.get(index)
            if state is not None and state.active and not state.ending:
                return state
    return None


def advance_entry(owner):
    """Complete the output-free zero-duration callback to enter the authored Wait."""
    state = active_state(owner, 13)
    if (state is not None and state.node.cls == "STU_54FEB21C"
            and state.evaluate(state.node.field("m_ED97A97C"), watch=False) == 0):
        state.request_finish()
        state.instance.update()


def invulnerable(owner):
    return active_state(owner, 3) is not None
