"""10431991EF24A9 timer/cleanup core; component87 selection belongs to its host.

Timer1 exits120, follows128 and clears/restores the owner. Timer2 follows120
before installing the owner. No timer or target is synthesized by this module.
"""

from dataclasses import dataclass

CLASS = "STU_91EF24A9"


@dataclass
class Context:
    component: object
    current: object = None
    pending: object = None  # Opaque component87+D8; these callbacks only clear it.
    resolve: object = NotImplemented  # Saved native state handle -> live State or None.
    retired: bool = False

    def retire(self):
        self.retired = True
        self.current = self.pending = None
        self.resolve = NotImplemented


def _unsupported(state, service):
    state.instance.component.unsupported(state.instance, service, CLASS, state.node)


def _owner(state):
    instance, component = state.instance, state.instance.component
    component.ensure_valid()
    if (instance.stopped or (instance.teardown and not state.ending)
            or component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state):
        _unsupported(state, "AI behavior source lifetime")


def context(state, *, required=False):
    _owner(state)
    component = state.instance.component
    world = component.world
    result = world.ai_behavior_context(component.entity, component=component)
    _owner(state)
    if component.world is not world:
        _unsupported(state, "AI behavior world replacement")
    if result is NotImplemented:
        if required or component.strict:
            _unsupported(state, "AI behavior component87")
        return result
    if result is None:
        if required:
            _unsupported(state, "AI behavior missing component87")
        return None
    if type(result) is not Context or result.component is not component or result.retired:
        _unsupported(state, "AI behavior component87 lifetime")
    return result


def _same(state, expected):
    if context(state, required=True) is not expected:
        _unsupported(state, "AI behavior component87 replacement")


def select(state):
    state.instance.follow(state.node, "m_6F92F4BD", state)
    owner = context(state, required=True)
    owner.current = state
    owner.pending = None


def clear(state, owner):
    owner.current = None
    token = state.ai_previous
    if type(token) is not int or not 0 <= token <= 0xFFFFFFFF:
        _unsupported(state, "AI behavior saved handle")
    if not token & 0xFFFF:
        return
    if not callable(owner.resolve):
        _unsupported(state, "AI behavior saved handle lookup")
    previous = owner.resolve(token)
    _same(state, owner)
    if previous is not None:
        from ow174.game.script.runtime import State

        if (not isinstance(previous, State) or previous.node.cls != CLASS
                or previous.instance.component is not state.instance.component):
            _unsupported(state, "AI behavior saved handle result")
        _owner(previous)
        select(previous)
        _same(state, owner)
    state.ai_previous = 0
    owner.pending = None


def timer(state, parameter):
    if parameter == 1:
        state.instance.exit_subgraph(state.node, "m_6F92F4BD")
        state.instance.follow(state.node, "m_AFA8C3AE", state)
        clear(state, context(state, required=True))
    elif parameter == 2:
        select(state)


def cleanup(state):
    owner = context(state)
    if owner is NotImplemented:
        return  # Existing permissive practice-range policy has its own exit.
    if owner is not None and owner.current is state:
        clear(state, owner)
    state.instance.exit_subgraph(state.node, "m_6F92F4BD")


def detach(state):
    """Failure/object destruction drops references without invoking authored plugs."""
    state.ai_previous = 0
    component = state.instance.component
    owner = component.world.ai_behavior_context(component.entity, component=component)
    if type(owner) is Context and owner.component is component:
        if owner.current is state:
            owner.current = None
        if owner.pending is state:
            owner.pending = None
