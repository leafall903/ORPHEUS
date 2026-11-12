"""Receiver row-ID links; scalar outputs may fail through to a lower binding."""

from ow174.game.script import array_actions, expr
from ow174.game.script.runtime import Link


def _unsupported(state, feature):
    state.instance.component.unsupported(state.instance, feature, state.node.cls, state.node)


def target(state, cfg):
    if cfg is None:
        return None
    result = expr.lvalue(cfg)
    if result is not None:
        return result if result[1] else None  # Native identifier0 is no destination.
    if isinstance(cfg, dict) and cfg.get('$') in expr.CONSTANTS:
        return None
    _unsupported(state, 'message receiver output lvalue')


def _before(state, var):
    return state.message_observed[var] if var in state.message_observed else var.value()


def _changed(state, var, before):
    if state.instance.teardown:
        return  # Physical owner teardown cannot execute live getters/callbacks.
    value = state.instance.component.changed(var, before)
    state.message_observed[var] = value
    for linked in var.stack_states():
        linked.stack_changed(var)


def bind(state, slot, cfg):
    """1B749B0: a repeated row updates in place; a null target retains an old row."""
    resolved = target(state, cfg)
    if resolved is None:
        return
    var = state.instance.find_var(*resolved)
    previous = state.message_bindings.get(slot)
    if previous is not None and previous[0] is var:
        _changed(state, var, _before(state, var))
        return
    remove(state, slot)
    before = _before(state, var)
    link = Link(state, slot, stack=True, dynamic=True)
    # Equal-priority above=true, without erasing this State's other row IDs.
    at = len(var.links)
    while at and var.links[at-1].priority > link.priority:
        at -= 1
    var.links.insert(at, link)
    state.message_bindings[slot] = (var, link)
    _changed(state, var, before)


def remove(state, slot, *, value=expr.EVALUATION_FAILED):
    """1B743E0/1B74850 remove only this row; baking uses its retained destination."""
    previous = state.message_bindings.pop(slot, None)
    if previous is None:
        return
    var, link = previous
    before = _before(state, var)
    if value is not expr.EVALUATION_FAILED:
        var.base = value
        for item in tuple(var.links):
            if item.state.active:
                item.state.var_written(var)
    var.links[:] = [item for item in var.links if item is not link]
    _changed(state, var, before)
    if not any(other is var for other, _ in state.message_bindings.values()):
        state.message_observed.pop(var, None)


def write(state, cfg, value):
    state.instance.write(target(state, cfg), value)


def array(state, cfg, pairs):
    resolved = target(state, cfg)
    if resolved is None:
        return
    if state.instance.find_var(*resolved).links:
        _unsupported(state, 'message receiver linked array checkpoint')
    if any(array_actions._key(key) for key, _ in pairs):
        _unsupported(state, 'message receiver keyed type12 array checkpoint')
    state.instance.write(resolved, tuple(value for _, value in pairs))


def abandon(state):
    """Quiet physical/context retirement; no output getter or authored callback."""
    for var, link in state.message_bindings.values():
        var.links[:] = [item for item in var.links if item is not link]
    state.message_bindings.clear()
    state.message_observed.clear()
