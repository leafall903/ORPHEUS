"""104319 33A46033 cache and entity-rule dispatch, with explicit engine inputs.

Outputs a..e retain the native8FED10 argument order. Native entity/model lookup,
capability/permission, spatial queries and engine writes belong to the owner.
None means an explicitly absent context/component; unknown services stay strict.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = "STU_33A46033"
_FAILED = object()


@dataclass
class Context:
    component: object  # Actual target's script component, or explicitly absent.
    native_flag: bool
    permitted: object = NotImplemented
    within_distance: object = NotImplemented
    write_entity: object = NotImplemented
    model_ready: object = NotImplemented
    write_model: object = NotImplemented
    route: object = NotImplemented
    extra_test: object = NotImplemented


def _unsupported(state, service):
    state.instance.component.unsupported(state.instance, service, state.node.cls, state.node)


def _call(state, callback, service, *args, boolean=False):
    if not callable(callback):
        _unsupported(state, service)
    result = callback(*args)
    _check_owner(state)
    if boolean and type(result) is not bool:
        _unsupported(state, service)
    return result


def _check_owner(state):
    instance = state.instance
    instance.component.ensure_valid()
    if (instance.stopped or instance.component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state):
        _unsupported(state, "entity control owner retired during callback")


def _value(state, name, default):
    cfg = state.node.field(name)
    if cfg is None:
        return default
    value = state.instance.evaluate(cfg, state, initial=_FAILED)
    _check_owner(state)
    return default if value is _FAILED else value


def boolean(state, name, default):
    """8F60F0: absent/failed configs retain the supplied conversion default."""
    return expr.truthy(_value(state, name, default))


def refresh_cache(state):
    """C8D540: four scalar caches followed by ordered valid entity targets."""
    state.control_cache_ready = False
    state.control_radius = expr.to_float(_value(state, "m_069FD7A5", -1.0), -1.0)
    state.control_bypass = boolean(state, "m_72FD7742", False)
    state.control_inverse_a = not boolean(state, "m_381879C1", False)
    state.control_inverse_b = not boolean(state, "m_7E42F868", False)
    state.control_targets = tuple(
        entity for value in play._iter_values(state.instance, state.node.field("m_C3E68133"), watcher=state)
        if expr.valid_entity(entity := expr.to_entity(value))
    )
    state.control_cache_ready = True


def combine(source, component, seed, within_distance):
    """8FED10: ordered type-list rules; e8/f0/f8/120 are gated,100 is always read."""
    if component is None:
        return None
    from ow174.game.script.runtime import Component

    if not isinstance(component, Component) or component.world is not source.instance.component.world:
        _unsupported(source, "entity control target component")
    component.ensure_valid()
    result = list(seed)
    registered = tuple(component.state_registries.get(CLASS, ()))
    for state in registered:
        source.instance.component.consume_work("entity control rule")
        if not state.instance.initialized:
            continue
        if not state.control_cache_ready:
            _unsupported(source, "entity control rule cache unavailable")
        near = _call(source, within_distance, "entity control distance", state, boolean=True)
        enabled = boolean(state, "m_3C443404", True)
        if not state.control_bypass and not near and enabled:
            result[0] &= not boolean(state, "m_381879C1", not result[0])
            result[1] &= not boolean(state, "m_7E42F868", not result[1])
            result[2] &= boolean(state, "m_DFD8E2EA", not result[2])
            result[4] |= boolean(state, "m_6771D268", result[4])
        result[3] |= boolean(state, "m_D82DB14B", result[3])
        if tuple(component.state_registries.get(CLASS, ())) != registered:
            _unsupported(source, "entity control registry mutation")
    return tuple(result)


def refresh(state):
    """8F93C0: cache first, optional tick, engine gates, aggregation, then consumers."""
    component = state.instance.component
    _check_owner(state)
    generation = (state.control_generation + 1) & 0xFF
    state.control_generation = 0 if generation == 255 else generation
    refresh_cache(state)
    if state.control_radius >= 0:
        state.ticks()
    context = component.world.entity_control_context(component.entity)
    _check_owner(state)
    if context is None:
        return
    if type(context) is not Context:
        _unsupported(state, "entity control context")
    if context.route is not None:
        _call(state, context.route, "entity control route", state, 6)
    native_flag = context.native_flag
    if type(native_flag) is not bool:
        _unsupported(state, "entity control native capability")
    allowed = native_flag and _call(
        state, context.permitted, "entity control permission", boolean=True,
    )
    extra_test = context.extra_test
    result = combine(state, context.component, (allowed, not allowed, True, False, False),
                     context.within_distance)
    if result is None:
        return
    _, second, third, fourth, fifth = result
    if third and not native_flag and extra_test is not None:
        second |= _call(state, extra_test, "entity control extra query", boolean=True)
    _call(state, context.write_entity, "entity control entity consumer", second)
    if _call(state, context.model_ready, "entity control model availability", boolean=True):
        previous, state.control_previous = state.control_previous, fourth
        if previous != fourth:
            _call(state, context.write_model, "entity control model consumer", state, fourth, fifth)
