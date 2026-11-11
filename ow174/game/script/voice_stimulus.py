"""104319 shared40E05F6B callbacks, with explicit voice services and poll phase.

The +80 callback belongs to1B8C690's separate poll phase, not ordinary tick+70.
Speech resource/rules/submission/status and the global double clock are external.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, play

CACHES = ('voice_handle', 'voice_attempts', 'voice_duration', 'voice_part', 'voice_started_at',
          'voice_delay', 'voice_cooldown', 'voice_retry_at', 'voice_submitted', 'voice_failed')


class DefinitionComponent:
    """Component 78 override storage with explicit owner resource leases."""

    def __init__(self, owner: int, *, acquire, release):
        if not callable(acquire) or not callable(release):
            raise ValueError("voice definition resource services must be callable")
        self.owner, self.acquire, self.release = owner, acquire, release
        self.guid, self.handle = 0, None
        self.active = self.retired = False
        self._installing = False

    def install(self, guid: int) -> None:
        if self.retired:
            raise ValueError("voice definition component is retired")
        if self._installing:
            raise ValueError("voice definition resource update already in progress")
        self._installing = True
        try:
            self._replace(guid)
        finally:
            self._installing = False

    def _replace(self, guid: int) -> None:
        # D529E0 obtains a temporary lease before BE6830 installs the override.
        temporary = self.acquire(guid) if guid else None
        if temporary is not None:
            self.release(temporary)
        self.clear()
        if self.retired:
            raise ValueError("voice definition component retired during resource update")
        handle = self.acquire(guid) if guid else None
        if self.retired:
            if handle is not None:
                self.release(handle)
            raise ValueError("voice definition component retired during resource update")
        self.guid, self.handle, self.active = guid, handle, True

    def clear(self) -> None:
        handle, self.handle = self.handle, None
        self.active = False
        if handle is not None:
            self.release(handle)

    def retire(self) -> None:
        self.retired = True
        self.clear()


@dataclass(frozen=True)
class Stimulus:
    guid: int
    playable: bool
    mode: bool = False


@dataclass
class Manager:
    """Explicit backend: duration(handle, GUID, mode); part(state, part_number)."""

    load: object = NotImplemented
    allow: object = NotImplemented
    submit: object = NotImplemented
    duration: object = NotImplemented
    part: object = NotImplemented


class Context:
    def __init__(self, component, *, clock, lookup):
        self.component, self.clock, self.lookup = component, clock, lookup
        self.registered = []
        self.retired = False

    def register(self, state):
        _owner(state, self)
        if state not in self.registered:
            self.registered.append(state)

    def unregister(self, state):
        if state in self.registered:
            self.registered.remove(state)

    def poll(self):
        """Only the owner's explicitly selected +80 phase; no script-time tick."""
        if self.retired:
            return 0
        snapshot = tuple(self.registered)
        with self.component.operation():
            for state in snapshot:
                self.component.consume_work('voice stimulus poll')
                _owner(state, self)
                update(state)
                if tuple(self.registered) != snapshot:
                    _unsupported(state, 'voice poll registry mutation')
        return len(snapshot)

    def notify_failure(self, state):
        if self.retired or state not in self.registered:
            return False
        _owner(state, self)
        failure(state)
        return True

    def retire(self):
        self.retired = True
        self.registered.clear()


def _unsupported(state, service):
    state.instance.component.unsupported(state.instance, service, state.node.cls, state.node)


def _owner(state, context=None):
    instance = state.instance
    component = instance.component
    component.ensure_valid()
    if (instance.stopped or component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state):
        _unsupported(state, 'voice stimulus owner retired')
    if context is not None and (context.component is not component or context.retired):
        _unsupported(state, 'voice stimulus context lifecycle')


def _context(state):
    _owner(state)
    context = state.instance.component.world.voice_context(state.instance.component.entity)
    _owner(state)
    if context is None:
        return None
    if type(context) is not Context:
        _unsupported(state, 'voice stimulus context')
    _owner(state, context)
    return context


def _call(state, context, callback, service, *args):
    if not callable(callback):
        _unsupported(state, service)
    result = callback(*args)
    _owner(state, context)
    return result


def _clock(state, context):
    value = _call(state, context, context.clock, 'voice global clock')
    if type(value) not in (int, float):
        _unsupported(state, 'voice global clock')
    return float(value)


def _manager(state, context):
    value = _call(state, context, context.lookup, 'voice manager lookup')
    if value is not None and type(value) is not Manager:
        _unsupported(state, 'voice manager lookup')
    return value


def _scalar(state, field):
    cfg = state.node.field(field)
    value = play.evaluate_scalar(state.instance, cfg, initial=0.0, watcher=state)
    _owner(state)
    return expr.to_float(value, 0.0)


def _stimulus(state, context, manager):
    value = play.evaluate_scalar(state.instance, state.node.field('m_stimulus'), initial=0, watcher=state)
    _owner(state, context)
    guid = play._asset(value)
    if not guid:
        return 0, None
    result = _call(state, context, manager.load, 'voice stimulus resource', guid)
    if result is None:
        return guid, None
    if (type(result) is not Stimulus or result.guid != guid
            or type(result.playable) is not bool or type(result.mode) is not bool):
        _unsupported(state, 'voice stimulus resource')
    return guid, result


def _float(state, value, service):
    if type(value) not in (int, float):
        _unsupported(state, service)
    return expr.f32(value)


def _milliseconds(value):
    value = expr.f32(expr.f32(value)*1000.0)
    value = expr.f32(value-0.5 if value < 0 else value+0.5)
    if not math.isfinite(value) or not -2147483648 <= value < 2147483648:
        return -2147483648
    return math.trunc(value)


def begin(state):
    """D43F70: host gate, lookup, reset, global clock, delay/gap, poll registration."""
    context = _context(state)
    if context is None:
        return
    _manager(state, context)  # The native begin ignores this lookup's result.
    state.voice_handle, state.voice_attempts = 0, 0
    state.voice_duration, state.voice_part = 0.0, 1
    state.voice_started_at = _clock(state, context)
    state.voice_delay = float(_scalar(state, 'm_B0263D2D'))
    state.voice_cooldown = float(_scalar(state, 'm_E0E02BD4'))
    state.voice_retry_at = 0.0
    state.voice_submitted = state.voice_failed = False
    context.register(state)


def update(state):
    """D44090: delayed request, failure timeout, duration poll and subsequent parts."""
    context = _context(state)
    if context is None or (manager := _manager(state, context)) is None:
        return
    if state.voice_started_at is None:
        _unsupported(state, 'voice stimulus uninitialized poll')
    if not state.voice_submitted:
        if not _clock(state, context)-state.voice_started_at >= state.voice_delay:
            return
        _, stimulus = _stimulus(state, context, manager)
        allowed = False
        if stimulus is not None and stimulus.playable:
            allowed = _call(state, context, manager.allow, 'voice stimulus permission', stimulus)
            if type(allowed) is not bool:
                _unsupported(state, 'voice stimulus permission')
        if allowed:
            targets = []
            for value in play._iter_values(state.instance, state.node.field('m_targets'), watcher=state):
                entity = expr.to_entity(value) & 0xFFFFFFFF
                if expr.valid_entity(entity) and entity not in targets:
                    targets.append(entity)
            _owner(state, context)
            handle = _call(state, context, manager.submit, 'voice stimulus submission', state,
                           stimulus, tuple(targets))
            if type(handle) is not int or not 0 <= handle <= 0xFFFFFFFF:
                _unsupported(state, 'voice stimulus submission')
            state.voice_handle = handle
            if expr.valid_entity(handle):
                state.voice_submitted = True
            else:
                state.voice_failed = True
        else:
            state.voice_failed = True
    if state.voice_failed:
        if _clock(state, context)-state.voice_started_at > state.voice_delay+state.voice_cooldown:
            state.request_finish()
        return
    if state.voice_retry_at > 0:
        if not _clock(state, context) >= state.voice_retry_at:
            return
        result = _float(state, _call(state, context, manager.part, 'voice next part', state,
                                    state.voice_part+1), 'voice next part')
        if result == 0 or math.isnan(result):
            second = _float(state, _call(state, context, manager.part, 'voice next part', state,
                                        state.voice_part), 'voice next part')
            if not second > 0:
                state.request_finish()
            return
        state.voice_part = (state.voice_part+1) & 0xFF
        state.voice_retry_at = _clock(state, context)+float(result)
        state.timer_at(0, 2)
        return
    if not expr.valid_entity(state.voice_handle):
        return
    guid, stimulus = _stimulus(state, context, manager)
    if not guid:
        return
    duration = _float(state, _call(state, context, manager.duration, 'voice duration status',
                                  state.voice_handle, guid, stimulus.mode if stimulus else False),
                      'voice duration status')
    state.voice_attempts = (state.voice_attempts+1) & 0xFF
    if state.voice_attempts > 5:
        state.voice_failed = True
        return
    if duration > 0:
        total = _milliseconds(duration)+_milliseconds(state.voice_cooldown)
        state.voice_duration = expr.f32(expr.f32(total)*expr.f32(0.001))
        state.timer_at(0, 1)
        state.voice_handle = 0
        state.voice_retry_at = _clock(state, context)+float(duration)+state.voice_cooldown


def timer(state, param):
    """D446A0: param1 writes cached duration; params1/2 follow the same +160 plug."""
    if _context(state) is None:
        return
    if param == 1:
        cfg = state.node.field('m_FC2F7AAA')
        if cfg is not None:
            target = expr.dynamic(cfg)
            if target is None:
                _unsupported(state, 'voice duration output')
            if target[1]:
                state.instance.write(target, state.voice_duration)
                _owner(state)
    if param in (1, 2):
        state.instance.follow(state.node, 'm_onFinishedPlug', state)


def failure(state):
    """9FC440/+1A8: explicit failure notification follows +168, without an end."""
    _owner(state)
    state.instance.follow(state.node, 'm_F96FC964', state)


def detach(state):
    context = state.instance.component.world.voice_context(state.instance.component.entity)
    if type(context) is Context and context.component is state.instance.component:
        context.unregister(state)
