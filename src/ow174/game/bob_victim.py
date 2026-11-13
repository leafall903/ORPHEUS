"""Static delivery of the authored B.O.B. uppercut victim graph."""

from ow174.game.mover import IN_AIR
from ow174.game.script import expr, graph

GRAPH = 0x17F6
AIRBORNE, GROUNDED = 417, 545


def active(component):
    return not component.strict and any(
        instance.graph.index == GRAPH for instance in component.instances.values()
    )


def _pose(component, airborne):
    component.set(AIRBORNE, airborne)
    component.set(GROUNDED, not airborne)


def sample(owner, component, *, initialize=False):
    """Keep the pose inputs current after the victim bridge initializes them."""
    grounded = component.entity_var(GROUNDED, create=False)
    if not component.strict and (initialize or grounded is not None and type(grounded.base) is bool):
        _pose(component, _airborne(owner))


def _airborne(owner):
    from ow174.game.bots import Bot

    if isinstance(owner, Bot):
        return bool(getattr(owner, "movement_flags", 0) & IN_AIR)
    return owner.mover.airborne


def start(fight, source, owner, charge_graph, *, instigator):
    from ow174.game.combat import _source_entity
    from ow174.game.script.bot import BotScript

    script = getattr(owner, "body_script", None)
    component = getattr(script, "component", None)
    if (charge_graph is None or component is None or component.strict or component.owner_dead
            or component.entity != _source_entity(owner) or component.fault is not None
            or getattr(script, "retired", False)):
        return False
    health = charge_graph.node(17)
    action = charge_graph.node(60)
    if (health is None or action is None or action.cls != "STUStatescriptActionPlayScript"
            or action.field("m_6D7701B7") is not None
            or (action.pos, "m_inPlug") not in health.links("m_onBeginPlug")
            or action.field("m_targets") != health.field("m_FA3ECC43")):
        return False
    resource = expr.guid_of(action.field("m_script.m_graph"))
    definition = graph.graph(GRAPH)
    if resource != 0x05800000000017F6 or definition is None:
        return False
    if isinstance(script, BotScript) and script.frame < fight.tick - 1:
        script.advance_live(fight.tick - 1, owner.position)
    _pose(component, _airborne(owner))
    instance = component.play_loaded_graph(
        definition, source=source, instigator=instigator,
        policy=action.field("m_AA4DE426"),
    )
    if instance is None:
        return False
    component.run_now()
    # In 17F6 the movement state is entered only through the scalar reset action.
    movement = instance.states.get(definition.node(1).state)
    return movement is not None and movement.active
