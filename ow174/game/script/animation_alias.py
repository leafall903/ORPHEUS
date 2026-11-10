"""104319 component55 alias requests, with explicit allocation/engine services.

The ordered handle list and State begin/cleanup are modeled. Notification,
selection of the current alias and animation playback remain owner services.
Unknown component availability never means an absent component.
"""

from ow174.game.script import expr, play

CLASS = "STU_B1FA9C2A"
_FAILED = object()


class Aliases:
    """Native18/20 rows,68 current alias,70 counter,74 mode; no implicit renderer.

    ``capacity`` declares known writable row storage. ``reserve(needed)`` supplies
    a new capacity, or None on allocation failure. Native push still consumes a
    handle and may notify on failure. ``notify()`` represents BD6C50; it does not
    implicitly choose the last alias or update current.
    """

    def __init__(self, owner, *, capacity, current=0, counter=0, mode=False,
                 reserve=NotImplemented, notify=NotImplemented):
        if type(owner) is not int or not 0 <= owner <= 0xFFFFFFFF or not expr.valid_entity(owner):
            raise ValueError("invalid animation alias owner")
        if type(capacity) is not int or not 0 <= capacity <= 0x3FFFFFFF:
            raise ValueError("invalid animation alias capacity")
        if type(current) is not int or not 0 <= current <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("invalid current animation alias")
        if type(counter) is not int or not 0 <= counter <= 0xFFFFFFFF or type(mode) is not bool:
            raise ValueError("invalid animation alias counter/mode")
        self.owner = owner
        self.rows = []
        self.capacity = capacity
        self.current = current
        self.counter = counter
        self.mode = mode
        self.reserve = reserve
        self.notify = notify
        self.retired = False

    def retire(self):
        """Local lifetime/failure containment; never execute an engine callback."""
        self.retired = True
        self.rows.clear()

    def changed(self, state):
        before = tuple(self.rows)
        _call(state, self, self.notify, "animation alias notification")
        if tuple(self.rows) != before:
            _unsupported(state, "animation alias notification changed registry")

    def push(self, state, alias):
        """BD6A80: consume uint32 handle, append if storage permits, then compare."""
        self.counter = (self.counter + 1) & 0xFFFFFFFF
        handle = self.counter  # Retain the sample across allocation/notification callbacks.
        needed = len(self.rows) + 1
        available = needed <= self.capacity
        if not available:
            before = tuple(self.rows)
            capacity = _call(state, self, self.reserve, "animation alias allocation", needed)
            if tuple(self.rows) != before:
                _unsupported(state, "animation alias allocation changed registry")
            if capacity is not None:
                if type(capacity) is not int or not needed <= capacity <= 0x3FFFFFFF:
                    _unsupported(state, "animation alias allocation capacity")
                self.capacity = capacity
                available = True
        if available:
            self.rows.append((handle, alias))
        if alias != self.current:
            self.changed(state)
        return handle

    def remove(self, state):
        """BD6900: last matching handle; a successful removal keeps State1C0."""
        for index in range(len(self.rows) - 1, -1, -1):
            if self.rows[index][0] == state.alias_handle:
                last = index == len(self.rows) - 1
                self.rows.pop(index)
                if last and (self.rows[-1][1] if self.rows else 0) != self.current:
                    self.changed(state)
                return
        state.alias_handle = 0


def _unsupported(state, feature):
    state.instance.component.unsupported(state.instance, feature, state.node.cls, state.node)


def _check(state, backend):
    instance = state.instance
    component = instance.component
    component.ensure_valid()
    if (instance.stopped or component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state or backend.retired
            or component.world.animation_aliases(component.entity) is not backend):
        _unsupported(state, "animation alias owner retired during callback")


def _call(state, backend, callback, feature, *args):
    if not callable(callback):
        _unsupported(state, feature)
    result = callback(*args)
    _check(state, backend)
    return result


def _backend(state):
    component = state.instance.component
    backend = component.world.animation_aliases(component.entity)
    if backend is None:
        return None
    if type(backend) is not Aliases or backend.owner != component.entity:
        _unsupported(state, "animation alias component")
    _check(state, backend)
    return backend


def _value(state, backend, field, default):
    cfg = state.node.field(field)
    if cfg is None:
        return default
    value = state.instance.evaluate(cfg, state, initial=_FAILED)
    _check(state, backend)
    return default if value is _FAILED else value


def begin(state):
    """CBA6B0: sample component, write mode, then alias request or queued abort."""
    state.alias_handle = 0
    backend = _backend(state)
    if backend is None:
        return
    backend.mode = expr.truthy(_value(state, backend, "m_9507488A", False))
    if backend.mode:
        backend.changed(state)
        from ow174.game.script.runtime import ABORT

        state.request(ABORT)  # Native virtual138/1B71120 schedules op2 at current time.
    else:
        alias = play._asset(_value(state, backend, "m_11E0A658", expr.Asset(0)))
        state.alias_handle = backend.push(state, alias)


def cleanup(state):
    """CBA9F0 resolves the component again and removes through the handle address."""
    backend = _backend(state)
    if backend is not None:
        backend.remove(state)
