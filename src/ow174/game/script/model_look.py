"""104319 7D71FDF3 model-look scope callbacks and tick, with explicit engine services.

Native namespace, fallback target policy, global entity lookup and model consumers
belong to the owner. Scope history is separate from ordinary State begin/end.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = "STU_7D71FDF3"
INVALID_TARGET = 0xFFFFFFFF
_FAILED = object()


@dataclass(frozen=True)
class Target:
    entity: int
    supports_look: bool

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError("model-look target needs a native uint32 identity")
        if type(self.supports_look) is not bool:
            raise ValueError("model-look capability must be known")


@dataclass
class Context:
    owner: int
    namespace_present: bool
    lookup: object = NotImplemented
    fallback_policy: object = NotImplemented
    fallback: object = NotImplemented
    resolve_cached: object = NotImplemented
    apply: object = NotImplemented
    remove: object = NotImplemented
    exit_suppressed: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not expr.valid_entity(self.owner)
                or not 0 <= self.owner <= 0xFFFFFFFF):
            raise ValueError("invalid model-look owner")
        if type(self.namespace_present) is not bool:
            raise ValueError("model-look namespace availability must be known")

    def retire(self):
        self.retired = True


def _unsupported(state, service):
    state.instance.component.unsupported(state.instance, service, state.node.cls, state.node)


def _check(state, context):
    instance = state.instance
    component = instance.component
    component.ensure_valid()
    if (instance.stopped or instance.teardown or component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state or context.retired
            or context.owner != component.entity or type(context.namespace_present) is not bool
            or component.world.model_look_context(component.entity) is not context):
        _unsupported(state, "model look owner retired during callback")


def _context(state):
    component = state.instance.component
    context = component.world.model_look_context(component.entity)
    if type(context) is not Context or context.owner != component.entity:
        _unsupported(state, "model look context")
    _check(state, context)
    return context


def _call(state, context, callback, feature, *args, boolean=False):
    if not callable(callback):
        _unsupported(state, feature)
    result = callback(*args)
    _check(state, context)
    if boolean and type(result) is not bool:
        _unsupported(state, feature)
    return result


def _value(state, context, name):
    cfg = state.node.field(name)
    if cfg is None:
        return _FAILED
    result = state.instance.evaluate(cfg, state, initial=_FAILED)
    _check(state, context)
    return result


def _first(value):
    while type(value) is tuple:
        value = value[0] if value else None
    return value


def _target_id(state, context):
    # F64C20 copies any Entity value, including nonzero invalid identities.
    # Only exactly zero takes the fallback path here; do not use valid_entity.
    value = _first(_value(state, context, "m_BBBF2499"))
    identifier = value.id if type(value) is expr.Entity else 0
    if identifier != 0:
        return identifier & 0xFFFFFFFF
    policy = _call(state, context, context.fallback_policy, "model look fallback policy", state, boolean=True)
    # Native evaluates the same config again and F65220 extracts only tag7.
    value = _first(_value(state, context, "m_BBBF2499"))
    handle = value if type(value) is expr.Handle else None
    identifier = _call(state, context, context.fallback, "model look fallback target", state, handle, policy)
    if type(identifier) is not int or not 0 <= identifier <= 0xFFFFFFFF:
        _unsupported(state, "model look fallback target")
    return identifier


def _lookup(state, context, identifier, *, cached=False):
    callback = context.resolve_cached if cached else context.lookup
    result = _call(state, context, callback, "model look cached target" if cached else "model look target",
                   identifier)
    if result is not None and type(result) is not Target:
        _unsupported(state, "model look target")
    return result


def _selected(state, context, identifier):
    target = _lookup(state, context, identifier) if context.namespace_present else None
    if target is None or not target.supports_look:
        from ow174.game.script.runtime import ABORT

        state.request(ABORT)
        return None
    return target


def enter(state):
    """9AAFA0: enable ticking, resolve target, cache identity, evaluate/apply look."""
    state.ticks()  # Native 1B72D80(true) precedes target/config/service work.
    context = _context(state)
    identifier = _target_id(state, context)
    target = _selected(state, context, identifier)
    if target is None:
        return
    state.look_target = target.entity
    value = _value(state, context, "m_CC4BEAB2")
    look = play._asset(value) if value is not _FAILED else 0
    if _call(state, context, context.apply, "model look apply", state, target, look, boolean=True):
        state.look = look


def refresh(state):
    """9AB7C0: sample look first; changed targets require a valid old target."""
    context = _context(state)
    value = _value(state, context, "m_CC4BEAB2")
    look = play._asset(value) if value is not _FAILED else 0
    identifier = _target_id(state, context)
    target = _selected(state, context, identifier)
    if target is None or (look == state.look and target.entity == state.look_target):
        return
    previous = _lookup(state, context, state.look_target, cached=True)
    if previous is None or not previous.supports_look:
        return
    state.look_target = target.entity  # Native commits this before remove/apply.
    _call(state, context, context.remove, "model look remove", state, previous, state.look)
    if _call(state, context, context.apply, "model look apply", state, target, look, boolean=True):
        state.look = look


def exit(state):
    """9AB6D0: instance318 bit6 skips all work; otherwise reset identity only."""
    context = _context(state)
    if _call(state, context, context.exit_suppressed, "model look exit flags", state, boolean=True):
        return
    if context.namespace_present:
        previous = _lookup(state, context, state.look_target, cached=True)
        if previous is not None:  # This path has no capability4 test.
            _call(state, context, context.remove, "model look remove", state, previous, state.look)
    state.look_target = INVALID_TARGET
