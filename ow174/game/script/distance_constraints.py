"""Registration order for the native component 88 distance constraints."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    state: object
    priority: float
    sequence: int


class Registry:
    def __init__(self):
        self._entries = []
        self._sequence = 0

    @property
    def entries(self):
        return tuple(self._entries)

    def register(self, state, priority=0.0, above_equal=False):
        self._sequence += 1
        sequence = self._sequence if above_equal else -self._sequence
        entry = Entry(state, priority, sequence)
        self._entries.append(entry)
        self._entries.sort(key=lambda row: (row.priority, row.sequence), reverse=True)
        return entry

    def unregister(self, entry):
        self._entries[:] = [row for row in self._entries if row is not entry]

    def clear(self):
        self._entries.clear()
