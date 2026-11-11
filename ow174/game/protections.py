"""Narrow resource-state protection services used by combat consumers.

Reaper Wraith and Mei Cryo-Freeze use their authored active states.
Their host entry and target exclusion are reconstructed.
"""

from ow174.game.script import expr

REAPER = 0x02E0000000000002
MEI = 0x02E00000000000DD
ORISA = 0x02E000000000013E
CRYO = 0x05FE
FORTIFY = 0x0CF4
# Explicit server missing-input policy for these supported bounded defenses.
# Other bodies retain their existing command-frontier policy.
IDLE_CLOCK_HEROES = frozenset((
    REAPER, MEI, ORISA, 0x02E0000000000029, 0x02E0000000000068, 0x02E000000000007A,
))
INPUT_CLOCK_HEROES = IDLE_CLOCK_HEROES | {0x02E0000000000009}


def _body_component(actor, hero):
    if getattr(getattr(actor, 'hero', None), 'guid', None) != hero:
        return None
    component = getattr(getattr(actor, 'body_script', None), 'component', None)
    if component is None or component.owner_dead or component.entity != actor.body:
        return None
    return component


def cryo_instance(actor):
    component = _body_component(actor, MEI)
    if component is not None:
        return next((i for i in component.instances.values() if i.graph.index == CRYO), None)
    return None


def _fortify_component(actor, index):
    component = _body_component(actor, ORISA)
    if component is not None:
        for instance in component.instances.values():
            if instance.graph.index == FORTIFY:
                state = instance.states.get(index)
                if state is not None and state.active:
                    return component
    return None


def incoming_scale(actor):
    """Consume the active Fortify combat-mod stack before health pools."""
    component = _fortify_component(actor, 5)
    return expr.to_float(component.entity_var(8243).value()) if component is not None else 1.0


def force_immune(actor):
    component = _fortify_component(actor, 9)
    return component is not None and expr.truthy(component.entity_var(9161).value())


def cryo_active(actor):
    instance = cryo_instance(actor)
    state = instance.states.get(6) if instance is not None else None
    return state is not None and state.active


def cryo_healing_sample(actor):
    """Read the activation key, elapsed milliseconds and healing per millisecond."""
    instance = cryo_instance(actor)
    state = instance.states.get(13) if instance is not None else None
    if state is None or not state.counter:
        return None
    duration = expr.to_float(instance.find_var(0, 567).value()) * 1000
    if duration <= 0:
        return None
    amount = expr.to_float(instance.component.entity_var(6578).value())
    end = instance.component.now if state.active else state.ended_at
    elapsed = max(0, min(duration, end - state.start))
    if elapsed >= duration - 1:
        elapsed = duration  # The float32 chase can finish one millisecond early.
    return (instance.id, state.counter, state.start), elapsed, amount / duration


def advance_entry(actor):
    cryo = cryo_instance(actor)
    if cryo is not None:
        state = cryo.states.get(41)
        if state is not None and state.active and state.node.cls == 'STU_54FEB21C':
            state.request_finish()
            cryo.component.run_now()
        return
    component = _body_component(actor, REAPER)
    if component is None:
        return
    for instance in tuple(component.instances.values()):
        if instance.graph.index != 0x0020:
            continue
        state = instance.states.get(1)
        if state is not None and state.active and state.node.cls == 'STU_54FEB21C':
            delay = state.evaluate(state.node.field('m_ED97A97C'), watch=False)
            if delay != 0:
                raise ValueError('unsupported Wraith entry duration')
            state.request_finish()
            component.run_now()


def intangible(actor):
    if cryo_active(actor):
        return True
    component = _body_component(actor, REAPER)
    if component is not None:
        for instance in component.instances.values():
            if instance.graph.index == 0x0020:
                state = instance.states.get(3)
                if state is not None and state.active:
                    return True
    return False
