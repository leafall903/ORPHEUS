"""104319 persistent message-State registry (D9A770/D9AA50/D9AD40).

This is distinct from packet/Entry dispatch. The owner supplies the engine's
channel key, resolved priority, registration frame, filter and invalidation hook.
State bindings, native filter scopes and engine phase policy are separate work.
"""

import math
from dataclasses import dataclass, field, replace

from ow174.game.script import expr


def _u32(value, name):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f'{name} must be uint32')


def _i32(value, name):
    if type(value) is not int or not -0x80000000 <= value <= 0x7FFFFFFF:
        raise ValueError(f'{name} must be int32')


@dataclass(frozen=True)
class Key:
    entity: int
    flags: int
    message: int  # Native manager keys use the resource's low uint32.

    def __post_init__(self):
        for name in ('entity', 'flags', 'message'):
            _u32(getattr(self, name), name)


@dataclass
class Sender:
    state: object
    priority: float
    prefer_later: bool
    frame: int
    sequence: int = -1

    def __post_init__(self):
        if self.state is None:
            raise ValueError('sender needs a State identity')
        if type(self.priority) not in (int, float):
            raise ValueError('priority must be numeric')
        self.priority = expr.f32(self.priority)
        if type(self.prefer_later) is not bool:
            raise ValueError('priority ordering flag must be boolean')
        _i32(self.frame, 'frame')
        _i32(self.sequence, 'sequence')

    @property
    def order(self):
        # D9AE6B sign-extends the low int32 before OR, then compares unsigned.
        return ((self.frame & 0xFFFFFFFF) << 32) | (self.sequence & 0xFFFFFFFFFFFFFFFF)


@dataclass
class _Channel:
    senders: list[Sender] = field(default_factory=list)
    receivers: list = field(default_factory=list)


@dataclass(frozen=True)
class Selection:
    state: object | None
    matches: int | None


class Manager:
    """Explicit manager storage with shared-budget and lifecycle callbacks.

    notify marks a receiver dirty; it must not immediately execute authored code.
    Mutation during selection/notification is unsupported. retire is terminal
    cleanup and deliberately runs no notification or filter callbacks.
    """

    def __init__(self, *, notify, consume_work, validate):
        if not all(callable(callback) for callback in (notify, consume_work, validate)):
            raise ValueError('message manager needs explicit owner callbacks')
        self.notify = notify
        self.consume_work = consume_work
        self.validate = validate
        self.channels: dict[Key, _Channel] = {}
        self.frame = None
        self.next_sequence = 0
        self.retired = False
        self._busy = 0

    def _check(self):
        if self.retired:
            raise ValueError('message manager has retired')
        self.validate()
        if self.retired:
            raise ValueError('message manager retired during owner callback')

    def _work(self, operation):
        self._check()
        self.consume_work('message manager ' + operation)
        self._check()

    def _mutation(self, key):
        self._check()
        if self._busy:
            raise ValueError('message manager mutation during callback is unsupported')
        if type(key) is not Key:
            raise ValueError('message manager needs an explicit native channel key')
        self._work('registration')

    def _notify(self, channel):
        self._busy += 1
        try:
            for receiver in channel.receivers:
                self._work('notification')
                self.notify(receiver)
                self._check()
        finally:
            self._busy -= 1

    def register_sender(self, key, sender):
        """D9A770: assign/reuse the record's sequence, upsert by State identity."""
        self._mutation(key)
        if type(sender) is not Sender:
            raise ValueError('message manager needs a resolved sender record')
        record = replace(sender)  # Validate edits, copy values, retain State identity.
        if self.frame != record.frame:
            self.frame, self.next_sequence = record.frame, 0
        if record.sequence == -1:
            record.sequence = self.next_sequence
            self.next_sequence = expr.i32(self.next_sequence + 1)
        else:
            self.next_sequence = expr.i32(max(record.sequence, self.next_sequence) + 1)
        sender.sequence = record.sequence
        channel = self.channels.get(key)
        if channel is None:
            self.channels[key] = _Channel([record])
            return
        for index, previous in enumerate(channel.senders):
            self._work('sender identity')
            if previous.state is record.state:
                channel.senders[index] = record
                break
        else:
            channel.senders.append(record)
        self._notify(channel)

    def register_receiver(self, key, receiver):
        """D9B2B0 appends, including duplicate receiver identities."""
        self._mutation(key)
        if receiver is None:
            raise ValueError('receiver needs a State identity')
        self.channels.setdefault(key, _Channel()).receivers.append(receiver)

    @staticmethod
    def _swap_remove(items, index):
        last = items.pop()
        if index < len(items):
            items[index] = last

    def _prune(self, key, channel):
        if not channel.senders and not channel.receivers:
            del self.channels[key]

    def unregister_sender(self, key, state):
        """D9B440: swap the last sender into its slot and dirty live receivers."""
        self._mutation(key)
        channel = self.channels.get(key)
        if channel is None:
            return
        for index, record in enumerate(channel.senders):
            self._work('sender identity')
            if record.state is state:
                self._swap_remove(channel.senders, index)
                self._prune(key, channel)
                self._notify(channel)
                return

    def unregister_receiver(self, key, receiver):
        """D9B6C0 swaps out the first occurrence, without dirtying receivers."""
        self._mutation(key)
        channel = self.channels.get(key)
        if channel is None:
            return
        for index, previous in enumerate(channel.receivers):
            self._work('receiver identity')
            if previous is receiver:
                self._swap_remove(channel.receivers, index)
                self._prune(key, channel)
                return
        # Native code assumes a matching registration here. Refuse the invalid
        # operation instead of reproducing its out-of-bounds memory write.
        raise ValueError('receiver is not registered in this channel')

    def _rows(self, key):
        if type(key) is not Key:
            raise ValueError('message manager needs an explicit native channel key')
        keys = [key]
        if key.entity != 0 and key.entity & 0x1FFFFFFF != 0x1FFFFFFF:
            keys.append(replace(key, entity=0))
        for channel_key in keys:
            self._work('channel selection')
            channel = self.channels.get(channel_key)
            if channel is not None:
                for record in channel.senders:
                    self._work('candidate selection')
                    yield record

    @staticmethod
    def _better(candidate, current):
        if current is None:
            return True
        if current.priority > candidate.priority:
            return False
        # UCOMISS treats unordered floats as the equal branch here.
        equal = (candidate.priority == current.priority or math.isnan(candidate.priority)
                 or math.isnan(current.priority))
        if not equal:
            return True
        if not candidate.prefer_later and current.prefer_later:
            return False
        if candidate.prefer_later == current.prefer_later:
            return ((candidate.order < current.order) != current.prefer_later)
        return True

    def _accept(self, predicate, record):
        if predicate is None:
            return True
        result = predicate(replace(record))
        self._check()
        if type(result) is not bool:
            raise ValueError('message manager filter result is unknown')
        return result

    def _selection(self, predicate):
        self._check()
        if predicate is not None and not callable(predicate):
            raise ValueError('message manager filter must be resolved explicitly')

    def select(self, key, *, predicate=None, count_matches=False):
        """D9AD40: highest priority, then flag and native frame/sequence order."""
        self._selection(predicate)
        if type(count_matches) is not bool:
            raise ValueError('match-count policy must be boolean')
        current, matches = None, 0 if count_matches else None
        self._busy += 1
        try:
            for record in self._rows(key):
                better = self._better(record, current)
                if (better or count_matches) and self._accept(predicate, record):
                    if count_matches:
                        matches += 1
                    if better:
                        current = record
        finally:
            self._busy -= 1
        self._check()
        return Selection(current.state if current is not None else None, matches)

    def collect(self, key, *, predicate=None):
        """D9AA50: local then global, insertion order, without deduplication."""
        self._selection(predicate)
        found = []
        self._busy += 1
        try:
            for record in self._rows(key):
                if self._accept(predicate, record):
                    found.append(record.state)
        finally:
            self._busy -= 1
        self._check()
        return tuple(found)

    def retire(self):
        self.retired = True
        self.channels.clear()
