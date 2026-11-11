"""Moira's authored 0F7C phase and its reconstructed combat consumers."""

from ow174.game.script import expr

HERO = 0x02E00000000001A2
GRAPH = 0x0F7C
PROTECTION = 28  # node49 stacks the authored entity variable 6604.


def active(actor):
    if getattr(getattr(actor, 'hero', None), 'guid', None) != HERO:
        return False
    script = actor.body_script
    if (script is None or script.retired or script.component.owner_dead
            or script.component.fault is not None):
        return False
    flag = script.component.entity_var(6604, create=False)
    return (flag is not None and expr.truthy(flag.value())
            and any(state.active and not state.ending
                    for instance in script.component.instances.values() if instance.graph.index == GRAPH
                    if (state := instance.states.get(PROTECTION)) is not None))


def enter(state):
    instance = state.instance
    world = instance.component.world
    if (world.hero == HERO and instance.graph.index == GRAPH and state.index == PROTECTION
            and getattr(world, 'match', None) is not None):
        world.match.combat.cleanse_ana_effects(instance.component.entity)
