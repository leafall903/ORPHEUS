"""104319 D4C3E0 persistent sending State against explicit engine services."""

from contextlib import contextmanager
from dataclasses import dataclass

from ow174.game.script import expr, message_manager, play


@dataclass(frozen=True)
class Priority:
    value: float
    prefer_later: bool


@dataclass(frozen=True)
class Policy:
    mode: bool  # Instance virtual148.
    instance_flag3: bool
    state_mode: bool  # State+C0; not inferred from authored clientOnly.
    local: bool | None = None  # State virtual1C8, consulted only in mode.
    world_a: bool | None = None  # State virtual1D0.
    world_b: bool | None = None  # State virtual1D8.


class Host:
    """One explicit engine manager, with each caller's own VM work budget.

    notify must queue native receiver dirtiness, not execute authored callbacks.
    This host does not choose a native frame, role, phase or global/TLS manager.
    """

    def __init__(self, *, notify):
        if not callable(notify):
            raise ValueError('message-State host needs an explicit notification service')
        self.contexts = set()
        self.current = []
        self.manager = message_manager.Manager(notify=notify, consume_work=self._work,
                                               validate=self._check)

    def _check(self):
        if not self.current:
            raise ValueError('message-State manager operation needs an owner context')
        self.current[-1].check()

    def _work(self, operation):
        self._check()
        self.current[-1].component.consume_work(operation)

    @contextmanager
    def use(self, context):
        context.check()
        self.current.append(context)
        try:
            yield self.manager
        finally:
            self.current.pop()

    def retire(self):
        self.manager.retire()
        for context in tuple(self.contexts):
            context.retire()


class Context:
    def __init__(self, component, host, *, policy, frame, priority, timer_notify=None,
                 receiver_policy=None, receiver_generation=None, receiver_filter=None,
                 permissive_nodes=None):
        if type(host) is not Host or not all(callable(item) for item in (policy, frame, priority)):
            raise ValueError('message-State services must be declared explicitly')
        if permissive_nodes is not None and not callable(permissive_nodes):
            raise ValueError('message-State node selection must be callable')
        self.component, self.host = component, host
        self.policy, self.frame, self.priority = policy, frame, priority
        self.timer_notify = timer_notify
        self.receiver_policy = receiver_policy
        self.receiver_generation = receiver_generation
        self.receiver_filter = receiver_filter
        self.permissive_nodes = permissive_nodes
        self.retired = False
        self.states = set()
        host.contexts.add(self)

    def check(self):
        component = self.component
        component.ensure_valid()
        if (self.retired or self.host.manager.retired
                or component.world.message_state_context(component.entity, component=component) is not self):
            raise ValueError('message-State owner context has retired or been replaced')

    def retire(self):
        self.retired = True
        for state in tuple(self.states):
            self.host.manager.discard_state(state)
            state.message_context_retired()
        self.states.clear()
        self.host.contexts.discard(self)


def persistent(state, *, starting=False):
    """Keep the selected transport for the lifetime of the active state."""
    component = state.instance.component
    if component.strict or state.message_context is not None:
        return True
    if not starting:
        return False
    context = component.world.message_state_context(component.entity, component=component)
    return (type(context) is Context and not context.retired and context.permissive_nodes is not None
            and context.permissive_nodes(state) is True)


def _unsupported(state, feature):
    state.instance.component.unsupported(state.instance, feature, state.node.cls, state.node)


def _byte(state, value, default=0):
    if value is None:
        value = default
    if type(value) not in (int, bool) or not 0 <= value <= 255:
        _unsupported(state, 'message-State byte field')
    return bool(value)


def _context(state, *, cleanup=False):
    instance, component = state.instance, state.instance.component
    component.ensure_valid()
    context = component.world.message_state_context(component.entity, component=component)
    if (type(context) is not Context or context.component is not component
            or instance.stopped or (instance.teardown and not cleanup)
            or component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state
            or state.message_context not in (None, context)):
        _unsupported(state, 'message-State context or lifecycle')
    try:
        context.check()
    except ValueError:
        _unsupported(state, 'message-State context lifecycle')
    if state.message_context is None:
        state.message_context = context
        context.states.add(state)
    return context


def _call(state, context, callback, *args):
    if not callable(callback):
        _unsupported(state, 'message-State engine callback')
    result = callback(*args)
    _context(state)
    return result


def created(state):
    """D4DB20 sets notification flags from authored policy, without evaluation."""
    target = state.node.field('m_target')
    notified = False
    if target is not None:
        if _byte(state, state.node.field('m_67FD6A60')):
            notified = True
        elif isinstance(target, dict) and target.get('$') == 'STU_1D719351':
            notified = (_byte(state, target.get('m_1199BFE0'))
                        or _byte(state, target.get('m_111E4A0E')))
        else:
            enum = state.node.field('m_AED90719')
            enum = 0 if enum is None else enum
            if type(enum) is not int or not 0 <= enum <= 5:
                _unsupported(state, 'message-State node policy')
            notified = enum > 1
    state.message_notified = notified


def begin(state):
    state.message_frame = state.message_sequence = -1
    _context(state)
    state.ticks()


def _remove(state, context):
    with context.host.use(context) as manager:
        for key in state.message_keys:
            manager.unregister_sender(key, state)
            _context(state, cleanup=True)
        state.message_keys.clear()


def refresh(state):
    """D4C3E0: unregister, resolve message/priority/frame, then register channels."""
    component = state.instance.component
    with component.operation():
        context = _context(state)
        try:
            _remove(state, context)
            state.unwatch()
            cfg = state.node.field('m_gameMessage')
            value, success = play.evaluate_scalar_result(state.instance, cfg, watcher=state)
            _context(state)
            message = (play._asset(value) if success else 0) & 0xFFFFFFFF
            if not message:
                return
            value, success = play.evaluate_scalar_result(
                state.instance, state.node.field('m_priority'), watcher=state)
            _context(state)
            resource = play._asset(value) if success else 0
            priority = _call(state, context, context.priority, state, resource)
            if priority is None:
                priority = Priority(0.0, False)
            if (type(priority) is not Priority or type(priority.value) not in (int, float)
                    or type(priority.prefer_later) is not bool):
                _unsupported(state, 'message-State priority resource')
            frame = _call(state, context, context.frame, state)
            if type(frame) is not int or not -0x80000000 <= frame <= 0x7FFFFFFF:
                _unsupported(state, 'message-State native frame')
            record = message_manager.Sender(state, priority.value, priority.prefer_later,
                state.message_frame if state.message_frame != -1 else frame,
                state.message_sequence if state.message_frame != -1 else -1)
            policy = _call(state, context, context.policy, state)
            if type(policy) is not Policy:
                _unsupported(state, 'message-State engine policy')

            def flag(name):
                value = getattr(policy, name)
                if type(value) is not bool:
                    _unsupported(state, 'message-State engine policy ' + name)
                return value

            mode = flag('mode')
            local = mode and flag('local')
            primary = (flag('instance_flag3') or not mode) and not flag('state_mode')
            secondary = mode and _byte(state, state.node.field('m_67FD6A60'))

            def register(entity, flags):
                key = message_manager.Key(entity, flags, message)
                state.message_keys.append(key)
                with context.host.use(context) as manager:
                    manager.register_sender(key, record)
                _context(state)

            target = state.node.field('m_target')
            if not isinstance(target, dict):
                _unsupported(state, 'message-State target policy')
            cls = target.get('$')
            if cls == 'STU_1D719351':
                if _byte(state, target.get('m_6A8DB059'), 1):
                    if primary:
                        register(0, 4 if local else 1)
                    if secondary:
                        register(0, 8 if local else 2)
                if mode:
                    if _byte(state, target.get('m_1199BFE0')):
                        register(0, 16)
                    if _byte(state, target.get('m_111E4A0E')):
                        register(0, 32)
            elif cls == 'STU_9B213168':
                primary_flag, secondary_flag = 1, 2
                if mode:
                    if local:
                        primary_flag, secondary_flag = 4, 8
                    elif flag('world_a'):
                        primary_flag, primary = 16, True
                    elif flag('world_b'):
                        primary_flag, primary = 32, True
                for value in play._iter_values(state.instance, target.get('m_targets'), watcher=state):
                    _context(state)
                    entity = expr.to_entity(value) & 0xFFFFFFFF
                    if not expr.valid_entity(entity):
                        continue
                    if primary:
                        register(entity, primary_flag)
                    if secondary:
                        register(entity, secondary_flag)
                _context(state)
            elif cls != 'STU_F121C8C5':
                _unsupported(state, 'message-State target type')
            state.message_frame, state.message_sequence = record.frame, record.sequence
            # 1B72510 forwards to EntityInstance virtualE0 (262E740+E0),
            # the known null callback1CE5640. It does not check transitions.
        except ValueError:
            _unsupported(state, 'message-State manager or owner lifecycle')


def cleanup(state):
    previous = state.message_context
    if (previous is None or previous.retired or previous.host.manager.retired
            or state.instance.component.world.message_state_context(
                state.instance.component.entity, component=state.instance.component) is not previous):
        abandon(state)
        return
    context = _context(state, cleanup=True)
    try:
        _remove(state, context)
    except ValueError:
        _unsupported(state, 'message-State cleanup lifecycle')


def abandon(state):
    context = state.message_context
    if context is not None:
        context.host.manager.discard_state(state)
        context.states.discard(state)
        state.message_context = None
    state.message_keys.clear()


def timer(state, param):
    if param != 1 or state.message_notified:
        return
    context = _context(state)
    state.message_notified = True
    if _call(state, context, context.timer_notify, state) is NotImplemented:
        _unsupported(state, 'message-State timer notification')
