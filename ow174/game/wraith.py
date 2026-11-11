"""Scoped Reaper Wraith bridge using the authored entry and active states."""

HERO = 0x02E0000000000002
GRAPH = 0x0020


def _instance(actor):
    script = getattr(actor, "body_script", None)
    if getattr(script, "hero", None) != HERO or script.retired or script.component.owner_dead:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == GRAPH), None)


def advance(actor):
    instance = _instance(actor)
    if instance is None:
        return
    entry = instance.states.get(1)
    if entry is not None and entry.active and entry.node.cls == "STU_54FEB21C":
        if entry.evaluate(entry.node.field("m_ED97A97C"), watch=False) != 0:
            raise ValueError("unsupported Reaper Wraith entry duration")
        entry.request_finish()
        instance.update()


def intangible(actor):
    instance = _instance(actor)
    state = instance.states.get(3) if instance is not None else None
    return state is not None and state.active
