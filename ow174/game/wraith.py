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


def active(actor):
    if getattr(getattr(actor, 'hero', None), 'guid', None) != HERO:
        return False
    script = actor.body_script
    if (script is None or script.retired or script.component.owner_dead
            or script.component.fault is not None):
        return False
    return any(state.active and not state.ending
               for instance in script.component.instances.values() if instance.graph.index == GRAPH
               if (state := instance.states.get(PHASE)) is not None)


def enter(state):
    instance = state.instance
    world = instance.component.world
    if world.hero != HERO or instance.graph.index != GRAPH or state.index != PHASE:
        return
    match = getattr(world, 'match', None)
    if match is None:
        return
    match.combat.cleanse_ana_effects(instance.component.entity)
