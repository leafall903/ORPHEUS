"""C778A0 query preparation and C77840 collection; actual root services are required."""

from dataclasses import dataclass, field, replace

from .target_scan import _key, _mask
from .target_spatial import _sphere, _vector


def _rows(values, width):
    if not isinstance(values, tuple) or len(values) > 32:
        raise ValueError("query rows require explicit inline storage (at most32)")
    if width == 1:
        return tuple(_mask(value) for value in values)
    if any(not isinstance(row, tuple) or len(row) != width for row in values):
        raise ValueError("query row is unavailable")
    return tuple(tuple(_mask(value) for value in row) for row in values)


@dataclass(frozen=True)
class RootQuery:
    center: tuple  # native16-byte center, including its unused fourth lane
    radius: float
    word: int
    any_bits: int
    excluded_bits: int
    flags: tuple
    first_rows: tuple
    second_rows: tuple
    third_rows: tuple

    def prepared(self, category):
        center = _vector(self.center, 4)
        radius = _sphere((*center[:3], self.radius))[3]
        if (not isinstance(self.flags, tuple) or len(self.flags) != 2
                or any(type(v) is not int or not 0 <= v < 256 for v in self.flags)):
            raise ValueError("query flags must be two explicit bytes")
        return replace(self, center=center, radius=radius, word=_mask(self.word),
                       any_bits=_mask(self.any_bits) | _key(category),
                       excluded_bits=_mask(self.excluded_bits), flags=tuple(v for v in self.flags),
                       first_rows=_rows(self.first_rows, 1), second_rows=_rows(self.second_rows, 1),
                       third_rows=_rows(self.third_rows, 2))


@dataclass
class Output:
    capacity: int
    values: list = field(default_factory=list)
    grow: object = NotImplemented  # callable(required)->new capacity, or None for known failure

    def emit(self, payload):
        payload = _mask(payload)
        if payload == 0:
            return
        if type(self.capacity) is not int or self.capacity < 0:
            raise ValueError("query output capacity is unavailable")
        required = len(self.values) + 1
        if required > self.capacity:
            if not callable(self.grow):
                raise ValueError("query output growth service is unavailable")
            capacity = self.grow(required)
            if capacity is None:
                return  # original failed append does not terminate either scan
            if type(capacity) is not int or capacity < required:
                raise ValueError("query output growth result is unavailable")
            self.capacity = capacity
        self.values.append(payload)


def aggregate(query, category, *, first, second, extra, output):
    """Clear output, copy query/OR category, visit first then second; no deduplication."""
    if not isinstance(query, RootQuery) or not isinstance(output, Output):
        raise ValueError("explicit query/output descriptors are required")
    output.values.clear()
    prepared = query.prepared(category)
    if not callable(first) or not callable(second):
        raise ValueError("both root scan services must be explicit")
    first(category, prepared, output.emit)
    second(category, prepared, output.emit, extra)
    return tuple(output.values)
