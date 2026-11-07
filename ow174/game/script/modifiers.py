"""104319's numeric modifier records, independent of named gameplay properties.

Native C2EC10 maintains the array; C2FAA0 selects insertion order. C32FB0
applies records in [start, end) through the six operations at C32ED0. The
world owner supplies source/read frames and property base values explicitly.
"""

import math
from dataclasses import dataclass, replace

from ow174.game.script.expr import f32

UINT32_MAX = (1 << 32) - 1
PRIORITY_EPSILON = f32(0.004)


def _uint(value, bits, name):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f"{name} must be uint{bits}")
    return value


def _number(value):
    if type(value) not in (int, float):
        raise ValueError("modifier values must be numeric")
    return f32(float(value))


def _minimum(a, b):
    # SSE MINSS/MAXSS return the second operand on unordered/equal inputs.
    return b if math.isnan(a) or math.isnan(b) or a >= b else a


def _maximum(a, b):
    return b if math.isnan(a) or math.isnan(b) or a <= b else a


def apply(operation: int, base: float, value: float, upper: float = 0.0) -> float:
    """C32ED0, preserving f32 rounding, signed zero and SSE unordered rules."""
    if type(operation) is not int or not 0 <= operation <= 5:
        raise ValueError("unknown numeric modifier operation")
    base, value, upper = map(_number, (base, value, upper))
    if operation == 0:
        return f32(value + base)
    if operation == 1:
        return f32(value * base)
    if operation == 2:
        return value
    if operation == 3:
        return _minimum(base, value)
    if operation == 4:
        return _maximum(base, value)
    if operation == 5:
        return _maximum(_minimum(base, _maximum(value, upper)), value)
    raise ValueError("unknown numeric modifier operation")


@dataclass(frozen=True)
class Record:
    kind: int
    key: int
    start: int
    field: int
    operation: int
    value: float
    priority: float = 0.0
    upper: float = 0.0
    end: int = UINT32_MAX

    def __post_init__(self):
        for name in ("kind", "start", "field", "end"):
            _uint(getattr(self, name), 32, name)
        _uint(self.key, 64, "key")
        if type(self.operation) is not int or not 0 <= self.operation <= 5:
            raise ValueError("unknown numeric modifier operation")
        for name in ("value", "priority", "upper"):
            object.__setattr__(self, name, _number(getattr(self, name)))


def _less(a: Record, b: Record) -> bool:
    difference = abs(f32(a.priority - b.priority))
    if difference > PRIORITY_EPSILON:
        return a.priority < b.priority
    return (a.start, a.key) < (b.start, b.key)


def insertion_index(records: list[Record], record: Record) -> int:
    """The native search, including its nontransitive epsilon comparison."""
    if not records:
        return 0
    low, high = 0, len(records) - 1
    while low < high:
        middle = (low + high) // 2
        if _less(records[middle], record):
            low = middle + 1
        else:
            high = middle
    return low + 1 if _less(records[low], record) else low


class NumericModifiers:
    """The numeric subarray of a known component 31, with an explicit clock.

    ready mirrors the native definition pointer gate on mutations. A missing
    component is represented separately by the world's None result. This store
    does not assign property names, supply base values or move a game entity.
    """

    def __init__(self, source_frame: int = 0, *, ready: bool = True):
        self.source_frame = _uint(source_frame, 32, "source frame")
        if type(ready) is not bool:
            raise ValueError("modifier readiness must be bool")
        self.ready = ready
        self.records: list[Record] = []

    @property
    def source_frame(self) -> int:
        return self._source_frame

    @source_frame.setter
    def source_frame(self, value: int) -> None:
        self._source_frame = _uint(value, 32, "source frame")

    def _find(self, kind, key):
        _uint(kind, 32, "kind")
        _uint(key, 64, "key")
        return next((index for index in reversed(range(len(self.records)))
                     if (self.records[index].kind, self.records[index].key) == (kind, key)), None)

    def upsert(self, record: Record) -> None:
        if type(record) is not Record:
            raise ValueError("expected a numeric modifier record")
        if not self.ready:
            return
        record = replace(record, end=UINT32_MAX)
        index = self._find(record.kind, record.key)
        if index is not None:
            if self.records[index].start == record.start:
                # Native updates in place, even when priority changes.
                self.records[index] = record
                return
            self.records.pop(index)
        self.records.insert(insertion_index(self.records, record), record)

    def retire(self, kind: int, key: int, end: int) -> None:
        end = _uint(end, 32, "end")
        index = self._find(kind, key)
        if self.ready and index is not None:
            self.records[index] = replace(self.records[index], end=end)

    def erase(self, kind: int, key: int) -> None:
        index = self._find(kind, key)
        if self.ready and index is not None:
            self.records.pop(index)

    def resolve(self, field: int, base: float, *, frame: int) -> tuple[bool, float]:
        _uint(field, 32, "field")
        _uint(frame, 32, "read frame")
        value, found = _number(base), False
        for record in self.records:
            if record.field == field and record.start <= frame < record.end:
                value = apply(record.operation, value, record.value, record.upper)
                found = True
        return found, value


def state_key(instance: int, state: int, counter: int, row: int, offset: int = 0) -> int:
    """D3C4DB: shifted native state identity plus the full row index."""
    _uint(instance, 16, "instance")
    _uint(state, 32, "state")
    _uint(counter, 16, "counter")
    _uint(row, 32, "row")
    _uint(offset, 64, "offset")
    signed_instance = instance if instance < 0x8000 else instance - 0x10000
    signed_state = state if state < 0x80000000 else state - 0x100000000
    base = (((signed_instance << 16) | signed_state) << 16) | counter
    return (((base << 16) | (offset & 0xFFFF)) + row) & ((1 << 64) - 1)
