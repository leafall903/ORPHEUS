"""104319 DataFlowMapping cache/provider callbacks with explicit component services."""

from dataclasses import dataclass, field

from ow174.game.script import expr, play

CLASS = 'STUStatescriptStateDataFlowMapping'
_FAILED = object()


@dataclass
class Context:
    owner: int
    component_present: bool
    namespace_present: bool
    register: object = NotImplemented
    unregister: object = NotImplemented
    special_changed: object = NotImplemented
    position: object = NotImplemented
    retired: bool = False
    component: object = None
    _providers: dict = field(default_factory=dict, init=False, repr=False)
    snapshots: object = NotImplemented

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid data-flow owner')
        if type(self.component_present) is not bool or type(self.namespace_present) is not bool:
            raise ValueError('data-flow component and namespace availability must be known')

    def retire(self):
        self.retired = True
        self._providers.clear()

    @classmethod
    def managed(cls, component, position):
        context = cls(component.entity, True, True, position=position, component=component)
        context.register = context._register_provider
        context.unregister = context._unregister_provider
        return context

    def _register_provider(self, state):
        self._providers[state] = state.mapping

    def _unregister_provider(self, state):
        self._providers.pop(state, None)

    def providers(self, mapping):
        return tuple(state for state, guid in self._providers.items()
                     if guid == mapping and state.active and not state.ending)


@dataclass(frozen=True)
class Snapshot:
    mapping: int
    value: float | expr.Entity | expr.Asset | expr.Vec3


class SnapshotRegistry:
    """Component82 indexed records, including reuse of the first empty slot."""

    def __init__(self):
        self.entries: list[Snapshot | None] = []

    def insert(self, record: Snapshot) -> int:
        for index, entry in enumerate(self.entries):
            if entry is None:
                self.entries[index] = record
                return index
        self.entries.append(record)
        return len(self.entries) - 1

    def remove(self, index: int, record: Snapshot):
        if not 0 <= index < len(self.entries) or self.entries[index] is not record:
            raise ValueError("component82 snapshot registration is stale")
        self.entries[index] = None
        while self.entries and self.entries[-1] is None:
            self.entries.pop()


def snapshot_enter(state):
    context = _context(state)
    registry = context.snapshots
    if registry is None:
        return
    if type(registry) is not SnapshotRegistry:
        _unsupported(state, "component82 snapshot registry")
    if state.snapshot_registration is not None:
        _unsupported(state, "component82 snapshot already registered")
    instance, node = state.instance, state.node
    value = play.evaluate_scalar(instance, node.field("m_5A6D4FBD"), initial=_FAILED)
    _check(state, context)
    mapping = 0 if value is _FAILED else play._asset(value)
    if not mapping:
        return
    answer = play.evaluate_scalar(instance, node.field("m_answer"), initial=_FAILED)
    _check(state, context)
    if answer is _FAILED:
        return
    if type(answer) not in (expr.Entity, expr.Asset, expr.Vec3):
        answer = expr.f32(expr.to_float(answer))
    record = Snapshot(mapping, answer)
    state.snapshot_registration = registry, registry.insert(record), record


def snapshot_exit(state):
    registration, state.snapshot_registration = state.snapshot_registration, None
    if registration is not None:
        registry, index, record = registration
        registry.remove(index, record)


def _unsupported(state, feature):
    state.instance.component.unsupported(state.instance, feature, state.node.cls, state.node)


def _check(state, context):
    instance, component = state.instance, state.instance.component
    component.ensure_valid()
    if (context.retired or context.owner != component.entity or instance.stopped
            or (context.component is not None and context.component is not component)
            or component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state
            or component.world.data_flow_context(component.entity) is not context
            or type(context.component_present) is not bool or type(context.namespace_present) is not bool):
        _unsupported(state, 'data-flow owner retired during callback')


def _context(state):
    component = state.instance.component
    context = component.world.data_flow_context(component.entity)
    if type(context) is not Context:
        _unsupported(state, 'data-flow context')
    _check(state, context)
    return context


def _call(state, context, callback, feature, *args):
    if not callable(callback):
        _unsupported(state, feature)
    result = callback(*args)
    _check(state, context)
    return result


def _value(state, context, cfg, *, watch=False):
    if cfg is None:
        return _FAILED
    result = state.instance.evaluate(cfg, state if watch else None, initial=_FAILED)
    _check(state, context)
    return result


def _remove(state, context):
    if not state.mapping:
        return
    _call(state, context, context.unregister, 'data-flow unregister', state)
    if state.mapping & 0xFFFFFFFF == 0x6B:
        _call(state, context, context.special_changed, 'data-flow special counter', -1)
    state.mapping = 0


def begin(state):
    # 9A6B60 resets the cached GUID before the shared refresh routine.
    state.mapping = 0
    refresh(state)


def refresh(state):
    """9AEB70: sample GUID, remove/register on change, then rebuild target policy."""
    context = _context(state)
    if not context.component_present:
        _unsupported(state, 'data-flow script component')
    value = _value(state, context, state.node.field('m_5A6D4FBD'), watch=True)
    mapping = 0 if value is _FAILED else play._asset(value)
    if mapping != state.mapping:
        _remove(state, context)
        if mapping:
            state.mapping = mapping  # 9AC140 commits before component registration.
            _call(state, context, context.register, 'data-flow register', state)
            if mapping & 0xFFFFFFFF == 0x6B:
                _call(state, context, context.special_changed, 'data-flow special counter', 1)
    # Registration consumers observe the prior target policy, as in the original.
    state.mapping_self, state.mapping_target = True, None
    selector = state.node.field('m_entity')
    if selector is not None and not isinstance(selector, dict):
        _unsupported(state, 'data-flow entity selector')
    if selector is None or selector.get('$') == 'STU_02C0109E':
        return
    if selector.get('$') == 'STU_469A9B36':
        state.mapping_self = False
    elif selector.get('$') == 'STU_328656A2':
        state.mapping_self = False
        value = _value(state, context, selector.get('m_609E2FBF'), watch=True)
        # 8F63C0/F65220 extracts a tag7 handle, not an EntityID.
        while type(value) is tuple:
            value = value[0] if value else None
        state.mapping_target = value if type(value) is expr.Handle else None
    else:
        _unsupported(state, 'data-flow entity selector')


def cleanup(state):
    """9A6B70: a missing component skips removal and preserves the cached GUID."""
    context = _context(state)
    if context.component_present:
        _remove(state, context)


def answer(state, kind):
    """The separate +1C0 provider interface: status4 leaves output untouched.

    kind is entity, asset, float or vector. These calls do not watch the answer;
    mapping/target dependencies belong to ordinary refresh, not provider queries.
    """
    if kind not in ('entity', 'asset', 'float', 'vector'):
        raise ValueError('unknown data-flow answer kind')
    context = _context(state)
    if not context.namespace_present:
        return 4, None
    value = _value(state, context, state.node.field('m_answer'))
    if value is _FAILED:
        value = None
    if kind == 'entity':
        return 1, expr.to_entity(value)
    if kind == 'asset':
        return 1, play._asset(value)
    if kind == 'float':
        return 1, expr.f32(expr.to_float(value))
    if type(value) is expr.Entity:
        position = _call(state, context, context.position, 'data-flow entity position', value.id)
        if position is not None and type(position) is not expr.Vec3:
            _unsupported(state, 'data-flow entity position')
        return 1, position if position is not None else expr.Vec3(0.0, 0.0, 0.0)
    while type(value) is tuple:
        value = value[0] if value else None
    return 1, value if type(value) is expr.Vec3 else expr.Vec3(0.0, 0.0, 0.0)
