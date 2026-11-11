"""Scoped server reconstruction of Reaper's authored Wraith phase."""

HERO = 0x02E0000000000002
GRAPH = 0x20
PHASE = 0  # node1, scoped by the authored three-second Wait


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
