"""104319 BC2F00/BC26F0 priority records in an explicit component86 host."""

from dataclasses import dataclass, replace

from ow174.game.script import expr


@dataclass(frozen=True)
class Entry:
    state: object
    source: int
    instance: int
    node: int
    kind: int
    priority: float
    before_equal: bool
    selection: "Selection | None" = None
    numeric_pair: "NumericPair | None" = None
    vector: "expr.Vec3 | None" = None


@dataclass(frozen=True)
class Selection:
    slot: int
    identifier: int
    flag: bool


@dataclass(frozen=True)
class NumericPair:
    first: float
    second: float


class Registry:
    def __init__(self, descriptors=(), *, accepted_selections=NotImplemented):
        self.descriptors = dict(descriptors)
        self.accepted_selections = (NotImplemented if accepted_selections is NotImplemented
                                    else frozenset(accepted_selections))
        self.entries: list[Entry] = []
        self.selection_entries: list[Entry] = []

    def priority(self, value):
        if value is None or isinstance(value, expr.Asset) and value.guid == 0:
            return 9999.0, False
        if isinstance(value, expr.Asset):
            return self.descriptors.get(value.guid, NotImplemented)
        return NotImplemented

    def insert(self, state, priority, before_equal, payload=None):
        instance = state.instance
        priority = expr.f32(priority)
        selection = payload if isinstance(payload, Selection) else None
        numeric_pair = payload if isinstance(payload, NumericPair) else None
        entries = self.entries if selection is None else self.selection_entries
        vector = payload if isinstance(payload, expr.Vec3) else None
        kind = 3 if vector is not None else 2 if payload is None else 1
        entry = Entry(state, instance.component.entity, instance.id, state.node.rid, kind,
                      priority, before_equal, selection, numeric_pair, vector)
        index = next((i for i, other in enumerate(entries)
                      if priority > other.priority or before_equal and priority == other.priority),
                     len(entries))
        entries.insert(index, entry)

    def update_vector(self, state, priority, before_equal, vector):
        for index, entry in enumerate(self.entries):
            if entry.state is state:
                if (entry.priority, entry.before_equal) == (expr.f32(priority), before_equal):
                    self.entries[index] = replace(entry, vector=vector)
                else:
                    self.entries.pop(index)
                    self.insert(state, priority, before_equal, vector)
                return

    def remove(self, state):
        for entries in (self.entries, self.selection_entries):
            entries[:] = [entry for entry in entries if entry.state is not state]

    def remove_component(self, component):
        for entries in (self.entries, self.selection_entries):
            entries[:] = [entry for entry in entries if entry.state.instance.component is not component]

    def clear(self):
        self.entries.clear()
        self.selection_entries.clear()
