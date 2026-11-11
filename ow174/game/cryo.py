"""Selected 104319 Mei Cryo protection, driven by the original duration state."""

HERO = 0x02E00000000000DD
GRAPH = 0x05FE


def resolve_gate(state):
    """Complete the unresolved startup gate in permissive server combat."""
    if (state.instance.graph.index == GRAPH and state.index == 41
            and state.node.cls == "STU_54FEB21C"):
        state.request_finish()


def protected(actor):
    if getattr(getattr(actor, "hero", None), "guid", None) != HERO:
        return False
    script = actor.body_script
    if script is None or script.retired or script.component.owner_dead:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            state = instance.states.get(6)
            if state is not None and state.active and not state.ending:
                return True
    return False
