"""104319 component86 base target registration (BC2F00/BC26F0)."""

from dataclasses import dataclass

from ow174.game.script import expr


@dataclass(frozen=True)
class Entry:
    state: object
    source: int
    instance: int
    node: int
    priority: float
    before_equal: bool


class Registry:
    def __init__(self, descriptors=()):
        self.descriptors = dict(descriptors)
        self.entries: list[Entry] = []

    def priority(self, value):
        if value is None or isinstance(value, expr.Asset) and value.guid == 0:
            return 9999.0, False
        if isinstance(value, expr.Asset):
            return self.descriptors.get(value.guid, NotImplemented)
        return NotImplemented

    def insert(self, state, priority, before_equal):
        instance = state.instance
        priority = expr.f32(priority)
        entry = Entry(state, instance.component.entity, instance.id, state.node.rid, priority, before_equal)
        index = next((i for i, other in enumerate(self.entries)
                      if priority > other.priority or before_equal and priority == other.priority),
                     len(self.entries))
        self.entries.insert(index, entry)

    def remove(self, state):
        self.entries[:] = [entry for entry in self.entries if entry.state is not state]

    def remove_component(self, component):
        self.entries[:] = [entry for entry in self.entries if entry.state.instance.component is not component]

    def clear(self):
        self.entries.clear()
