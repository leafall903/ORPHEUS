"""Scoped combat protection for the authored Cryo-Freeze lifetime."""

from ow174.game.script import expr
from ow174.game.transcendence import active_state

HERO = 0x02E00000000000DD
GRAPH = 0x05FE


def active(actor):
    return active_state(actor, HERO, GRAPH, 6) is not None


def advance_entry(actor):
    state = active_state(actor, HERO, GRAPH, 41)
    if (state is not None and not state.instance.component.strict
            and state.node.cls == "STU_54FEB21C"):
        state.request_finish()
        state.instance.update()


def healing_sample(actor):
    """Sample the authored total and duration with continuous server integration."""
    script = actor.body_script
    if actor.hero.guid != HERO or script is None or script.retired:
        return None
    component = script.component
    if component.owner_dead or component.fault:
        return None
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    state = instance.states.get(13) if instance is not None else None
    if state is None or not state.counter:
        return None
    end = component.now if state.active else state.ended_at
    if end is None:
        return None
    duration = expr.to_float(instance.find_var(0, 567).value()) * 1000.0
    amount = expr.to_float(component.entity_var(6578).value())
    if duration <= 0 or amount <= 0:
        return None
    elapsed = max(0.0, min(duration, end - state.start))
    # The authored float32 ChaseVar can finish within 1ms of its endpoint.
    if elapsed >= duration - 1.0:
        elapsed = duration
    return (instance.id, state.counter, state.start), amount * elapsed / duration
